import json 
import logging
import simpy
import networkx as nx
from typing import Union
import matplotlib.pyplot as plt
from default import PROTOCOLS, DEFAULT_MOBILITY

class Topology:
    PROPAGATION_SPEED = 3e8  # meters per second (speed of light)

    def __init__(self,
                 env: simpy.Environment,
                 directed: bool = False,
                 logger: logging.Logger = None):
        """
        :param env:      the SimPy environment
        :param directed: whether to use a directed graph
        :param logger:   optional injected logger
        """
        self.env = env
        self.logger = logger or logging.getLogger(__name__)
        self.protocols = PROTOCOLS
        self.mobility_map = DEFAULT_MOBILITY
        self.logger.debug(f"Loaded {len(self.protocols)} protocols and mobility defaults")
        self.G = nx.DiGraph() if directed else nx.Graph()

    def load_topology(self, topo_source: Union[str, dict]):
        """Populate self.G from JSON, attach link resources, compute distance and propagation delay."""
        if isinstance(topo_source, str):
            with open(topo_source, "r") as f:
                topo = json.load(f)
        else:
            topo = topo_source

        # Nodes
        for node in topo.get("nodes", []):
            missing = [k for k in ('id','category','type','pos') if k not in node]
            if missing:
                raise ValueError(f"Node definition missing: {missing}")
            nid, cat, typ, pos = node['id'], node['category'], node['type'], node['pos']
            attrs = node.get('attributes', {}).copy()
            attrs['pos'] = tuple(pos)
            attrs['mobility'] = attrs.get('mobility', self.mobility_map.get(typ, False))
            self.G.add_node(nid, category=cat, type=typ, **attrs)
            self.logger.debug(f"Added node {nid}: {cat}/{typ} at {attrs['pos']}, mobility={attrs['mobility']}")

        # Edges
        for link in topo.get("links", []):
            missing = [k for k in ('s','d','protocol') if k not in link]
            if missing:
                raise ValueError(f"Link definition missing: {missing}")
            u, v, proto = link['s'], link['d'], link['protocol']
            overrides = link.get('attributes', {})
            proto_def = self.protocols.get(proto)
            if not proto_def:
                self.logger.warning(f"Unknown protocol '{proto}' for edge {u}->{v}")
                continue

            # Merge protocol defaults and overrides
            data = {**proto_def, **overrides, 'protocol': proto}
             # Compute distance and propagation delay
            pu = self.G.nodes[u]['pos']
            pv = self.G.nodes[v]['pos']
            dist = ((pu[0] - pv[0])**2 + (pu[1] - pv[1])**2)**0.5
            prop_delay_ms = dist / self.PROPAGATION_SPEED * 1000  # ms
            # Override protocol propagation delay
            data['PrD'] = prop_delay_ms
            # Store distance for reference
            data['distance'] = dist
            # Attach SimPy resource for queuing
            data['resource'] = simpy.Resource(self.env, capacity=1)
            # Add edge
            self.G.add_edge(u, v, **data)

           

            self.logger.debug(
                f"Added edge {u}->{v} via {proto}, distance={dist:.2f} m, prop_delay={prop_delay_ms:.3f} ms"
            )

        self.logger.info(f"Topology loaded: {self.G.number_of_nodes()} nodes, {self.G.number_of_edges()} edges")

    def get_node(self, node_id: str) -> dict:
        try:
            return self.G.nodes[node_id]
        except KeyError:
            self.logger.error(f"Node '{node_id}' not found in topology")
            return {}

    def get_edge(self, u: str, v: str) -> dict:
        data = self.G.get_edge_data(u, v)
        if data is None:
            self.logger.error(f"Edge '{u}->{v}' not found in topology")
            return {}
        return data

    def get_nodes(self, *, category: str = None, type: str = None):
        return [n for n, d in self.G.nodes(data=True)
                if (category is None or d['category'] == category)
                and (type is None or d['type'] == type)]

    def get_edges(self, *, data: bool = False):
        return list(self.G.edges(data=data))
    
    def add_node(self, node: dict):
        """Add a single node from a definition dict."""
        missing = [k for k in ('id','category','type','pos') if k not in node]
        if missing:
            raise ValueError(f"Node missing mandatory fields: {missing}")
        nid, cat, typ, pos = node['id'], node['category'], node['type'], node['pos']
        attrs = node.get('attributes', {}).copy()
        attrs['pos'] = tuple(pos)
        attrs['mobility'] = attrs.get('mobility', self.mobility_map.get(typ, False))
        self.G.add_node(nid, category=cat, type=typ, **attrs)

    def add_link(self, u: str, v: str, protocol: str, attributes: dict = None) -> int:
    
        """Add a link between u and v using a protocol key and optional attribute overrides. Returns new edge count."""
        if u not in self.G or v not in self.G:
            raise KeyError(f"Both nodes '{u}' and '{v}' must exist to add a link")
        proto_def = self.protocols.get(protocol)
        if not proto_def:
            raise ValueError(f"Unknown protocol '{protocol}'")
        attrs = (attributes or {}).copy()
        data = {**proto_def, **attrs, 'protocol': protocol}
        data['resource'] = simpy.Resource(self.env, capacity=1)
        # compute distance and override PrD
        pu, pv = self.G.nodes[u]['pos'], self.G.nodes[v]['pos']
        dist = ((pu[0]-pv[0])**2 + (pu[1]-pv[1])**2)**0.5
        data['distance'] = dist
        data['PrD'] = dist/self.PROPAGATION_SPEED*1000
        self.G.add_edge(u, v, **data)
        return self.G.number_of_edges()
    

    def remove_node(self, node_id: str) -> int:
        """Remove the node and all connected edges. Returns new node count."""
        if node_id not in self.G:
            self.logger.error(f"Node '{node_id}' not found for removal")
            return self.G.number_of_nodes()
        self.G.remove_node(node_id)
        return self.G.number_of_nodes()
    
    
    def remove_link(self, u: str, v: str) -> int:
        """Remove the edge between u and v. Returns new edge count."""
        if not self.G.has_edge(u, v):
            self.logger.error(f"Cannot remove non-existent edge {u}-{v}")
            return self.G.number_of_edges()
        self.G.remove_edge(u, v)
        return self.G.number_of_edges()


    def visualize(self, with_labels=True, figsize=(10, 8)):
        """Draw topology with custom colors and sizes per node type using a fresh color scheme."""
        pos = {n: d['pos'] for n, d in self.G.nodes(data=True)}
        node_colors = []
        node_sizes = []

        # New color and size maps
        color_map = {
            'cloud':   '#FFB84C',  
            'fog':     '#C4E1F6',  
            'edge':    '#FDE49E',  
            'device':  '#39B5E0',  
            'comm':    '#59D5E0',  
        }
        size_map = {
            'cloud':   1200,
            'fog':      800,
            'edge':     600,
            'device':   300,
            'comm':     450,
        }

        for n, d in self.G.nodes(data=True):
            typ = d.get('type')
            cat = d.get('category')
            key = typ if typ in color_map else cat if cat in color_map else 'device'
            node_colors.append(color_map[key])
            node_sizes.append(size_map[key])

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
            attrs = {k: v for k, v in d.items() if k not in {'category','type','pos'}}
            nodes.append({
                'id': n,
                'category': d['category'],
                'type': d['type'],
                'attributes': attrs,
                'pos': list(d['pos'])
            })
        for u, v, d in self.G.edges(data=True):
            attrs = {k: v for k, v in d.items() if k != 'protocol'}
            links.append({
                's': u,
                'd': v,
                'protocol': d.get('protocol',''),
                'attributes': attrs
            })
        return {'nodes': nodes, 'links': links}
