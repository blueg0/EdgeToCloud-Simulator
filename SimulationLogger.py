import csv

class CSVLogger:
    """
    Simple CSV logger for simulation metrics.
    Writes each row immediately to the specified CSV file.
    """
    def __init__(self, filepath: str= None):
        path = "result"
        if  filepath is not None:
            path = filepath
        self._file = open("%s.csv" % path, mode='w', newline='')
        self._writer = csv.DictWriter(
            self._file,
            fieldnames=[
                "timestamp",
                "event_type",   # SEND, RECV, WAIT, GOT_DATA, PROC_START, PROC_END, DROP...
                "msg_type",     # APP, CONS_REQ, CONS_RESP, STORE, etc
                "msg_id",
                "msg/data_name",     # link.name or "DATA"
                "src_service",
                "dst_service",
                "src_node",
                "dst_node",
                "application",
            ]
        )
        self._writer.writeheader()

    def log(self, row: dict):
        """
        Append a single event record (as a dict) to the CSV.
        Example row keys: timestamp, msg_id, event_type, src_service, ...
        """
        self._writer.writerow(row)

    def close(self):
        """Flush and close the CSV file."""
        self._file.close()