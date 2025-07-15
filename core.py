# core.py
from collections import defaultdict
import logging

from dataSelection import HighestVersionSelectionStrategy
logging.basicConfig(level=logging.INFO,format="[%(levelname)s] %(message)s")
logging.getLogger(__name__)
logger = logging.getLogger(__name__)

from typing import Any, Dict, List, Tuple, Type
import networkx as nx
import simpy
import random
import itertools
import copy

from SimulationLogger import CSVLogger
from Topology import StorageFullError, Topology
from application import *
from servicesManager import ServicesManager, DictDeployment
from dataManager import DataManager, DefaultDataManager
from serviceSelection import RandomSelectionStrategy, SelectionStrategy, ShortestPathSelectionStrategy  


class Core:
    def __init__(
        self,
        topology: Topology,
        service_selection : SelectionStrategy,
        data_selection : SelectionStrategy
    ):
        
        self.topology      = topology
        self.env           = topology.env
        self.service_selection   = service_selection
        self.data_selection = data_selection
        self.data_manager  : DataManager = None
        self.apps          : Dict[str, Application] = {}  
        self.placement     : Dict[Tuple[str, str], List[Any]] = {}  
        self.link_busy     : Dict[Tuple[Any, Any], float] = {}  
        self._data_waiters : Dict[Tuple[Any,str], List[simpy.Event]] = {}  
        self._msg_id_counter = itertools.count(1)
        self._path_cache : Dict[Tuple[Any,Any], List[Any]]   = {}  
        self._service_procs : Dict[Tuple[str,str,Any], List[simpy.Process]] = {}
        self.data_locations: Dict[str, Dict[str,int]] = defaultdict(dict)
        self.csv_logger = CSVLogger(filepath="result")
        # initialize link busy times
        for u, v in self.topology.get_edges():
            self.link_busy[(u, v)] =  0.0
            self.link_busy[(v, u)] =  0.0


   

    def _get_path(self, src: Any, dst: Any) -> List[Any]:
        key = (src, dst)
        if key not in self._path_cache:
            # compute once
            self._path_cache[key] = nx.shortest_path(
                self.topology.G, src, dst, weight='cost')
        return self._path_cache[key]

    def register_application(
        self,
        app: Application,
        deployer :  ServicesManager
    ):
        """Register a new Application under its `app.name`."""
        if app.name in self.apps:
            raise ValueError(f"App {app.name!r} already registered")
        app.deployer = deployer 
        self.apps[app.name] = app
        deployer.deploy_services(app=app,core=core)
        
        logger.info(f"Registered application '{app.name}'")
        


    # def place_service(
    #     self,
    #     app_name:     str,
    #     service_name: str,
    #     nodes:        List[Any]
    # ):
    #     """
    #     Record where to deploy a given service of a given app.
    #     """
    #     if app_name not in self.apps:
    #         raise KeyError(f"No such app {app_name!r}")
    #     if service_name not in self.apps[app_name].services:
    #         raise KeyError(f"No such service {service_name!r} in app {app_name!r}")
    #     self.placement[(app_name, service_name)] = nodes
        




    def send_message(self, msg: Message):
        """
        Send either an application‐level message (msg.link != None)
        via the configured SelectionStrategy, or a raw data request/response
        (msg.link is None) using its src_node/dst_node fields.
        Logs every send (and any drops) to CSV.
        """
        # 1) stamp a unique ID
        msg.id = next(self._msg_id_counter)

        # 2) decide which kind of message this is
        if msg.link is None:
            # raw data fetch or response
            if msg.src_node is None or msg.dst_node is None:
                raise RuntimeError("Raw messages must set src_node & dst_node")
            dst_node = msg.dst_node
            
        else:
            # application‐level message
            key = (msg.link.app, msg.link.dst_module)
            candidates = self.placement.get(key)
            if not candidates:
                # 2a) no placement → drop
                logger.warning(f"No placement for {key}, dropping msg#{msg.id}")
                self.csv_logger.log({
                    "timestamp":   f"{self.env.now:.5f}",
                    "event_type":  "DROP",
                    "msg_type":    msg.message_type.name,
                    "msg/data_name":    msg.link.name,
                    "msg_id":      msg.id,
                    "src_service": msg.link.src_module,
                    "dst_service": msg.link.dst_module,
                    "src_node":    msg.src_node,
                    "dst_node":    None,
                    "application": msg.link.app,
                })
                return
            # 2b) pick destination via strategy
            dst_node = self.service_selection.select(
                candidates=candidates,
                src_node=msg.src_node,
                topology=self.topology.G
            )
            msg.dst_node = dst_node
            

        # 3) build path metadata
        msg.path = self._get_path(msg.src_node, dst_node)
        msg.next_hop_idx = 1
        msg.time_cr = self.env.now

        # 4) log to CSV
        self.csv_logger.log({
            "timestamp":   f"{self.env.now:.5f}",
            "event_type":  "SEND",
            "msg_type":    msg.message_type.name,
            "msg/data_name":    (msg.link.name if msg.link else msg.data_name),
            "msg_id":      msg.id,
            "src_service": (msg.link.src_module if msg.link else ""),
            "dst_service": (msg.link.dst_module if msg.link else ""),
            "src_node":    msg.src_node,
            "dst_node":    msg.dst_node,
            "application": (msg.link.app         if msg.link else ""),
        })

        # 5) console‐log
        logger.info(
            f"{msg.time_cr:8.5f}s  SEND  "
            f"{msg.message_type.name:<8}  "
            f"msg#{msg.id}  "
            f"{(msg.link.src_module if msg.link else "")}@{msg.src_node} → "
            f"{(msg.link.dst_module if msg.link else msg.data_name)}@{dst_node}"
        )

        # 6) hand off to the network layer
        self.env.process(self._network_process(msg))



    def _network_process(self, msg: Message):
        """
        Walk the message link by link, applying:
          - serialization delay = size_bytes / (bw*1e6)
          - propagation delay  = edge['PrD']
        """
        for u, v in zip(msg.path[:-1], msg.path[1:]):
            edge = self.topology.get_edge(u, v)
            bw   = edge.get('BW', 1)    # Mbps
            prd  = edge.get('PrD', 0)   # seconds
            ser  = msg.size * 8 / (bw * 1e6)
            latency = ser + prd
            #yield self.env.timeout(ser + prd)
            now       = self.env.now
            busy_until= self.link_busy[(u, v)]
            wait      = max(0, busy_until - now)

            # reserve the link
            self.link_busy[(u, v)] = now + wait + latency

            # actually incur wait + transmission
            yield self.env.timeout(wait + latency)

        # arrived
        msg.time_rec = self.env.now
        logger.info(
            f"{msg.time_rec:8.5f}s  RECV  "
            f"{msg.message_type.name:<8}  "
            f"msg#{msg.id}  "
            f"{(msg.link.src_module if msg.link else "")}@{msg.src_node} → "
            f"{(msg.link.dst_module if msg.link else msg.data_name)}@{msg.dst_node}"
        )

        self.csv_logger.log({
        "timestamp":   f"{now:.5f}",
        "event_type":  "RECV",
        "msg_type":    msg.message_type.name,
        "msg/data_name":    (msg.link.name if msg.link else msg.data_name),
        "msg_id":      msg.id,
        "src_service": (msg.link.src_module if msg.link else ""),
        "dst_service": (msg.link.dst_module if msg.link else ""),
        "src_node":    msg.src_node,
        "dst_node":    msg.dst_node,
        "application": (msg.link.app         if msg.link else ""),
    })
        self._deliver(msg)

 

    def _deliver(self, msg: Message):
        
        mt = msg.message_type
        
        # 0) replication copies
        if mt is MessageType.STORE:
            node = self.topology.get_node(msg.dst_node)
            try:
                node.store_data(msg.data)
                # if self.data_locations[msg.data_name][msg.dst_node] < msg.data.version :
                self.data_locations[msg.data_name][msg.dst_node] = msg.data.version
                logger.info(
                    f"{self.env.now:8.5f}s  DATA_STORED  "
                    f"@{node.name}  "
                    )
            except StorageFullError as e:
                 logger.error(f"STORE_FAIL @{node.name}: {e}")
            return
        
        # 1) cons‐resp (wake waiters)
        if mt is MessageType.CONS_RESP:
            key = (msg.dst_node, msg.data_name)
            for ev in self._data_waiters.pop(key, []):
                ev.succeed(msg.data)
            logger.debug(f"Served data‐response for '{msg.data_name}' to node {msg.dst_node}")
            return
        
        # 2) cons‐req (serve from local)
        if mt is MessageType.CONS_REQ:
            stored = self._fetch_from_local_storage(msg.data_name, msg.dst_node)
            resp = Message(
                src_node     = msg.dst_node,
                dst_node     = msg.src_node,
                data_name    = msg.data_name,
                data         = stored,
                size         = stored.size,
                instructions = 0,
                message_type = MessageType.CONS_RESP
            )
            
            self.send_message(resp)
            return
        
        # 3) application‐level traffic
        
        app = self.apps[msg.link.app]
        svc = app.services[msg.link.dst_module]
        if isinstance(svc, ProcessingServices):
            # if msg.data is not None :
                # self.place_data(msg.data, msg.dst_node)
            proc =  self.env.process(
                        svc.process(msg, core=self, env=self.env, node_id=msg.dst_node)
                    )
            key = (msg.link.app, msg.link.dst_module, msg.dst_node)
            self._service_procs.setdefault(key, []).append(proc)
            self.env.process(self._watch_and_prune(proc, key))

    



    def _fetch_from_local_storage(self, data_name: str, node_id: Any) -> Data:
        """
        Look up the latest Data object named `data_name` on `node_id`,
        deep-copy it, and return it. Raise if not found.
        """
        # grab your Node instance
        node_obj = self.topology.get_node(node_id)

        # assume Node.get_data(name) returns the latest Data or None
        data = node_obj.get_data(data_name)
        if data is None:
            raise RuntimeError(f"Node {node_id!r} has no stored data '{data_name}'")

        # return a fresh copy so in-place tweaks don’t affect the stored replica
        data.last_consult =self.env.now
        return copy.deepcopy(data)
    

    def _place_data(self,data,src_node ) :
        self.data_manager.place_data(core=self, data=data,src_node=src_node)


    def wait_for_data(self, node_id: Any, data_name: str) -> simpy.Event:
        """
        Return an Event that will fire once `data_name` arrives at `node_id`.
        Services yield this to pause until that Data object is delivered.
        """
        ev = self.env.event()
        key = (node_id, data_name)
        self._data_waiters.setdefault(key, []).append(ev)
        logger.debug(f"Node {node_id} waiting for data '{data_name}'")
        return ev


    def _select_data(self,service, data_name, src_node):
        node_obj = core.topology.get_node(src_node)
        if node_obj.get_data(data_name) is None:
            logger.info(
                f"{self.env.now:8.5f}s  WAIT  "
                f"{service}@{src_node} needs '{data_name}'"
            )

            locations = self.data_locations.get(data_name, {})
            
            storage_node= self.data_selection.select(candidates=locations, src_node=src_node, topology=self.topology.G)
            req = Message(
                        src_node     = src_node,
                        dst_node     = storage_node,
                        data_name    = data_name,
                        message_type = MessageType.CONS_REQ
                    )
            self.send_message(req)
            # register an Event that will fire when data arrives
            ev = core.wait_for_data(src_node, data_name)
            return ev

    def abort_service_procs(self, app_name: str, svc_name: str, node_id: Any):
        """
        Interrupts and removes all in‐flight Process objects for that service
        running on the given node.
        """
        key = (app_name, svc_name, node_id)
        for proc in self._service_procs.pop(key, []):
            proc.interrupt()

    def abort_node_procs(self, node_id: Any):
        """
        Interrupts *all* Processes running on the given node_id,
        regardless of application or service.
        """
        # collect matching keys so we can pop safely
        for key in list(self._service_procs):
            if key[2] == node_id:
                for proc in self._service_procs.pop(key):
                    proc.interrupt()


    def _watch_and_prune(self, proc: simpy.Process, key: Tuple[str,str,Any]):
        """
        Wait until `proc` finishes, then remove it from self._service_procs[key].
        """
        try:
            yield proc
        except simpy.Interrupt:
            # expected when we abort in-flight work
            pass
        except Exception as e:
            logger.warning(f"Service-proc {key} terminated with exception: {e}")


        procs = self._service_procs.get(key)
        if not procs:
            return
        try:
            procs.remove(proc)
        except ValueError:
            # already pruned (race), ignore
            return
        if not procs:
            # clean up the empty list
            del self._service_procs[key]
    
    def run(self,until):
        try:
            self.env.run(until=until)
        finally:
            # close the CSV logger so data is written out
            self.csv_logger.close()






