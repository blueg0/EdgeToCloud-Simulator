
# application.py




from dataclasses import dataclass
import itertools
import json, copy
import random

from typing import Any, Callable, Dict, Set, List, Optional, Union
from distributions import *
import simpy


# data = { "name": "str","id": "str", "content": "any" ,"size": "int", "cr_time": "float"}
    
@dataclass(slots=True)
class DataGenerator:
    name:    str
    size:    int
    content: Any

    def __call__(self) -> Dict[str,Any]:
        
        return {
            "name" : self.name,
            "content": self.content() if callable(self.content) else self.content,
            "size":    self.size() if callable(self.size) else self.size
        }
    


@dataclass(frozen=True, slots=True)
class ServiceLink:
    """
    A *logical* edge in your application graph.
    
      • name         : logical message name/type  
      • src_module   : producer service name  
      • dst_module   : consumer service name  
      • instructions : compute units required at the consumer  
      • size         : byte-size of the payload (if any)  
      • data_name    : the key under which data will be placed in runtime Message.data  
                       (None if this link carries no data)
    """
    name:         str
    app:          str
    src_module:   str
    dst_module:   str
    instructions: float = 0.0
    size:         int = 0
    data_name:    Optional[str] = None 
    probability: float = 1.0

    

class Message:
    __slots__ = (
        "id", "link", "data", "size", "instructions",
        "time_cr", "time_rec", "src_node", "dst_node",
        "path", "next_hop_idx"
    )

    def __init__(self, link: ServiceLink):
        self.link         = link
        self.id           = None
        self.data         = None
        # **always** initialize from the link template
        self.size         = link.size
        self.instructions = link.instructions
        self.time_cr      = 0.0
        self.time_rec     = 0.0
        self.src_node     = None
        self.dst_node     = None
        self.path         = []
        self.next_hop_idx = 0

    def __repr__(self):
        return (f"<Message {self.link.name} "
                f"{self.link.src_module}→{self.link.dst_module} "
                f"id={self.id} cr={self.time_cr:.2f}>")

    
class Service:
    
    def __init__(self,
                name: str,
                requirements: Optional[Dict[str, float]] = None,
                in_messages: List[ServiceLink] = None,
                out_messages: List[ServiceLink] = None,
                
    ):
        self.name = name
        self.requirements = requirements  or {}
        self.in_messages = in_messages  or []
        self.out_messages = out_messages or []
        
  


class GenerationService(Service):
    """
       A service that periodically generates Data dicts of the form:
      { "name": str,
        "id": str,
        "content": Any,
        "size": int,
        "createdd": float }
    and wraps them into outgoing ServiceLinks.
    """
    def __init__(
        self,
        name:         str,
        distribution: Callable[[], float],
        data_fn:      Callable[[], Dict[str,Any]],
        out_messages: List[ServiceLink] = None,
        
    ):
        super().__init__(name=name, out_messages=out_messages)
        self.distribution= distribution
        self.data_fn = data_fn
        # self.node_id = None
        self._id_counter = itertools.count(1)
        
        
    def generate(self,core, env: simpy.Environment):
        while True:
            # a) wait next interval
            dt = self.distribution()
            yield env.timeout(dt)

            # b) generate data 
            data = self.data_fn() # lambda: {"name":"Temp", "size":100, "content":random.uniform(20,30)}
            data["id"] = next(self._id_counter) 
            data["created"] = env.now
            for link in self.out_messages:
                msg              = Message(link)
                msg.data         = data
                msg.size         = data["size"]
                msg.time_cr      = env.now
                core.send_source_message(msg)
    
