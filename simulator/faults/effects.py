"""Fault effect channels - the boundary between cause and consequence.

The fault engine (benchmark side) only ever writes *cause* contributions here,
e.g. ``("WU-CWP-101A", "damage") <- {"F-COOL-001": 0.21}``. The module that
owns the target (equipment, utilities, inventory, maintenance, quality, ...)
reads the combined value and decides the consequences. This keeps fault
injection out of every domain module's internal state.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# How contributions of several simultaneous faults combine, per channel.
COMBINE = {
    "damage": "sum",                 # equipment health loss (0..1)
    "efficiency_loss": "complement",  # 1 - prod(1 - x)
    "failed": "any",
    "capacity_loss": "complement",    # utility capacity fraction lost
    "thermal_degradation": "complement",
    "supply_disruption": "any",       # supplier cannot deliver
    "stock_loss": "max",              # fraction of on-hand stock rejected/lost
    "response_delay_s": "max",
    "stock_blocked": "any",           # spare part unavailable
    "release_delay_s": "max",
    "blocked": "any",
    "test_offset": "sum",
    "value": "sum",
}


class FaultEffects:
    def __init__(self) -> None:
        self._data: Dict[Tuple[str, str], Dict[str, float]] = {}

    def reset(self) -> None:
        self._data.clear()

    def set(self, target: str, channel: str, fault_id: str, value: float) -> None:
        if channel not in COMBINE:
            raise ValueError(f"Unknown fault effect channel '{channel}'")
        self._data.setdefault((target, channel), {})[fault_id] = float(value)

    def clear_fault(self, fault_id: str) -> List[Tuple[str, str]]:
        touched = []
        for key in list(self._data):
            if fault_id in self._data[key]:
                del self._data[key][fault_id]
                touched.append(key)
                if not self._data[key]:
                    del self._data[key]
        return touched

    def clear_target(self, target: str, channels=None) -> List[str]:
        """Remove all contributions on a target (e.g. after repair). Returns fault ids affected."""
        faults = []
        for key in list(self._data):
            if key[0] == target and (channels is None or key[1] in channels):
                faults.extend(self._data[key].keys())
                del self._data[key]
        return sorted(set(faults))

    def value(self, target: str, channel: str, default: float = 0.0) -> float:
        contrib = self._data.get((target, channel))
        if not contrib:
            return default
        mode = COMBINE[channel]
        vals = [contrib[k] for k in sorted(contrib)]
        if mode == "sum":
            return float(sum(vals))
        if mode == "max":
            return float(max(vals))
        if mode == "any":
            return 1.0 if any(v != 0 for v in vals) else 0.0
        prod = 1.0
        for v in vals:
            prod *= (1.0 - min(max(v, 0.0), 1.0))
        return 1.0 - prod

    def contributions(self, target: str, channel: str) -> Dict[str, float]:
        return dict(self._data.get((target, channel), {}))

    def to_dict(self) -> dict:
        return {f"{t}:{c}": dict(v) for (t, c), v in sorted(self._data.items())}
