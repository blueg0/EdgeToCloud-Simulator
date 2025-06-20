# core.py

from typing import Any, Dict, List, Tuple
import networkx as nx
import simpy
import random
import itertools
from Topology import Topology
from application import *
from deploymentManager import DeploymentManager  

class Core:
    def __init__(
        self,
        env: simpy.Environment,
        topology: Topology
    ):
        self.env       = env
        self.topology  = topology

        # map from app_name -> Application
        self.apps: Dict[str, Application] = {}

        # map from (app_name, service_name) -> [node IDs]
        self.placement: Dict[Tuple[str, str], List[Any]] = {}
        self._msg_id_counter = itertools.count(1)


    def register_application(self, app: Application):
        """Register a new Application under its `app.name`."""
        if app.name in self.apps:
            raise ValueError(f"App {app.name!r} already registered")
        self.apps[app.name] = app

    def place_service(
        self,
        app_name:     str,
        service_name: str,
        nodes:        List[Any]
    ):
        """
        Record where to deploy a given service of a given app.
        """
        if app_name not in self.apps:
            raise KeyError(f"No such app {app_name!r}")
        if service_name not in self.apps[app_name].services:
            raise KeyError(f"No such service {service_name!r} in app {app_name!r}")
        self.placement[(app_name, service_name)] = nodes

    def start(self):
        """
        Launch *all* generators across *all* registered apps.
        """
        for app in self.apps.values():
            for svc_name in app.sources:
                svc = app.services[svc_name]
                assert isinstance(svc, GenerationService)
                for node in self.placement.get((app.name, svc_name), []):
                    svc.node_id = node
                    self.env.process(svc.generate(core=self, env=self.env))

    def send_source_message(self, msg: Message):
        """
        Called by a GenerationService when it has a fresh Message.
        Stamp on the src_node and forward to send_message().
        """
        app = self.apps[msg.link.app]
        src_svc = app.services[msg.link.src_module]
        msg.src_node = getattr(src_svc, "node_id", None)
        self.send_message(msg)

    def send_message(self, msg: Message):
        """
        Pick one of the deployed instances of msg.link.dst_module,
        compute shortest‐path over the topology, and pump it along.
        """
        app = self.apps[msg.link.app]
        key = (msg.link.app, msg.link.dst_module)
        candidates = self.placement.get(key)
        if not candidates:
            raise RuntimeError(f"No placement for {key}")

        # 1) randomly load‐balance among instances

        msg.id = next(self._msg_id_counter)

        dst_node = random.choice(candidates)
        msg.dst_node = dst_node

        # 2) stamp send time & print it
        send_time = self.env.now
        print(f"[SEND @ {send_time:.3f}s] "
              f"{msg.link.name} | "
              f"{msg.link.src_module}@{msg.src_node} → "
              f"{msg.link.dst_module}@{msg.dst_node}")

        # 3) find path in the network (weighted by PrD) and start transmission
        msg.path = nx.shortest_path(
            self.topology.G,
            source=msg.src_node,
            target=dst_node,
            weight="PrD"
        )
        msg.next_hop_idx = 1
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
            ser  = msg.size / (bw * 1e6)
            yield self.env.timeout(ser + prd)

        # arrived
        msg.time_rec = self.env.now
        self._deliver(msg)

    def _deliver(self, msg: Message):
        """
        Hand the message to the next service’s logic.
        If it’s a ProcessingServices, start its process(); else drop/log.
        """
        recv_time = self.env.now
        print(f"[RECV @ {recv_time:.3f}s] "
              f"{msg.link.name} | "
              f"{msg.link.src_module}@{msg.src_node} → "
              f"{msg.link.dst_module}@{msg.dst_node}")

        app = self.apps[msg.link.app]
        svc = app.services[msg.link.dst_module]
        if isinstance(svc, ProcessingServices):
            self.env.process(
                svc.process(msg, core=self, env=self.env, node_id=msg.dst_node)
            )

    
# 1) Build the SimPy environment and topology
env  = simpy.Environment()
topo = Topology()
topo.load_topology("topology.json")

# 2) Create the Core and register your application
core = Core(env, topology=topo)
app  = Application(name="MyApp")
core.register_application(app)

# 3) Define services
temp_svc = GenerationService(
    name         = "TempSensor",
    distribution = ExponentialDistribution(rate=1/60),
    data_fn      = DataGenerator(name="temperature", size=500, content="20°C"),
    out_messages = []
)
filter_svc = ProcessingServices(name="FilterService", )
logger_svc = ProcessingServices(name="LoggerService")

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
    instructions = 5000,
    size         = 100,
    data_name    = "temperature"
)
link2 = ServiceLink(
    name         = "LogTemp",
    app          = app.name,
    src_module   = filter_svc.name,
    dst_module   = logger_svc.name,
    instructions = 2000,
    size         =   100,
    data_name    = None    # no payload, just a signal
)
app.add_link(link1)
app.add_link(link2)

# 6) Create a DeploymentManager specifically for "MyApp"
deployer = DeploymentManager(core, app.name)

# 7) Deploy each service (places + starts generators as needed)
deployer.deploy_generator("TempSensor",    ["s6"])     # three temperature sensors? just one here
deployer.deploy_processor("FilterService", ["fog3"])
deployer.deploy_processor("LoggerService", ["cloud1"])

# 8) Run the simulation
env.run(until=1000)

topo.visualize()
 
