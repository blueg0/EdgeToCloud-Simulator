from abc import ABC, abstractmethod
from typing import Any, Dict, List, TYPE_CHECKING
if TYPE_CHECKING:
    from core import Core 
    from application import Application    
import simpy


class ServicesManager(ABC) : 
    

       
    
    @abstractmethod
    def deploy_services(self, core: 'Core', app: 'Application'):    
        """
        Deploy services as specified in the placement dictionary.
        { service_name: [node_id, ...], ... }
        """
        pass


    @abstractmethod
    def migrate_services(self):
        """
        Migrate services according to the migration plan:
        { service_name: {'from': [...], 'to': [...]}, ... }
        """
        pass


    @abstractmethod
    def scale_services(self):
        """
        Scale out services by deploying them to additional nodes.
        { service_name: [node_ids], ... }
        """
        pass
    @abstractmethod
    def delete_services(self):
        """
        Remove specified services from specified nodes.
        { service_name: [node_ids], ... }
        """
        pass


class DictDeployment(ServicesManager):
    """
    Knows how to place & (for generators) start services for a
    *single* Application in a given Core.
    """

    def __init__(self, placement):
        super().__init__()
        self.placement =  placement

    def deploy_services(self,app: 'Application', core: 'Core'):
        """
        Given a single dict { service_name: [node_id, ...], ... }
        place *and* start generators, and place processors.
        """
        

        for svc_name, nodes in self.placement.items():
            if svc_name not in app.services:
                raise KeyError(f"{svc_name!r} not in application {app.name !r}")
            svc = app.services[svc_name]

            # record placement for everyone
            
            core.placement[(app.name, svc_name)] = nodes
            from application import GenerationService
            # if it’s a generator, also kick off generate() on each node
            if isinstance(svc, GenerationService):
                for node in nodes:
                    proc =  core.env.process(
                                svc.generate(core=core, env=core.env, node_id=node)
                            )
                    key = (app, svc, node)
                    core._service_procs.setdefault(key, []).append(proc)
                    core.env.process(core._watch_and_prune(proc, key))



    def delete_services(self, app: 'Application', core: 'Core', plan: Dict[str, List[Any]]) -> None:
        """
        Undeploy the given services per-node, aborting any in-flight work.
        plan: { service_name: [node_id, ...], ... }
        """
        for svc_name, nodes in plan.items():
            # 1) sanity check
            if svc_name not in app.services:
                raise KeyError(f"{svc_name!r} not in application {app.name!r}")

            # 2) current placement list
            key = (app.name, svc_name)
            placed = core.placement.get(key, [])

            for node_id in nodes:
                if node_id not in placed:
                    continue

                # 3) remove from routing/placement
                placed.remove(node_id)
                core.placement[key] = placed
                print(f"[SERVICE-REMOVED] '{svc_name}' @ {node_id}")

                # 4) abort any SimPy Processes (both processors & generators)
                core.abort_service_procs(app.name, svc_name, node_id)
               


    def migrate_services(self, plan: Dict[str, Any]):
        raise NotImplementedError("migrate_services() not implemented yet")

    def scale_services(self, plan: Dict[str, Any]):
        raise NotImplementedError("scale_services() not implemented yet")



