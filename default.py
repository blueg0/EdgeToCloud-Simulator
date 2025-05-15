PROTOCOLS = {
    "wifi_4":        {"BW": 54,      "tx_power": 1.5,      "rx_power": 1,        "idle_power": 0.8,     "sleep_power": 0.1   },
    "wifi_5":        {"BW": 1300,    "tx_power": 2,        "rx_power": 1.5,      "idle_power": 1,       "sleep_power": 0.2   },
    "wifi_6":        {"BW": 2400,    "tx_power": 2.5,      "rx_power": 2,        "idle_power": 1.2,     "sleep_power": 0.3   },
    "ble":           {"BW": 1,       "tx_power": 0.01,     "rx_power": 0.01,     "idle_power": 0.005,   "sleep_power": 0.001 },
    "zigbee":        {"BW": 0.25,    "tx_power": 0.01,     "rx_power": 0.01,     "idle_power": 0.005,   "sleep_power": 0.001 },
    "zwave":         {"BW": 0.1,     "tx_power": 0.02,     "rx_power": 0.015,    "idle_power": 0.007,   "sleep_power": 0.001 },
    "lorawan":       {"BW": 0.05,    "tx_power": 0.01,     "rx_power": 0.01,     "idle_power": 0.005,   "sleep_power": 0.001 },
    "sigfox":        {"BW": 0.006,   "tx_power": 0.005,    "rx_power": 0.005,    "idle_power": 0.002,   "sleep_power": 0.0005},
    "4G":            {"BW": 100,     "tx_power": 1,        "rx_power": 1,        "idle_power": 0.5,     "sleep_power": 0.1   },
    "5G":            {"BW": 10000,   "tx_power": 3,        "rx_power": 2,        "idle_power": 1.5,     "sleep_power": 0.3   },
    "nbiot":         {"BW": 0.25,    "tx_power": 0.1,      "rx_power": 0.1,      "idle_power": 0.05,    "sleep_power": 0.01  },
    "optical_fiber": {"BW": 40000,   "tx_power": 10,       "rx_power": 10,       "idle_power": 5,       "sleep_power": 1     },
    "ethernet":      {"BW": 40000,   "tx_power": 10,       "rx_power": 10,       "idle_power": 5,       "sleep_power": 1     }
}

DEFAULT_MOBILITY = {
    "sensor":     False,
    "actuator":   False,
    "vehicle":    True,
    "smartobject": False,
}