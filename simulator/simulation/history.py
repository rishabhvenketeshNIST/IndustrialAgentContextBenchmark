"""Bounded in-memory trend buffer for visualisation (explicitly NOT a historian).

A fixed-capacity ring buffer sampled every ``interval_s`` simulated seconds.
It is cleared on reset and exists only in memory; data survives a run only if
the user exports it.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from ..tep import catalog
from ..tep import control_scheme as cs


class TrendBuffer:
    def __init__(self, interval_s: int = 10, capacity: int = 20000) -> None:
        if interval_s < 1 or capacity < 10:
            raise ValueError("invalid trend buffer configuration")
        self.interval_s = int(interval_s)
        self.capacity = int(capacity)
        self._getters: List[Tuple[str, Callable[[], float]]] = []
        self._index: Dict[str, int] = {}
        self._data: Optional[np.ndarray] = None
        self._time = np.zeros(self.capacity, dtype=np.int64)
        self._n = 0          # total samples written
        self.meta: Dict[str, dict] = {}

    def define(self, name: str, getter: Callable[[], float], group: str, label: str, unit: str = "") -> None:
        if name in self._index:
            raise ValueError(f"duplicate trend series {name}")
        self._index[name] = len(self._getters)
        self._getters.append((name, getter))
        self.meta[name] = {"name": name, "group": group, "label": label, "unit": unit}

    def build_default(self, state, assets: List[str], utilities: List[str], storages: List[str]) -> None:
        p = state.process
        for i, v in enumerate(catalog.XMEAS):
            self.define(f"XMEAS({i + 1})", (lambda i=i: p.xmeas[i]), "XMEAS", v.name, v.unit)
        for i, v in enumerate(catalog.XMEAS):
            self.define(f"TRUE:XMEAS({i + 1})", (lambda i=i: p.xmeas_true[i]), "XMEAS (true)", v.name, v.unit)
        for i, v in enumerate(catalog.XMV):
            self.define(f"XMV({i + 1})", (lambda i=i: p.xmv[i]), "XMV", v.name, "%")
        for lid, d in cs.LOOPS.items():
            self.define(f"SP:{d.tag}", (lambda lid=lid: p.setpoints.get(lid, np.nan)), "Setpoints",
                        f"{d.tag} setpoint", catalog.XMEAS[d.pv - 1].unit)
        for aid in assets:
            props = state.entity(aid).properties
            self.define(f"{aid}.health", (lambda pr=props: pr.get("health", np.nan)), "Equipment health",
                        f"{state.entity(aid).name} health", "fraction")
            self.define(f"{aid}.efficiency", (lambda pr=props: pr.get("efficiency", np.nan)), "Equipment health",
                        f"{state.entity(aid).name} efficiency", "fraction")
        for uid in utilities:
            props = state.entity(uid).properties
            units = state.entity(uid).units
            for prop in ("available_capacity", "utilization", "flow", "temperature", "pressure", "availability",
                         "capacity_fraction"):
                self.define(f"{uid}.{prop}", (lambda pr=props, k=prop: pr.get(k, np.nan)), "Utilities",
                            f"{state.entity(uid).name} {prop}", units.get(prop, ""))
        for sid in storages:
            props = state.entity(sid).properties
            self.define(f"{sid}.level_pct", (lambda pr=props: pr.get("level_pct", np.nan)), "Inventory",
                        f"{state.entity(sid).name} level", "%")
        prod = state.production
        self.define("production.rate_kg_h", lambda: prod.get("rate_kg_h", np.nan), "Production",
                    "Product rate", "kg/h")
        self._data = np.full((self.capacity, len(self._getters)), np.nan)

    def clear(self) -> None:
        self._n = 0
        if self._data is not None:
            self._data[:] = np.nan

    def record(self, t: int, force: bool = False) -> None:
        if self._data is None or (not force and t % self.interval_s != 0):
            return
        row = self._n % self.capacity
        self._time[row] = t
        vals = self._data[row]
        for j, (_, g) in enumerate(self._getters):
            v = g()
            try:
                vals[j] = float(v) if v is not None else np.nan
            except (TypeError, ValueError):
                vals[j] = np.nan
        self._n += 1

    @property
    def size(self) -> int:
        return min(self._n, self.capacity)

    def series_names(self) -> List[str]:
        return list(self._index)

    def query(self, names: List[str], since: Optional[int] = None, max_points: int = 2000) -> dict:
        n = self.size
        if n == 0 or self._data is None:
            return {"time": [], "series": {nm: [] for nm in names}}
        start = self._n - n
        rows = [(start + k) % self.capacity for k in range(n)]
        t = self._time[rows]
        mask = t >= since if since is not None else np.ones(n, dtype=bool)
        rows = np.asarray(rows)[mask]
        t = t[mask]
        if len(rows) > max_points > 0:
            step = int(np.ceil(len(rows) / max_points))
            rows, t = rows[::step], t[::step]
        out = {}
        for nm in names:
            if nm not in self._index:
                raise KeyError(f"Unknown trend series '{nm}'")
            col = self._data[rows, self._index[nm]]
            out[nm] = [None if not np.isfinite(x) else round(float(x), 6) for x in col]
        return {"time": [int(x) for x in t], "series": out}

    def to_columns(self) -> Tuple[List[int], Dict[str, List[float]]]:
        q = self.query(self.series_names(), max_points=0)
        return q["time"], q["series"]
