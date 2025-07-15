from typing import Any, Dict
import networkx as nx

class HighestVersionSelectionStrategy:

    def select(self, candidates: Dict[Any,int], src_node: Any, topology: nx.Graph) -> Any:
        
        if not candidates:
            raise ValueError("No candidates provided")

        # 1) find the highest version
        max_ver = max(candidates.values())
        top_nodes = [node for node, ver in candidates.items() if ver == max_ver]

        # 2) if only one, easy
        if len(top_nodes) == 1:
            return top_nodes[0]

        distances = {
            node: nx.shortest_path_length(topology, src_node, node, weight='cost')
            for node in top_nodes
        }
        # pick the node with minimal distance
        best = min(distances, key=distances.get)
        return best
            