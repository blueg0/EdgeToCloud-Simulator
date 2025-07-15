
# application.py

import logging


logging.getLogger(__name__).setLevel(logging.INFO)
logger = logging.getLogger(__name__)


from dataclasses import dataclass
import itertools
import json, copy
import random
from simpy import AllOf
from typing import Any, Callable, Dict, Set, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core import Core
    from ServicesManager import DeploymentManager
from distributions import ExponentialDistribution, DeterministicDistribution, UniformDistribution
import simpy
from enum import Enum, auto
from serviceSelection import SelectionStrategy

class MessageType(Enum):
    APP         = auto()   # normal inter–service traffic
    CONS_REQ   = auto()   # “please give me Data X” request
    CONS_RESP  = auto()   # the reply with Data X
    INFO_REQ   = auto()   # request where to place data from master
    INFO_RESP  = auto()   # the reply with where to place data
    STORE     = auto()   # replicate/copy Data X



# data = { "name": "str","id": "str", "content": "any" ,"size": "int", "cr_time": "float"}

@dataclass(slots=True)
class Data:
    name : str
    version : int
    origin_node : Any
    size : float
    value : Any
    cr_time : float
    last_consult : float




@dataclass(slots=True)
class DataGenerator:
    name    : str            
    size    : float | Callable[[], float]
    content : Any | Callable[[], Any]

    def __call__(self, timestamp: float, version: int, origin_node : str) -> Data :
        """
        Returns a tuple (value, size).
        We’ll wrap that into a Data() later.
        """
        val  = self.content() if callable(self.content) else self.content
        sz   = self.size()    if callable(self.size)    else self.size
        return Data(
            name=self.name,
            version=version,
            origin_node=origin_node,
            size=sz,
            value=val,
            cr_time=timestamp,
            last_consult=timestamp
        )
    

    

@dataclass(frozen=True, slots=True)
class ServiceLink:
    """
    A *logical* edge in your application graph.
    
      • name         : logical message name/type  
      • src_module   : producer service name  
      • dst_module   : consumer service name  
      • instructions : compute units required at the consumer  
      • size         : byte-size of the payload (if any)  
      • with_data    : if this link will transfer data 
                       (false if this link carries no data)
    """
    name:         str
    app:          str
    src_module:   str
    dst_module:   str
    instructions: float = 0.0
    size:         int = 0
    with_data : bool = False
    probability: float = 1.0

    

