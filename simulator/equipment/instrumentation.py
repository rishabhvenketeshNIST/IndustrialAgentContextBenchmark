"""Instrumentation layer: transmitters between the TEP process and its users.

TEP computes XMEAS (including the measurement noise defined in teprob.f). In a
plant, the control system and people see transmitter outputs. Sensor faults
(bias, drift, dropout, stuck) are applied here to produce the *transmitted*
value. The native controllers receive the transmitted values (the adapter swaps
them into /PV/ only for the duration of the CONTRLn calls), so a biased sensor
causes the real closed-loop consequence while the TEP physics and the true
XMEAS are never overwritten.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from ..tep.catalog import NUM_XMEAS


@dataclass
class SensorOverlay:
    fault_id: str
    kind: str                  # bias | drift | dropout | stuck
    magnitude: float = 0.0     # bias offset (engineering units) or drift rate (units per hour)
    start_s: int = 0
    intensity: float = 1.0     # 0..1 progression multiplier from the fault engine
    held_value: Optional[float] = None


@dataclass
class Instrumentation:
    clock: object
    overlays: Dict[int, List[SensorOverlay]] = field(default_factory=dict)
    last_output: np.ndarray = field(default_factory=lambda: np.full(NUM_XMEAS, np.nan))
    quality: List[str] = field(default_factory=lambda: ["GOOD"] * NUM_XMEAS)

    def reset(self) -> None:
        self.overlays.clear()
        self.last_output = np.full(NUM_XMEAS, np.nan)
        self.quality = ["GOOD"] * NUM_XMEAS

    def add(self, xmeas_index: int, overlay: SensorOverlay) -> None:
        if not 1 <= xmeas_index <= NUM_XMEAS:
            raise ValueError(f"XMEAS index must be 1..{NUM_XMEAS}")
        if overlay.kind not in ("bias", "drift", "dropout", "stuck"):
            raise ValueError(f"Unknown sensor overlay kind '{overlay.kind}'")
        if overlay.kind in ("dropout", "stuck") and not np.isnan(self.last_output[xmeas_index - 1]):
            overlay.held_value = float(self.last_output[xmeas_index - 1])
        self.overlays.setdefault(xmeas_index, []).append(overlay)

    def remove_fault(self, fault_id: str) -> List[int]:
        affected = []
        for idx in list(self.overlays):
            before = len(self.overlays[idx])
            self.overlays[idx] = [o for o in self.overlays[idx] if o.fault_id != fault_id]
            if len(self.overlays[idx]) != before:
                affected.append(idx)
            if not self.overlays[idx]:
                del self.overlays[idx]
        return affected

    def set_intensity(self, fault_id: str, intensity: float) -> None:
        for lst in self.overlays.values():
            for o in lst:
                if o.fault_id == fault_id:
                    o.intensity = float(intensity)

    def active_channels(self) -> Dict[int, List[str]]:
        return {idx: [o.kind for o in lst] for idx, lst in self.overlays.items()}

    def filter(self, true_xmeas: np.ndarray) -> np.ndarray:
        """Transmitted values for the current simulation time (pure w.r.t. inputs + overlays)."""
        out = np.array(true_xmeas, dtype=float, copy=True)
        t = self.clock.time_s
        for idx, lst in self.overlays.items():
            i = idx - 1
            v = out[i]
            for o in lst:
                if o.kind == "bias":
                    v = v + o.magnitude * o.intensity   # a biased transmitter still reports GOOD
                elif o.kind == "drift":
                    v = v + o.magnitude * max(0, t - o.start_s) / 3600.0 * o.intensity
                elif o.kind in ("dropout", "stuck"):
                    if o.held_value is None:
                        o.held_value = float(v)
                    v = o.held_value   # hold last value; dropout is flagged BAD in observe()
            out[i] = v
        return out

    def observe(self, true_xmeas: np.ndarray) -> np.ndarray:
        """Compute transmitted values and remember them (for hold-last-value semantics)."""
        out = self.filter(true_xmeas)
        q = ["GOOD"] * NUM_XMEAS
        for idx, lst in self.overlays.items():
            if any(o.kind == "dropout" for o in lst):
                q[idx - 1] = "BAD"
        self.quality = q
        self.last_output = out.copy()
        return out
