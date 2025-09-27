# dataManager.py
from abc import ABC, abstractmethod
from typing import Any, Dict, List, TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core import Core
    from application import Data

from Topology import StorageFullError
from application import Message, MessageType


class DataManager(ABC):
    """
    Abstract contract for data placement and deletion policies.
    `placement` maps a data name to the list of node IDs that should hold replicas.
    """
    def __init__(self):
        self.placement: Dict[str, List[str]] = {}

    @abstractmethod
    def change_placement(self, data_name: str, new_placement: List[str]) -> None:
        """Update the replica set (node IDs) for a given data name."""

    @abstractmethod
    def place_data(self, core: 'Core', data: 'Data', src_node: str) -> None:
        """Replicate freshly produced `data` according to `self.placement`."""

    @abstractmethod
    def delete_data(
        self,
        core: 'Core',
        node: str,
        data_name: str,
        version: Optional[int] = None
    ) -> None:
        """Delete `data_name` (optionally a specific `version`) from `node`."""


class DefaultDataManager(DataManager):
    def __init__(self, data_placement: Dict[str, List[str]]):
        super().__init__()
        self.placement = data_placement or {}

    def change_placement(self, data_name: str, new_placement: List[str]) -> None:
        self.placement[data_name] = list(new_placement or [])

    def place_data(self, core: 'Core', data: 'Data', src_node: str) -> None:
        targets = self.placement.get(data.name, [])
        if not targets:
            print(f"[STORE-SKIP] No placement defined for {data.name!r}")
            return

        for node_id in targets:
            if node_id == src_node:
                node_obj = core.topology.get_node(node_id)
                try:
                    node_obj.store_data(data)
                    core.data_locations[data.name][src_node] = data.version
                except StorageFullError as e:
                    print(f"[STORE-FAIL] {e}")
            else:
                # replicate via the network as a STORE control message
                msg = Message(
                    src_node=src_node,
                    dst_node=node_id,
                    data=data,
                    data_name=data.name,
                    size=data.size,
                    message_type=MessageType.STORE,
                )
                core.send_message(msg)

    def delete_data(
        self, core: 'Core', node: str, data_name: str, version: Optional[int] = None
    ) -> None:
        node_obj = core.topology.get_node(node)
        try:
            node_obj.delete_data(name=data_name, version=version)
        except KeyError as e:
            print(f"[DELETE-FAIL] {e}")