class ProcessingServices(Service) :
    def __init__(
        self,
        name: str,
        requirements: dict = None,
        in_messages: List[ServiceLink] = None,
        out_messages: List[ServiceLink] = None,
        data_fn:      Callable[[], Dict[str,Any]] = None,
        external_data: set = None
    ):
        super().__init__(
            name=name,
            requirements=requirements or {},
            in_messages=in_messages or [],
            out_messages=out_messages or []
        )
        self.external_data = external_data or set()
        self._data_id_counter = itertools.count(1)
        self.data_fn = data_fn

    def process(
        self,
        msg: Message,
        core: 'Core',
        env: simpy.Environment,
        node_id: Any
    ):
        """
        SimPy process for handling an incoming message:
          • simulate compute delay
          • then for each outgoing link, forward with prob=link.probability
        """
        # 1) stamp start
        start = env.now
        print(f"[PROC-START @{start:.3f}s] "
              f"{msg.link.name} @ {self.name} on node {node_id}")

        # 2) compute delay
        cpu_speed = core.topology.G.nodes[node_id]["cpu"]
        delay = msg.instructions / cpu_speed
        yield env.timeout(delay)

        # 3) stamp finish
        finish = env.now
        print(f"[PROC-END   @{finish:.3f}s] "
              f"{msg.link.name} @ {self.name} on node {node_id} "
              f"(took {delay:.3f}s)")
        if self.data_fn is not None:
            data = self.data_fn()
        # 4) forward on each outgoing link with its own probability
        for link in self.out_messages:
            p = getattr(link, "probability", 1.0)
            if random.random() >= p:
                # dropped by probability
                continue
            
            new_msg = Message(link)
            new_msg.instructions = link.instructions
            new_msg.time_cr      = env.now
            new_msg.id           = msg.id      
            new_msg.src_node     = node_id
            if link.data_name is not None:
                new_msg.data         = data
                new_msg.size         = data['size'] 
            else :
                new_msg.data = None
                new_msg.size = link.size

            core.send_message(new_msg)


# class BatchProcessingService(Service):
#     pass    
    


class Application:
    """
    Build an application entirely from a JSON spec.
    """

    def __init__(self, name: str):
        self.name     = name
        self.services: Dict[str, Service] = {}
        self.links:    List[ServiceLink]  = []
        self.sources:  Set[str]           = set()

    def add_service(self, svc: Service):
        if svc.name in self.services:
            raise ValueError(f"Service {svc.name!r} already exists")
        self.services[svc.name] = svc
        if isinstance(svc, GenerationService):
            self.sources.add(svc.name)

    def add_link(self, link: ServiceLink):
        if link.src_module not in self.services:
            raise ValueError(f"Unknown src service: {link.src_module}")
        if link.dst_module not in self.services:
            raise ValueError(f"Unknown dst service: {link.dst_module}")
        self.links.append(link)
        self.services[link.src_module].out_messages.append(link)
        self.services[link.dst_module].in_messages.append(link)

    def load_from_json(self, path: str):
        cfg = json.load(open(path))

        # 1) Services
        for s in cfg.get("services", []):
            name = s["name"]
            typ  = s.get("type", "PROCESS").upper()

            if typ == "GENERATION":
                # build distribution
                dcfg = s["distribution"]
                if dcfg["type"] == "exponential":
                    dist = ExponentialDistribution(rate=dcfg["rate"])
                elif dcfg["type"] == "deterministic":
                    dist = DeterministicDistribution(interval=dcfg["interval"])
                elif dcfg["type"] == "uniform":
                    dist = UniformDistribution(
                        low=dcfg["low"], high=dcfg["high"]
                    )
                else:
                    raise ValueError(f"Unknown distribution: {dcfg['type']}")

                # build data generator
                gcfg = s["data_gen"]
                if gcfg["type"] == "static":
                    data_fn = DataGenerator(
                        name    = gcfg["name"],
                        size    = gcfg["size"],
                        content = gcfg["content"]
                    )
                else:
                    raise ValueError(f"Unknown data_gen: {gcfg['type']}")

                svc = GenerationService(
                    name         = name,
                    distribution = dist,
                    data_fn      = data_fn,
                    out_messages = []
                )

            elif typ == "PROCESS":
                svc = ProcessingServices(name=name)
            else:
                svc = Service(name=name)

            self.add_service(svc)

        # 2) Links
        for L in cfg.get("links", []):
            link = ServiceLink(
                name         = L["name"],
                app          = self.name,
                src_module   = L["src"],
                dst_module   = L["dst"],
                instructions = float(L.get("instructions", 0.0)),
                size         = int  (L.get("size",         0)),
                data_name    = L.get("data_name")  # may be None
            )
            self.add_link(link)

    def __repr__(self):
        svcs = ", ".join(self.services)
        lnks = ", ".join(f"{l.src_module}→{l.dst_module}({l.name})"
                         for l in self.links)
        return (f"Application({self.name!r}, "
                f"services=[{svcs}], links=[{lnks}])")