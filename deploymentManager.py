from typing import Any, List
import simpy

class DeploymentManager:
    """
    Knows how to place & (for generators) start services for a
    *single* Application in a given Core.
    """
    def __init__(self, core, app_name: str):
        self.core     = core
        self.app_name = app_name

    def deploy_generator(self, service_name: str, nodes: List[Any]):
        """
        Place a GenerationService of self.app_name on `nodes`
        and spin up its generate() loops immediately.
        """
        # record placement
        self.core.place_service(self.app_name, service_name, nodes)

        # fetch the service instance
        svc = self.core.apps[self.app_name].services[service_name]
        for node in nodes:
            svc.node_id = node
            # start its SimPy process
            self.core.env.process(
                svc.generate(core=self.core, env=self.core.env)
            )

    def deploy_processor(self, service_name: str, nodes: List[Any]):
        """
        Place a ProcessingServices of self.app_name on `nodes`.
        It will automatically react when messages arrive.
        """
        self.core.place_service(self.app_name, service_name, nodes)