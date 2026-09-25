"""Base class and context for enterprise simulation modules.

Modules never hold references to each other. They interact through:
  * the canonical state (``ctx.state``),
  * the event bus (``ctx.bus``) - synchronous and deterministic,
  * the process interface (``ctx.process``) for operator-level control actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, TYPE_CHECKING

from ..common import RandomStreams

if TYPE_CHECKING:  # pragma: no cover
    from ..events import EventBus
    from ..state import CanonicalState
    from .clock import SimulationClock
    from .process_interface import ProcessInterface


@dataclass
class ModuleContext:
    state: "CanonicalState"
    bus: "EventBus"
    clock: "SimulationClock"
    rng: RandomStreams
    config: Dict[str, Any]
    process: "ProcessInterface"
    tep_mapping: Any = None

    def cfg(self, section: str) -> Any:
        return self.config.get(section, {})


class SimulationModule:
    """Lifecycle: setup() once per run, then every simulated second:
    pre_step(t) before the TEP step and post_step(t) after it."""

    name = "module"

    def setup(self, ctx: ModuleContext) -> None:
        self.ctx = ctx

    def pre_step(self, t: int) -> None:
        pass

    def post_step(self, t: int) -> None:
        pass

    def summary(self) -> Dict[str, Any]:
        return {}
