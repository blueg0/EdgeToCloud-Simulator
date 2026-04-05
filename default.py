
import random
from enum import Enum
from typing import Dict


class NodeGroup(Enum):
    SENSOR = "sensor"
    ACTUATOR = "actuator"
    SMART_OBJECT = "smartobject"
    EDGE_NODE = "edge"
    FOG_NODE = "fog"
    CLOUD = "cloud"

    
def generate_default_resources(group: 'NodeGroup') -> Dict[str, int]:
    """
    Generate default resources for a node based on its NodeGroup.

    Units:
        - cpu: MIPS (Million Instructions Per Second)
        - memory: MB (Megabytes)
        - storage: GB (Gigabytes)
    """
    if group in (NodeGroup.SENSOR, NodeGroup.ACTUATOR):
        return {}

    choices = {
        NodeGroup.SMART_OBJECT: {
            'cpu': [2**i for i in range(11, 14)],   # 2048–8192 MIPS
            'memory': [2**i for i in range(12, 15)],   # 4096–16384 MB
            'storage': [2**i for i in range(5, 9)]     # 32–256 GB
        },
        NodeGroup.EDGE_NODE: {
            'cpu': [2**i for i in range(12, 14)],   # 4096–16384 MIPS
            'memory': [2**i for i in range(12, 16)],   # 4096–65536 MB
            'storage': [2**i for i in range(7, 10)]    # 128–1024 GB
        },
        NodeGroup.FOG_NODE: {
            'cpu': [2**i for i in range(13, 16)],   # 8192–32768 MIPS
            'memory': [2**i for i in range(13, 17)],   # 8192–65536 MB
            'storage': [2**i for i in range(8, 12)]    # 256–2048 GB
        },
        NodeGroup.CLOUD: {
            'cpu': [2**i for i in range(14, 17)],   # 16384–65536 MIPS
            'memory': [2**i for i in range(16, 20)],   # 65536–524288 MB
            'storage': [2**i for i in range(10, 14)]   # 1024–16384 GB
        }
    }

    if group not in choices:
        raise ValueError(f"Unsupported NodeGroup for resource generation: {group}")

    group_choices = choices[group]
    return {
        key: random.choice(vals)
        for key, vals in group_choices.items()
    }


C = 3e8  

PROTOCOLS = {
    "wifi_4":        {"BW": 50,      "prop_speed": C/1.0003,  "tx_power": 1.5,   "rx_power": 1,     "idle_power": 0.8,    "sleep_power": 0.1,     },
    "wifi_5":        {"BW": 1300,    "prop_speed": C/1.0003,  "tx_power": 2,     "rx_power": 1.5,   "idle_power": 1,      "sleep_power": 0.2,     },
    "wifi_6":        {"BW": 2400,    "prop_speed": C/1.0003,  "tx_power": 2.5,   "rx_power": 2,     "idle_power": 1.2,    "sleep_power": 0.3,     },
    "ble":           {"BW": 1,       "prop_speed": C/1.0003,  "tx_power": 0.01,  "rx_power": 0.01,  "idle_power": 0.005,  "sleep_power": 0.001,   },
    "zigbee":        {"BW": 0.25,    "prop_speed": C/1.0003,  "tx_power": 0.01,  "rx_power": 0.01,  "idle_power": 0.005,  "sleep_power": 0.001,   },
    "zwave":         {"BW": 0.1,     "prop_speed": C/1.0003,  "tx_power": 0.02,  "rx_power": 0.015, "idle_power": 0.007,  "sleep_power": 0.001,   },
    "lorawan":       {"BW": 0.05,    "prop_speed": C/1.0003,  "tx_power": 0.01,  "rx_power": 0.01,  "idle_power": 0.005,  "sleep_power": 0.001,   },
    "sigfox":        {"BW": 0.006,   "prop_speed": C/1.0003,  "tx_power": 0.005, "rx_power": 0.005, "idle_power": 0.002,  "sleep_power": 0.0005,  },
    "4G":            {"BW": 100,     "prop_speed": C/1.0003,  "tx_power": 1,     "rx_power": 1,     "idle_power": 0.5,    "sleep_power": 0.1,     },
    "5G":            {"BW": 10000,   "prop_speed": C/1.0003,  "tx_power": 3,     "rx_power": 2,     "idle_power": 1.5,    "sleep_power": 0.3,     },
    "nbiot":         {"BW": 0.25,    "prop_speed": C/1.0003,  "tx_power": 0.1,   "rx_power": 0.1,   "idle_power": 0.05,   "sleep_power": 0.01,    },
    "optical_fiber": {"BW": 400,     "prop_speed": C/1.5,     "tx_power": 10,    "rx_power": 10,    "idle_power": 5,      "sleep_power": 1,       },
    "ethernet":      {"BW": 400,     "prop_speed": 0.67 *C,   "tx_power": 10,    "rx_power": 10,    "idle_power": 5,      "sleep_power": 1,       },
}