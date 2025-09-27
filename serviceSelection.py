# selection.py

from abc import ABC, abstractmethod
import random
from typing import List, Any, Optional, Dict
import networkx as nx


class ServiceSelection(ABC):
    """
    Base class for any node‐selection strategy.
    Users should override `select()` to implement their own logic.
    """

    @abstractmethod
    def select(
        self,
        candidates: List[Any],
        src_node: Any,
        topology: nx.Graph,
        **kwargs
    ) -> Any:
        """
        Return one element from `candidates`, based on the strategy.

        Args:
            candidates: list of node IDs to choose from
            src_node:   the node ID where the request originates
            topology:   the networkx graph of the topology
            **kwargs:    additional parameters (e.g. weights, metrics)

        Returns:
            One element of `candidates`.
        """
    


class RandomSelectionStrategy(ServiceSelection):
    """Pick uniformly at random."""

    def select(self, candidates, src_node, topology, **kwargs):
        return random.choice(candidates)


class FirstSelectionStrategy(ServiceSelection):
    """Always pick the first candidate."""

    def select(self, candidates, src_node, topology, **kwargs):
        if not candidates:
            raise ValueError("No candidates available")
        return candidates[0]


class ShortestPathSelectionStrategy(ServiceSelection):
    """
    Pick the candidate whose shortest‐path distance (using `weight`)
    from `src_node` is minimal.
    """

    def __init__(self, weight: Optional[str] = "cost"):
        self.weight = weight

    def select(self, candidates, src_node, topology, **kwargs):
        if not candidates:
            raise ValueError("No candidates available")

        # compute path‐lengths once
        lengths: Dict[Any, float] = {}
        for dst in candidates:
            try:
                lengths[dst] = nx.shortest_path_length(
                    topology, src_node, dst, weight=self.weight
                )
            except nx.NetworkXNoPath:
                lengths[dst] = float("inf")

        # pick the candidate with minimum length
        best = min(lengths, key=lengths.get)
        if lengths[best] == float("inf"):
            # no reachable candidate
            raise RuntimeError(f"No path from {src_node} to any candidate")
        return best
    