class Message:
    __slots__ = (
        "id", "link", "data", "size", "instructions",
        "time_cr", "time_rec", "src_node", "dst_node",
        "path", "next_hop_idx",
        "data_name",      # only for fetch/replica
        "message_type",   # one of the above MessageType values
    )
    def __init__(
        self,
        message_type: MessageType,
        link:    Optional[ServiceLink]=None,
        src_node: Any       =None,
        dst_node: Any       =None,
        data:     Data       =None,
        size:     int       =0,
        instructions:int    =0,
        data_name: Optional[str]=None,
        
    ):
        self.link          = link
        self.id            = None
        self.data          = data
        self.size          = size
        self.instructions  = instructions
        self.time_cr       = 0.0
        self.time_rec      = 0.0
        self.src_node      = src_node
        self.dst_node      = dst_node
        self.path          = []
        self.next_hop_idx  = 0
        self.data_name     = data_name
        self.message_type  = message_type


    
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
       A service that periodically generates Data 
    and wraps them into outgoing ServiceLinks.
    """
    def __init__(
        self,
        name:         str,
        distribution: Callable[[], float],
        data_fn:      DataGenerator,
        out_messages: List[ServiceLink] = None,
        place_data : bool = False
        
    ):
        super().__init__(name=name, out_messages=out_messages)
        self.distribution= distribution
        self.data_fn = data_fn
        self.place_data = place_data
        self._version_counters = itertools.count(1)
        
        
    def generate(self,core: 'Core', env: simpy.Environment, node_id):
        

        while True:
           # 1) Wait
            yield env.timeout(self.distribution())

            # 2) Build a Data object
            
            version     = next(self._version_counters)
            data = self.data_fn(timestamp=env.now, version=version, origin_node=node_id)

            if self.place_data :
                core._place_data(data=data, src_node= node_id)

            for link in self.out_messages:
                msg = Message(
                link      = link,
                src_node  = node_id,
                data      = data,
                message_type= MessageType.APP,
                instructions= link.instructions,
                size=data.size,
                
                )
                core.send_message(msg)
                
                    
class ProcessingServices(Service) :
    def __init__(
        self,
        name: str,
        requirements: dict = None,
        in_messages: List[ServiceLink] = None,
        out_messages: List[ServiceLink] = None,
        data_fn:     DataGenerator = None,
        external_data: set = None,
        place_before : bool =False,
        place_after : bool = False, 
        last_service : bool = False
    ):
        super().__init__(
            name=name,
            requirements=requirements or {},
            in_messages=in_messages or [],
            out_messages=out_messages or []
        )
        self.external_data = external_data or set()
        self._version_counters= itertools.count(1)
        self.data_fn = data_fn
        self.place_before = place_before
        self.place_after = place_after
        self.last_service = last_service

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
        if self.place_before :

            core._place_data(data=msg.data, src_node= node_id)

        wait_events = []

        for data_name in self.external_data:
            ev = core._select_data(service= self.name, data_name=data_name, src_node=node_id)
            wait_events.append(ev)

        # 1) Wait for *all* of them (if any)
        if wait_events:
            all_res = yield AllOf(env, wait_events)
            fetched = {d.name: d for ev, d in all_res.items()}
            logger.info(
                f"{env.now:8.5f}s  GOT_DATA  "
                f"{self.name}@{node_id} got {list(fetched)}"
            )
        else:
            fetched = {}

        # 1) stamp start
       

        # 2) Stamp start of processing (compute & queuing)
        arrival = env.now
        
        node_obj = core.topology.get_node(node_id)
        yield  node_obj.cpu.get(msg.instructions)

        queuing = env.now - arrival
        start_compute = env.now
        logger.info(
            f"{start_compute:8.5f}s  PROC_START  "
            f"msg#{msg.id}  "
            f"{self.name}@{node_id}"
        )
        core.csv_logger.log({
            "timestamp":   f"{start_compute:.5f}",
            "msg/data_name":    msg.link.name,
            "msg_id":      msg.id,
            "event_type":  "PROC_START",
            "src_service": self.name,
            "dst_service": "",
            "src_node":    node_id,
            "dst_node":    node_id,
            "application": msg.link.app
        })
        cpu_speed     = node_obj.cpu_capacity
        compute_time  = msg.instructions / cpu_speed
        yield env.timeout(compute_time)
        yield node_obj.cpu.put(msg.instructions)
        finish = env.now
        logger.info(
            f"{finish:8.5f}s  PROC_END  "
            f"msg#{msg.id}  "
            f"{self.name}@{node_id} "
            f"queued={queuing:.3f}s compute={compute_time:.3f}s"
        )
        core.csv_logger.log({
            "timestamp":   f"{finish:.5f}",
            "msg/data_name":    msg.link.name,
            "msg_id":      msg.id,
            "event_type":  "PROC_END",
            "src_service": self.name,
            "dst_service": "",
            "src_node":    node_id,
            "dst_node":    node_id,
            "application": msg.link.app
        })

        # 3) stamp finish
       

        
        # possibly generate new Data
        new_data = None
        if self.data_fn is not None:
            version     = next(self._version_counters)
            new_data = self.data_fn(timestamp=env.now, version=version, origin_node=node_id)
            
        if self.place_after and new_data is not None :
            core._place_data(data=new_data, src_node= node_id)
        # 4) forward on each outgoing link with its own probability
        for link in self.out_messages:
            if random.random() >= link.probability:
                continue

            out = Message(
                link         = link,
                src_node     = node_id,
                data         = new_data if link.with_data else None,
                message_type = MessageType.APP,
                size = new_data.size if link.with_data else link.size,
                instructions = link.instructions
                )
            
            
            

            core.send_message(out)


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
        self.deployer: Optional[DeploymentManager] = None

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

        # 1) Services (unchanged)
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

        # 2) Links (updated)
        for L in cfg.get("links", []):
            link = ServiceLink(
                name         = L["name"],
                app          = self.name,
                src_module   = L["src"],
                dst_module   = L["dst"],
                instructions = float(L.get("instructions", 0.0)),
                size         = int(L.get("size", 0)),
                # now a bool indicating whether this link carries data
                with_data    = bool(L.get("data_name")),
                # optional forward‐probability (default 1.0)
                probability  = float(L.get("probability", 1.0))
            )
            self.add_link(link)