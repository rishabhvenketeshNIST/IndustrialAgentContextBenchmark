"""Deterministic simulation clock.

Simulation time is an integer number of seconds (the TEP integration step is
one second, temain_mod.f DELTAT). Timestamps are derived from a configured
simulation start instant - never from the wall clock - so event traces are
reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..common import fmt_hms, iso, parse_datetime, sim_timestamp

SPEED_PRESETS = (0.1, 0.5, 1.0, 5.0, 10.0, 50.0, 100.0, 500.0, 1000.0)
MAX_SPEED = 5000.0


@dataclass
class SimulationClock:
    start: datetime
    time_s: int = 0
    step_s: int = 1

    @classmethod
    def from_config(cls, start: str) -> "SimulationClock":
        return cls(start=parse_datetime(start))

    def advance(self, n: int = 1) -> int:
        if n < 0:
            raise ValueError("clock cannot move backwards")
        self.time_s += n * self.step_s
        return self.time_s

    def reset(self) -> None:
        self.time_s = 0

    @property
    def time_h(self) -> float:
        return self.time_s / 3600.0

    def timestamp(self, sim_seconds: Optional[float] = None) -> str:
        return sim_timestamp(self.start, self.time_s if sim_seconds is None else sim_seconds)

    def to_dict(self) -> dict:
        return {"simulation_start": iso(self.start), "time_s": self.time_s, "time_h": self.time_h,
                "time_hms": fmt_hms(self.time_s), "timestamp": self.timestamp()}


def validate_speed(speed: float) -> float:
    speed = float(speed)
    if not (0.01 <= speed <= MAX_SPEED):
        raise ValueError(f"speed must be between 0.01 and {MAX_SPEED}")
    return speed
