# analyzer.py
from __future__ import annotations
import math
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd


class Analyzer:
    """
    Lightweight results analyzer for your simulator logs.

    Expected CSVs (written by SimulationLogger):
      - <base>.csv        main event stream
          columns (typical):
            timestamp, event_type, msg_type, msg_id, msg/data_name,
            src_service, dst_service, src_node, dst_node, application
      - failures.csv      (optional) node failure/repair events
          columns (typical):
            timestamp, event, node_id     # event in {NODE_DOWN, NODE_UP}

    Derived timing metrics (if the events exist in your log):
      - time_latency       = RECV.ts       - SEND.ts
      - time_wait          = PROC_START.ts - RECV.ts
      - time_service       = PROC_END.ts   - PROC_START.ts
      - time_response      = PROC_END.ts   - RECV.ts
      - time_total_response= time_latency + time_response
    """

    def __init__(self, base_path: str = "result"):
        self.base_path = Path(base_path)

        # Main event log
        self.df = pd.read_csv(f"{self.base_path}.csv")
        if "timestamp" in self.df:
            self.df["timestamp"] = pd.to_numeric(self.df["timestamp"], errors="coerce")
        if "msg_id" in self.df:
            # allow missing msg_id for some control events
            self.df["msg_id"] = pd.to_numeric(self.df["msg_id"], errors="coerce").astype("Int64")

        # Optional failures
        self.df_fail = pd.DataFrame()
        if Path("failures.csv").exists():
            self.df_fail = pd.read_csv("failures.csv")
            if "timestamp" in self.df_fail:
                self.df_fail["timestamp"] = pd.to_numeric(self.df_fail["timestamp"], errors="coerce")

        # cache
        self._times_df: Optional[pd.DataFrame] = None

    # -------------------- Basics --------------------
    def count_messages(self, event: Optional[str] = None) -> int:
        """Number of rows; optionally only those with a specific event_type."""
        if event is None:
            return int(len(self.df))
        return int((self.df["event_type"] == event).sum())

    def drop_rate(self) -> float:
        """DROP / SEND ratio (NaN if no SEND)."""
        sends = (self.df["event_type"] == "SEND").sum()
        drops = (self.df["event_type"] == "DROP").sum()
        return float("nan") if sends == 0 else drops / sends

    def bytes_transmitted(
        self,
        size_map: Optional[Dict[str, int] | Callable[[pd.Series], int]] = None,
        use_event: str = "SEND",
        include_types: Iterable[str] = ("APP", "STORE", "CONS_REQ", "CONS_RESP"),
    ) -> float:
        """
        Sum bytes across events. Since size isn't logged, supply:
          - size_map: dict keyed by 'msg/data_name' OR a callable(row)->bytes.
        """
        df = self.df[(self.df["event_type"] == use_event) & (self.df["msg_type"].isin(include_types))]
        if df.empty or size_map is None:
            return float("nan")
        if callable(size_map):
            sizes = df.apply(size_map, axis=1).astype(float)
        else:
            sizes = df["msg/data_name"].map(size_map).astype(float)
        return float(np.nansum(sizes))

    # -------------------- Per-message timings --------------------
    def compute_times_df(self) -> pd.DataFrame:
        """
        Build a per-message dataframe (one row per msg_id) with derived times.
        Uses first occurrence of each event per msg_id.
        """
        if self._times_df is not None:
            return self._times_df

        meta_cols = [
            "application",
            "msg_type",
            "msg/data_name",
            "src_service",
            "dst_service",
            "src_node",
            "dst_node",
        ]

        def first_event(event_name: str, col_out: str) -> pd.DataFrame:
            if "msg_id" not in self.df.columns:
                return pd.DataFrame(columns=["msg_id", col_out]).set_index("msg_id")
            fr = (
                self.df[self.df["event_type"] == event_name][["msg_id", "timestamp"]]
                .dropna(subset=["msg_id"])
                .drop_duplicates(subset=["msg_id"])
                .rename(columns={"timestamp": col_out})
                .set_index("msg_id")
            )
            return fr

        send_meta = (
            self.df[self.df["event_type"] == "SEND"][["msg_id", "timestamp"] + meta_cols]
            .dropna(subset=["msg_id"])
            .drop_duplicates(subset=["msg_id"])
            .rename(columns={"timestamp": "time_emit"})
            .set_index("msg_id")
        )

        recv = first_event("RECV", "time_reception")
        pstart = first_event("PROC_START", "time_proc_start")
        pend = first_event("PROC_END", "time_proc_end")

        tdf = send_meta.join(recv, how="left").join(pstart, how="left").join(pend, how="left")

        # Derivations
        tdf["time_latency"] = tdf["time_reception"] - tdf["time_emit"]
        tdf["time_wait"] = tdf["time_proc_start"] - tdf["time_reception"]
        tdf["time_service"] = tdf["time_proc_end"] - tdf["time_proc_start"]
        tdf["time_response"] = tdf["time_proc_end"] - tdf["time_reception"]
        tdf["time_total_response"] = tdf["time_latency"] + tdf["time_response"]

        self._times_df = tdf.reset_index()
        return self._times_df

    def times(
        self,
        column: str,
        agg: str | Callable = "mean",
        groupby: Iterable[str] = ("msg/data_name",),
    ) -> pd.DataFrame:
        """
        Aggregate a timing column over group keys.
        column ∈ {time_latency, time_wait, time_service, time_response, time_total_response}
        """
        tdf = self.compute_times_df()
        return tdf.groupby(list(groupby)).agg({column: agg})

    def average_loop_response(self, loops: List[List[str]]) -> List[float]:
        """
        Each loop is a list of logical message names (values of 'msg/data_name').
        For each loop, sum the mean time_total_response of its elements.
        """
        tdf = self.compute_times_df()
        means = tdf.groupby("msg/data_name")["time_total_response"].mean()
        results = []
        for loop in loops:
            total = 0.0
            for name in loop:
                total += float(means.get(name, 0.0))
            results.append(total)
        return results

    # -------------------- Utilization & throughput --------------------
    def utilization(self, id_entity: Any, total_time: Optional[float], by: str = "dst_node") -> float:
        """
        Approximate utilization as Σ(service_time where <by>==id) / total_time.
        by ∈ {"dst_node", "dst_service"}.
        """
        tdf = self.compute_times_df()
        if total_time is None:
            total_time = float(self.df["timestamp"].max() - self.df["timestamp"].min())
        busy = float(np.nansum(tdf.loc[tdf[by] == id_entity, "time_service"]))
        return 0.0 if total_time <= 0 else busy / total_time

    def service_utilization(self, service: str, total_time: float) -> pd.DataFrame:
        """
        Utilization (%) of a given service, broken down by destination node.
        """
        tdf = self.compute_times_df()
        g = (
            tdf[tdf["dst_service"] == service]
            .groupby(["dst_node"])["time_service"]
            .sum()
            .reset_index()
        )
        g["utilization_pct"] = 100.0 * g["time_service"] / (total_time if total_time > 0 else np.nan)
        return g[["dst_node", "utilization_pct"]].sort_values("utilization_pct", ascending=False)

    def throughput(self, window_s: float = 1.0, event: str = "RECV") -> pd.DataFrame:
        """
        Messages per window_s seconds (default uses RECV timestamps).
        Returns columns: t_start, t_end, count
        """
        df = self.df[self.df["event_type"] == event][["timestamp"]].sort_values("timestamp").copy()
        if df.empty:
            return pd.DataFrame(columns=["t_start", "t_end", "count"])
        t0 = float(df["timestamp"].min())
        tf = float(df["timestamp"].max())
        bins = np.arange(t0, tf + window_s, window_s)
        labels = bins[:-1]
        df["bin"] = pd.cut(df["timestamp"], bins=bins, labels=labels, right=False)
        grp = df.groupby("bin").size().reindex(labels, fill_value=0).reset_index()
        grp.columns = ["t_start", "count"]
        grp["t_start"] = grp["t_start"].astype(float)
        grp["t_end"] = grp["t_start"] + window_s
        return grp[["t_start", "t_end", "count"]]
    

    def data_only(self): 
        filtered_df = self.df[self.df["msg_type"].isin(["STORE", "CONS_REQ", "CONS_RESP"])]

        # Save to a new CSV
        filtered_df.to_csv("data_only.csv", index=False)

    # -------------------- Data fetch latency (CONS_REQ -> CONS_RESP) --------------------
    def data_fetch_latencies(self) -> pd.DataFrame:
        """
        Pair each CONS_REQ with the first subsequent CONS_RESP carrying the same data
        from the storage node back to the requester node.
        Returns: data_name, requester, storage, t_req, t_resp, latency
        """
        req = self.df[self.df["msg_type"] == "CONS_REQ"].copy()
        resp = self.df[self.df["msg_type"] == "CONS_RESP"].copy()
        if req.empty or resp.empty:
            return pd.DataFrame(columns=["data_name", "requester", "storage", "t_req", "t_resp", "latency"])

        req = req.sort_values("timestamp")
        resp = resp.sort_values("timestamp")

        # index responses by (data_name, requester, storage)
        resp_idx: Dict[tuple, List[pd.Series]] = {}
        for _, r in resp.iterrows():
            key = (r["msg/data_name"], r["dst_node"], r["src_node"])  # storage -> requester in RESP
            resp_idx.setdefault(key, []).append(r)

        rows = []
        for _, q in req.iterrows():
            key = (q["msg/data_name"], q["src_node"], q["dst_node"])  # requester -> storage in REQ
            candidates = resp_idx.get(key, [])
            cand = next((r for r in candidates if r["timestamp"] >= q["timestamp"]), None)
            if cand is not None:
                rows.append(
                    {
                        "data_name": q["msg/data_name"],
                        "requester": q["src_node"],
                        "storage": q["dst_node"],
                        "t_req": float(q["timestamp"]),
                        "t_resp": float(cand["timestamp"]),
                        "latency": float(cand["timestamp"] - q["timestamp"]),
                    }
                )
        return pd.DataFrame(rows)

    # -------------------- Failures --------------------
    def failures_summary(self, total_time: Optional[float] = None) -> pd.DataFrame:
        """
        For each node: number of downs, total downtime, and crude MTBF/MTTR.
        If total_time is None, it's inferred from the main log span.
        """
        if self.df_fail.empty:
            return pd.DataFrame(columns=["node_id", "num_downs", "total_downtime", "MTBF", "MTTR"])

        if total_time is None:
            total_time = float(self.df["timestamp"].max() - self.df["timestamp"].min())

        df = self.df_fail.sort_values("timestamp")
        rows = []
        for nid in df["node_id"].unique().tolist():
            d = df[df["node_id"] == nid]
            downs = d[d["event"] == "NODE_DOWN"]["timestamp"].tolist()
            ups = d[d["event"] == "NODE_UP"]["timestamp"].tolist()

            # pair downs -> ups (in order); if last down has no up, count until end
            pairs = []
            i = j = 0
            while i < len(downs):
                t_down = float(downs[i])
                if j < len(ups) and float(ups[j]) >= t_down:
                    t_up = float(ups[j])
                    j += 1
                else:
                    t_up = None
                pairs.append((t_down, t_up))
                i += 1

            downtime = 0.0
            for td, tu in pairs:
                if tu is None:
                    # fall back to end of main log span
                    tu = float(self.df["timestamp"].max())
                downtime += max(0.0, tu - td)

            num = len(pairs)
            mttr = (downtime / num) if num > 0 else float("nan")
            uptime = max(0.0, float(total_time) - downtime)
            mtbf = (uptime / num) if num > 0 else float("nan")

            rows.append(
                {
                    "node_id": nid,
                    "num_downs": int(num),
                    "total_downtime": downtime,
                    "MTBF": mtbf,
                    "MTTR": mttr,
                }
            )
        out = pd.DataFrame(rows)
        return out.sort_values(["total_downtime", "num_downs"], ascending=[False, False]).reset_index(drop=True)

    # -------------------- Pretty printers --------------------
    def show_loops(self, loops: List[List[str]]) -> List[float]:
        vals = self.average_loop_response(loops)
        for i, loop in enumerate(loops):
            print(f"\t{i} - {loop} :\t {vals[i]:.6f}")
        return vals

    def show_results(self, total_time: Optional[float] = None, loops: Optional[List[List[str]]] = None):
        if total_time is None:
            total_time = float(self.df["timestamp"].max() - self.df["timestamp"].min())
        print(f"\tSimulation Time: {total_time:.2f}")

        if loops:
            print("\tApplication loops delays:")
            self.show_loops(loops)

        sends = self.count_messages("SEND")
        drops = self.count_messages("DROP")
        rate = self.drop_rate()
        rate_str = f"{rate:.4f}" if not (rate is None or math.isnan(rate)) else "NaN"
        print("\tDrops / Sends:")
        print(f"\t\tSends={sends} Drops={drops} DropRate={rate_str}")

        # Top node utilizations (approx.)
        tdf = self.compute_times_df()
        if not tdf.empty:
            util_nodes = (
                tdf.groupby("dst_node")["time_service"].sum().sort_values(ascending=False)
                / (total_time if total_time > 0 else np.nan)
            ).head(5)
            print("\tTop node utilizations (approx.):")
            for nid, u in util_nodes.items():
                if pd.isna(u):
                    continue
                print(f"\t\t{nid}: {100*u:.2f}%")

        # Failures
        fs = self.failures_summary(total_time=total_time)
        if not fs.empty:
            print("\tFailures summary (top 5 by downtime):")
            for _, r in fs.head(5).iterrows():
                print(
                    f"\t\t{r['node_id']}: downs={int(r['num_downs'])} "
                    f"downtime={r['total_downtime']:.3f}s MTTR={r['MTTR']:.3f}s MTBF={r['MTBF']:.3f}s"
                )




# after the simulation writes result.csv (and optionally failures.csv)
# an = Analyzer("result")

# 1) Derived timings per message
# times = an.compute_times_df()     # one row per msg_id
# print(times.head())

# # 2) Aggregates
# print(an.times("time_total_response", "mean"))           # by message/data name
# print(an.throughput(window_s=1.0).head())                # msgs/sec

# # 3) Utilization
# print("edge1 util:", an.utilization("edge1", total_time=100.0, by="dst_node"))
# print(an.service_utilization("AggregatorService", total_time=100.0))

# 4) Data fetch latencies (CONS_REQ -> CONS_RESP)
# print(an.data_fetch_latencies().head())

# 5) Console summary
# an.show_results(total_time=100.0, loops=[["telemetry","aggregate","commit"]])