# ============================================
#                 EXAMPLE
# ============================================





# 1) Build the SimPy environment and topology

topo = Topology()
topo.load_topology("topology.json")

selection = ShortestPathSelectionStrategy()
data_selection = HighestVersionSelectionStrategy()
# 2) Create the Core and register your application
core = Core(topology=topo, service_selection=selection, data_selection=data_selection)
app  = Application(name="MyApp")

# 3) Define services
temp_svc = GenerationService(
    name         = "TempSensor",
    distribution = DeterministicDistribution(interval = 10),
    data_fn      = DataGenerator(name="temperature", size=10, content=lambda: random.randint(20,30)),
    place_data=True
)
filter_svc = ProcessingServices(name="FilterService" )
logger_svc = ProcessingServices(name="LoggerService",external_data=["temperature"])

# 4) Add services to the app
app.add_service(temp_svc)
app.add_service(filter_svc)
app.add_service(logger_svc)

# 5) Define the logical ServiceLinks
link1 = ServiceLink(
    name         = "ReadTemp",
    app          = app.name,
    src_module   = temp_svc.name,
    dst_module   = filter_svc.name,
    instructions = 100,
    size         = 0,
    with_data=    True
)
link2 = ServiceLink(
    name         = "LogTemp",
    app          = app.name,
    src_module   = filter_svc.name,
    dst_module   = logger_svc.name,
    instructions = 1000,
    size         =   100,
    with_data= False
)
app.add_link(link1)
app.add_link(link2)

# 6) Create a ServicesManager specifically for "MyApp"
placement = {"TempSensor" : ["s1"],
                     "FilterService": ["fog3"],
                     "LoggerService": ["cloud1","cloud2"]}

deployer = DictDeployment(placement =placement)

data_manager = DefaultDataManager()
dataplacement = {"temperature" : ["fog2", "fog1"]}
data_manager.set_placement(placement=dataplacement)
core.data_manager = data_manager
core.register_application(app=app, deployer=deployer)

# def schedule_undeploy(env : simpy.Environment, deployer : ServicesManager, app, core):
#     yield env.timeout(20.036)
#     plan = {"LoggerService": ["cloud2"]}
#     deployer.delete_services(app, core, plan)
#     print(f"[{env.now}] Undeployed LoggerService from cloud2")

# core.env.process(schedule_undeploy(core.env, deployer, app, core))
def remove_location(env: simpy.Environment, data_manager: DefaultDataManager):
    yield env.timeout(30)
    data_manager.set_placement(placement={"temperature" : ["cloud1"]})

core.env.process(remove_location(env=core.env, data_manager=data_manager))    
# 8) Run the simulation
core.run(until=100)
print(core.data_locations)



