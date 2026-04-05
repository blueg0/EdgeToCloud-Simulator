from collections import defaultdict
import json 
import logging
logging.getLogger(__name__).setLevel(logging.INFO)
import simpy
import networkx as nx
from typing import Dict, List, Optional, Union

import matplotlib.pyplot as plt

from application import Data
from default import PROTOCOLS, NodeGroup, generate_default_resources

class StorageFullError(Exception):
    pass


def _gb_to_bytes(val):
    """Interpret a numeric storage value as GB and convert to bytes."""
    return int(val * (1024 ** 3))









class Node:
    """
    Represents a generic node with compute and storage capabilities.
    """
    def __init__(
        self,
        id: str,
        env : simpy.Environment,
        name: str,
        group: NodeGroup,
        pos: tuple,
        mobility: bool = False,
        resources: dict = None,
        buffer_size: Optional[int] = None,
        failure_rate: float = 0.0,   # failures per time unit
        repair_rate:  float = 0.0    # repairs per time unit        
    ):
        self.id = id
        self.env= env
        self.name = name
        self.group = group
        self.pos = pos
        self.mobility = mobility
        self.failure_rate = failure_rate
        self.repair_rate  = repair_rate 
        self. max_versions: int = 2       

        # Override or generate resource capacities
        if resources is None:
            resources = generate_default_resources(group)
        self.cpu_capacity = resources.get('cpu', 0)
        self.memory_capacity = resources.get('memory', 0)
        self.storage_capacity = resources.get('storage', 0)

        # Dynamic state
       # simpy primitives
        if self.cpu_capacity > 0: 
            self.cpu = simpy.Container(
                env,
                init=self.cpu_capacity,
                capacity=self.cpu_capacity
            )
        # optional inbound buffer
        if buffer_size is None or buffer_size <= 0:
            self.buffer = simpy.Store(env)
        else:
           self.buffer = simpy.Store(env, capacity=buffer_size)
        self.memory_allocated = 0
        self.storage_used = 0
        self.storage: Dict[str, List[Data]] = defaultdict(list)

    def __repr__(self):
        return (
            f"<Node {self.id} ({self.group.name}) "
            f"CPU={self.cpu.level}/{self.cpu_capacity} "
            f"RAM={self.memory_capacity} "
            f"STO={self.storage_used}/{self.storage_capacity}>"
        )


    def store_data(self, data: Data):
        """
        Store a Data object on the node, but keep only the newest
        `max_versions` for each data.name.  If adding this would exceed
        that, delete the oldest version first.
        """
        # 1) Prune oldest if we already have max_versions of this name
        lst = self.storage[data.name]
        if len(lst)>= self.max_versions:
            # find and remove the *oldest* (minimum version)
            oldest = min(lst, key=lambda d: d.version)
            lst.remove(oldest)
            self.storage_used -= oldest.size

        # 2) Now store the new one (still enforcing capacity)
        if data.size + self.storage_used > self.storage_capacity:
            raise StorageFullError(
                f"Node {self.id} storage full: "
                f"{self.storage_used}/{self.storage_capacity}"
            )
        # 3) append new
        lst.append(data)
        self.storage_used += data.size


    def get_data(self, name: str, version: Optional[int] = None) -> Optional[Data]:
        """Return the latest (or specific) version from storage[name]."""
        lst = self.storage.get(name, [])
        if not lst:
            return None
        if version is None:
            # pick highest version
            data = max(lst, key=lambda d: d.version)
        else:
            # find specific
            matches = [d for d in lst if d.version == version]
            data = matches[0] if matches else None
        if data:
            data.last_consult = self.env.now
        return data


    def delete_data(self, name: str, version: Optional[int] = None) -> None:
        """Remove one version from storage[name]."""
        lst = self.storage.get(name)
        if not lst:
            raise KeyError(f"No data named {name!r} on node {self.id}")
        # choose which to delete
        if version is None:
            # delete *oldest* version
            to_del = min(lst, key=lambda d: d.version)
        else:
            matched = [d for d in lst if d.version == version]
            if not matched:
                raise KeyError(f"No data named {name!r} v{version} on node {self.id}")
            to_del = matched[0]
        lst.remove(to_del)
        self.storage_used -= to_del.size
        if not lst:
            # clean up empty entry
            del self.storage[name]





    




