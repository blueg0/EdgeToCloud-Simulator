# dataManager.py
from abc import ABC, abstractmethod
from typing import Any, List,TYPE_CHECKING, Optional
if TYPE_CHECKING:
    from core import Core
    from application import Data
from Topology import StorageFullError
from application import Message, MessageType

class DataManager(ABC):
    def __init__(self):
        self.placement = None        
    @abstractmethod
    def place_data(self, core : 'Core', data: 'Data', src_node) -> None:
        """Place replicas of `data` on the given `nodes`."""
    
    @abstractmethod
    def delete_data(self, data_name: str, node: Any) -> None:
        """Delete the replica of `data_name` from `node`."""
    
    @abstractmethod
    def tranfer_data(self, data_name: str) -> None:
        """transfer a replica of `data_name` from `src_node` to `dst_node`."""



        

class DefaultDataManager(DataManager):
    
    def __init__(self):
        super().__init__()
        
    def set_placement(self,placement) : 
        #for each data the nodes where it should be stored 
        self.placement = placement

    def place_data(self,core: 'Core', data: 'Data', src_node):
        for node_id in self.placement[data.name] : 
            if node_id == src_node :
                node_obj = core.topology.get_node(node_id)
                try:
                    node_obj.store_data(data)
                    
                    core.data_locations[data.name][src_node] = data.version
                except StorageFullError as e:
                    print(f"[STORE-FAIL] {e}")
            else : 
                msg = Message(
                    src_node=src_node,
                    dst_node= node_id, 
                    data=data, 
                    data_name=data.name,
                    size=data.size,
                    message_type= MessageType.STORE)
                
                core.send_message(msg)

        return 
    
    def delete_data(self, core: 'Core', node: Any, data_name: str, version : Optional[int]= None ) -> None:
        node_obj = core.topology.get_node(node)
        try: 
            node_obj.delete_data(name=data_name, version=version )
        except KeyError as e : 
            print(f"[DELETE-FAIL] {e}")
        

    def tranfer_data(self, data_name, src_node, dst_node):
        raise NotImplementedError("transfer_data() not implemented yet")


