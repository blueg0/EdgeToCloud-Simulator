# core.py
from collections import defaultdict
import logging
import csv

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
from dataSelection import DataSelection, HighestVersion
from serviceSelection import RandomSelectionStrategy, ServiceSelection, ShortestPathSelectionStrategy  


class Core:
    def __init__(
        self,
        topology: Topology,
        service_selection : ServiceSelection,
        data_selection : DataSelection,
        data_manager : DataManager = None
    ):
        
        self.topology      = topology
        self.env           = topology.env
        self.service_selection   = service_selection
        self.data_selection = data_selection
        self.data_manager  = data_manager
        self.apps          : Dict[str, Application] = {}  
        self.services_placement     : Dict[Tuple[str, str], List[Any]] = {}  
        self.link_busy     : Dict[Tuple[Any, Any], float] = {}  
        self._data_waiters : Dict[Tuple[Any,str], List[simpy.Event]] = {}  
        self._msg_id_counter = itertools.count(1)
        self._path_cache : Dict[Tuple[Any,Any], List[Any]]   = {}  
        self._pruned_paths: Dict[Any, List[Tuple[src,dst]]] = defaultdict(list)
        self._service_procs : Dict[Tuple[str,str,Any], List[simpy.Process]] = {}
        self.data_locations: Dict[str, Dict[str,int]] = defaultdict(dict)
        self.csv_logger = CSVLogger(filepath="result")
        self._failure_file = open("failures.csv", "w", newline="")
        self._failure_writer = csv.DictWriter(
             self._failure_file,
             fieldnames=["timestamp","event","node_id"]
             )
        self._failure_writer.writeheader()


        self._failed_node_services: Dict[Any, List[Tuple[str,str]]] = defaultdict(list)
        self._failed_node_data:     Dict[Any, List[str]]         = defaultdict(list)


        self._saved_node_attrs = {
            nid: dict(attrs) 
            for nid, attrs in self.topology.G.nodes(data=True)
        }

        self._saved_edges = {
            nid: [(nbr, dict(self.topology.G.edges[nid,nbr]))
                  for nbr in self.topology.G.neighbors(nid)]
            for nid in self.topology.G.nodes()
        }

 


        # 2) spawn a lifecycle process *per node* that has non-zero rates
        for node_id in list(self.topology.G.nodes()):   
            node = self.topology.get_node(node_id)
            if node.failure_rate > 0 and node.repair_rate > 0:
                self.env.process(self._node_lifecycle(node_id))
   


    def _node_lifecycle(self, node_id: Any):
        """
        Loop forever: wait for a failure, then for a repair.
        """
        node = self.topology.get_node(node_id)
        λf, λr = node.failure_rate, node.repair_rate
        while True:
            # time to failure
            ttf = random.expovariate(λf)
            yield self.env.timeout(ttf)
            self._fail_node(node_id)

            # time to repair
            ttr = random.expovariate(λr)
            yield self.env.timeout(ttr)
            self._repair_node(node_id)


    def _fail_node(self, node_id):
        # 1) pull node out of the graph
        self.topology.G.remove_node(node_id)

        # 2) record & remove it from service placement
        for key, nodes in self.services_placement.items():
            if node_id in nodes:
                self._failed_node_services[node_id].append(key)
                nodes.remove(node_id)

        # 3) record & remove it from data placement
        if isinstance(self.data_manager, DefaultDataManager):
            for data_name, nodes in self.data_manager.placement.items():
                if node_id in nodes:
                    self._failed_node_data[node_id].append(data_name)
                    nodes.remove(node_id)

        # 4) abort in-flight procs
        self.abort_node_procs(node_id)

        pruned = []
        for key, path in list(self._path_cache.items()):
            if node_id in path:
                pruned.append(key)
                del self._path_cache[key]
        self._pruned_paths[node_id] = pruned

        logger.info(f"{self.env.now:8.5f}s  NODE_DOWN  {node_id}")
        self._failure_writer.writerow({
            "timestamp": f"{self.env.now:.5f}",
            "event":     "NODE_DOWN",
            "node_id":   node_id
        })
        self._failure_file.flush()
    
    def _repair_node(self, node_id):
        # 1) restore the node & its original edges
        attrs = self._saved_node_attrs[node_id]
        self.topology.G.add_node(node_id, **attrs)
        for nbr, eattrs in self._saved_edges[node_id]:
            if nbr in self.topology.G:
                self.topology.G.add_edge(node_id, nbr, **eattrs)

        # 2) put back exactly the services that belonged here,
        for app_name, svc_name in self._failed_node_services.pop(node_id, []):
            # restore routing placement
            key = (app_name, svc_name)
            self.services_placement[key].append(node_id)

            # if it’s a periodic generator, restart its loop
            svc = self.apps[app_name].services[svc_name]
            if isinstance(svc, GenerationService):
                proc = self.env.process(
                    svc.generate(core=self, env=self.env, node_id=node_id)
                )
                # keep it tracked so it can be aborted on a subsequent failure
                self._service_procs.setdefault((app_name, svc_name, node_id), []).append(proc)

        # 3) put back exactly the data placements
        if isinstance(self.data_manager, DefaultDataManager):
            for data_name in self._failed_node_data.pop(node_id, []):
                self.data_manager.placement[data_name].append(node_id)

        for key in self._pruned_paths.pop(node_id, []):
            self._path_cache.pop(key, None)

        logger.info(f"{self.env.now:8.5f}s  NODE_UP    {node_id}")
        self._failure_writer.writerow({
            "timestamp": f"{self.env.now:.5f}",
            "event":     "NODE_UP",
            "node_id":   node_id
        })
        self._failure_file.flush()






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
        deployer.deploy_services(app=app,core=self)
        
        logger.info(f"Registered application '{app.name}'")
        


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
            candidates = self.services_placement.get(key)
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
        Walk the message link by link; if an edge (u,v) disappears,
        recompute the remainder of the path from u to dst on the live graph.
        """
        dst = msg.dst_node

        # Keep going until we've reached the final hop
        while msg.next_hop_idx < len(msg.path):
            u = msg.path[msg.next_hop_idx - 1]
            v = msg.path[msg.next_hop_idx]

            # 1) If (u→v) no longer exists, splice in a new sub-path
            if not self.topology.G.has_edge(u, v):
                remainder = nx.shortest_path(self.topology.G, u, dst, weight='cost')
                # build: existing hops up to u, then remainder
                msg.path = msg.path[: msg.next_hop_idx] + remainder
                v = msg.path[msg.next_hop_idx]  # updated next hop

            # 2) Reserve & transmit on (u→v)
            edge      = self.topology.get_edge(u, v)
            bw, prd   = edge.get('BW', 1), edge.get('PrD', 0)
            ser       = msg.size * 8 / (bw * 1e6)
            latency   = ser + prd
            now       = self.env.now
            busy_until= self.link_busy.get((u, v), 0.0)
            wait      = max(0, busy_until - now)

            # atomically reserve the link
            self.link_busy[(u, v)] = now + wait + latency

            # actually incur wait + transmission
            yield self.env.timeout(wait + latency)

            # advance to the next hop
            msg.next_hop_idx += 1

        # arrived!
        msg.time_rec = self.env.now
        logger.info(
            f"{msg.time_rec:8.5f}s  RECV  "
            f"{msg.message_type.name:<8}  "
            f"msg#{msg.id}  "
            f"{(msg.link.src_module if msg.link else '')}@{msg.src_node} → "
            f"{(msg.link.dst_module if msg.link else msg.data_name)}@{dst}"
        )
        # log to CSV (as before)…
        self.csv_logger.log({
            "timestamp":   f"{msg.time_rec:.5f}",
            "event_type":  "RECV",
            "msg_type":    msg.message_type.name,
            "msg/data_name":    (msg.link.name if msg.link else msg.data_name),
            "msg_id":      msg.id,
            "src_service": (msg.link.src_module if msg.link else ""),
            "dst_service": (msg.link.dst_module if msg.link else ""),
            "src_node":    msg.src_node,
            "dst_node":    msg.dst_node,
            "application": (msg.link.app if msg.link else ""),
        })
        # finally deliver it up the stack
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
            ev = self.wait_for_data(src_node, data_name)
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
            self._failure_file.close()