class Topology:
 
    

    def __init__(self,
                 logger: logging.Logger = None):
        """
        :param directed: whether to use a directed graph
        :param logger:   optional injected logger
        """
        self.logger = logger or logging.getLogger(__name__)
        self.protocols = PROTOCOLS

        self.G = nx.Graph()
        self.env = simpy.Environment()

    def load_topology(self, topo_source: Union[str, dict]):
        if isinstance(topo_source, str):
            with open(topo_source, "r") as f:
                topo = json.load(f)
        else:
            topo = topo_source

        for node in topo.get("nodes", []):
            try:
                self.add_node(node)
                self.logger.info(f"Added node {node['id']} ({node['name']})")
            except Exception as e:
                self.logger.error(f"Failed to add node {node.get('id', '?')}: {e}")

        for link in topo.get("links", []):
            missing = [k for k in ('s', 'd', 'protocol') if k not in link]
            if missing:
                raise ValueError(f"Link definition missing fields: {missing}")

            u, v, proto = link['s'], link['d'], link['protocol']
            attributes = link.get('attributes', {})
            try:
                self.add_link(u, v, proto, attributes)
                self.logger.info(f"Added link {u}->{v} via {proto}")
            except Exception as e:
                self.logger.error(f"Failed to add link {u}->{v}: {e}")

        self.logger.info(f"Topology loaded: {self.G.number_of_nodes()} nodes, {self.G.number_of_edges()} edges")

    def get_node(self, node_id: str) -> Node:
        """
        Return the Node object for the given node_id.
        """
        if node_id in self.G:
            return self.G.nodes[node_id]['obj']
        self.logger.error(f"Node '{node_id}' not found in topology")
        return None

    def get_edge(self, u: str, v: str) -> dict:
        data = self.G.get_edge_data(u, v)
        if data is None:
            self.logger.error(f"Edge '{u}->{v}' not found in topology")
            return {}
        return data

    def get_nodes(self, *, group: NodeGroup = None) -> list[str]:
        """
        Return list of node IDs, optionally filtered by NodeGroup.
        """
        return [n for n, d in self.G.nodes(data=True)
                if group is None or d['obj'].group == group]

    def get_edges(self, *, data: bool = False):
        return list(self.G.edges(data=data))

    def add_node(self, node: dict):
        required = ['id', 'name', 'group', 'position']
        missing = [k for k in required if k not in node]
        if missing:
            raise ValueError(f"Node missing mandatory fields: {missing}")

        nid = node['id']
        name = node['name']
        pos = tuple(node['position'])

        try:
            group = NodeGroup(node['group'].lower())
        except ValueError:
            raise ValueError(f"Invalid node group '{node['group']}' for node {nid}")

        mobility = node.get('mobility', False)
        resources = node.get("resources")
        if resources is None:
            resources = generate_default_resources(group)   # existing call
        else:
            resources = dict(resources)  # shallow copy to avoid mutating the caller

        # Normalize storage: interpret topology JSON 'storage' as GB → convert to bytes
        if "storage" in resources and isinstance(resources["storage"], (int, float)):
            resources["storage"] = _gb_to_bytes(resources["storage"])
        fr = node.get("failure_rate", 0.0)
        rr = node.get("repair_rate", 0.0)


        node_obj = Node(
            env=self.env,
            id = nid,
            name= name,
            group= group,
            pos= pos, 
            mobility= mobility,
            resources=resources,
            buffer_size = node.get("buffer_size", None),
            failure_rate=fr,
            repair_rate=rr
        )

        # Store only the Node object; all access goes through it
        self.G.add_node(nid, obj=node_obj)

    def add_link(self, u: str, v: str, protocol: str, attributes: dict = None) -> int:
        if u not in self.G or v not in self.G:
            raise KeyError(f"Both nodes '{u}' and '{v}' must exist to add a link")

        proto_def = self.protocols.get(protocol)
        if not proto_def:
            raise ValueError(f"Unknown protocol '{protocol}'")

        data = {'protocol': protocol, **proto_def}
        node_u = self.G.nodes[u]['obj']
        node_v = self.G.nodes[v]['obj']
        pu = node_u.pos
        pv = node_v.pos
        dist = ((pu[0] - pv[0])**2 + (pu[1] - pv[1])**2)**0.5
        data['length'] = dist
        data['PrD'] = dist/data["prop_speed"]
        data['cost'] = 1.0 / data["BW"]
        data['failure_rate'] = float(attributes.get('failure_rate', 0.0))
        data['repair_rate']  = float(attributes.get('repair_rate', 0.0))

        self.G.add_edge(u, v, **data)
        return self.G.number_of_edges()

    def remove_node(self, node_id: str) -> int:
        if node_id not in self.G:
            self.logger.error(f"Node '{node_id}' not found for removal")
            return self.G.number_of_nodes()
        self.G.remove_node(node_id)
        return self.G.number_of_nodes()

    def remove_link(self, u: str, v: str) -> int:
        if not self.G.has_edge(u, v):
            self.logger.error(f"Cannot remove non-existent edge {u}-{v}")
            return self.G.number_of_edges()
        self.G.remove_edge(u, v)
        return self.G.number_of_edges()

    def visualize(self, with_labels=True, figsize=(10, 8)):
        pos = {n: d['obj'].pos for n, d in self.G.nodes(data=True)}
        node_colors = []
        node_sizes = []

        color_map = {
            'cloud':   '#FFB84C',
            'fog': '#C4E1F6',
            'edge': '#FDE49E',
            'smartobject': '#39B5E0',
            'sensor':  '#00DFA2',
            'actuator': '#DA0C81',
        }
        size_map = {
            'cloud':   1200,
            'fog': 1000,
            'edge': 800,
            'smartobject': 600,
            'sensor':  300,
            'actuator': 300,
        }

        for n, d in self.G.nodes(data=True):
            grp = d['obj'].group.value
            node_colors.append(color_map.get(grp, '#CCCCCC'))
            node_sizes.append(size_map.get(grp, 400))

        plt.figure(figsize=figsize)
        nx.draw(
            self.G,
            pos,
            with_labels=with_labels,
            node_color=node_colors,
            node_size=node_sizes,
            edge_color='gray',
            font_size=8
        )
        plt.title("Edge-to-Cloud Topology Visualization")
        plt.show()

    def to_dict(self):
        nodes, links = [], []
        for n, d in self.G.nodes(data=True):
            obj = d['obj']
            nodes.append({
                'id': obj.id,
                'name': obj.name,
                'group': obj.group.value,
                'position': list(obj.pos),
                'mobility': obj.mobility,
                'resources': {
                    self.CPU: obj.cpu_capacity,
                    self.RAM: obj.memory_capacity,
                    self.DISK: obj.storage_capacity
                }
            })

        for u, v, d in self.G.edges(data=True):
            attrs = {k: v for k, v in d.items() if k not in {'protocol'}}
            links.append({
                's': u,
                'd': v,
                'protocol': d.get('protocol', ''),
                'attributes': attrs
            })

        return {'nodes': nodes, 'links': links}
