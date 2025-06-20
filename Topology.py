import json 
import logging
import simpy
import networkx as nx
from typing import Union

import matplotlib.pyplot as plt
from default import PROTOCOLS, NodeGroup, generate_default_resources

# Global logger setup
logging.basicConfig(level=logging.INFO)


class Topology:
    CPU = 'cpu'
    RAM = 'ram'
    DISK = 'disk'
    PROPAGATION_SPEED = 3e8  # meters per second (speed of light)

    def __init__(self,

                 directed: bool = False,
                 logger: logging.Logger = None):
        """
        
        :param directed: whether to use a directed graph
        :param logger:   optional injected logger
        """
        
        self.logger = logger or logging.getLogger(__name__)
        self.protocols = PROTOCOLS
        self.logger.debug(f"Loaded {len(self.protocols)} protocols and mobility defaults")
        self.G = nx.DiGraph() if directed else nx.Graph()

    def load_topology(self, topo_source: Union[str, dict]):
        if isinstance(topo_source, str):
            with open(topo_source, "r") as f:
                topo = json.load(f)
        else:
            topo = topo_source

        for node in topo.get("nodes", []):
            try:
                self.add_node(node)
                self.logger.debug(f"Added node {node['id']} ({node['name']})")
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
                self.logger.debug(f"Added link {u}->{v} via {proto}")
            except Exception as e:
                self.logger.error(f"Failed to add link {u}->{v}: {e}")

        self.logger.info(f"Topology loaded: {self.G.number_of_nodes()} nodes, {self.G.number_of_edges()} edges")
        
    def get_node(self, node_id: str) -> dict:
        if node_id in self.G:
            return self.G.nodes[node_id]
        self.logger.error(f"Node '{node_id}' not found in topology")
        return {}

    def get_edge(self, u: str, v: str) -> dict:
        data = self.G.get_edge_data(u, v)
        if data is None:
            self.logger.error(f"Edge '{u}->{v}' not found in topology")
            return {}
        return data

    def get_nodes(self, *, group: NodeGroup = None):
        return [n for n, d in self.G.nodes(data=True)
                if group is None or d.get('group') == group]

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
        resources = node.get('resources') or generate_default_resources(group)

        attrs = {
            'name': name,
            'group': group,
            'pos': pos,
            'mobility': mobility,
            **resources
        }

        self.G.add_node(nid, **attrs)

    def add_link(self, u: str, v: str, protocol: str, attributes: dict = None) -> int:
        if u not in self.G or v not in self.G:
            raise KeyError(f"Both nodes '{u}' and '{v}' must exist to add a link")

        proto_def = self.protocols.get(protocol)
        if not proto_def:
            raise ValueError(f"Unknown protocol '{protocol}'")

        
        data = {'protocol': protocol, **proto_def}
        pu, pv = self.G.nodes[u]['pos'], self.G.nodes[v]['pos']
        dist = ((pu[0] - pv[0])**2 + (pu[1] - pv[1])**2)**0.5
        data['length'] = dist
        prd = dist / self.PROPAGATION_SPEED * 1000
        data['PrD'] = prd
        data['cost'] = 1.0 / data["BW"]  #  like OSPF cost
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
        pos = {n: d['pos'] for n, d in self.G.nodes(data=True)}
        node_colors = []
        node_sizes = []

        color_map = {
            'cloud':   '#FFB84C',
            'fognode': '#C4E1F6',
            'edgenode': '#FDE49E',
            'smartobject': '#39B5E0',
            'sensor':  '#00DFA2',
            'actuator': '#DA0C81',
        }
        size_map = {
            'cloud':   1200,
            'fognode': 1000,
            'edgenode': 800,
            'smartobject': 600,
            'sensor':  300,
            'actuator': 300,
        }

        for n, d in self.G.nodes(data=True):
            group = d.get('group')
            key = group.value if isinstance(group, NodeGroup) else str(group)
            node_colors.append(color_map.get(key, '#CCCCCC'))
            node_sizes.append(size_map.get(key, 400))

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
            nodes.append({
                'id': n,
                'name': d.get('name', n),
                'group': d['group'].value if isinstance(d['group'], NodeGroup) else d['group'],
                'position': list(d['pos']),
                'mobility': d.get('mobility', False),
                'resources': {k: d[k] for k in [self.CPU, self.RAM, self.DISK] if k in d}
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
