"""Operator actions: the HMI gateway.

Every operator action is validated, executed through the command registry
(handlers contributed by the owning modules) and recorded as an
OPERATOR_ACTION event. Scenarios may schedule operator actions for fully
deterministic, scripted runs.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List

from ..common import ConfigError
from ..events import EventType
from ..simulation.module import ModuleContext, SimulationModule

Command = Callable[..., Any]


class OperatorModule(SimulationModule):
    name = "operator"

    def __init__(self) -> None:
        self.commands: Dict[str, Command] = {}

    def register(self, name: str, handler: Command) -> None:
        self.commands[name] = handler

    def setup(self, ctx: ModuleContext) -> None:
        super().setup(ctx)
        self._scheduled: List[dict] = []
        for i, a in enumerate(ctx.config.get("operator_actions", []) or []):
            if "time" not in a or "action" not in a:
                raise ConfigError(f"operator action needs 'time' and 'action': {a}")
            if a["action"] not in self.commands:
                raise ConfigError(f"Unknown operator action '{a['action']}'. Known: {sorted(self.commands)}")
            self._scheduled.append({"seq": i, **a})
        self._scheduled.sort(key=lambda a: (int(a["time"]), a["seq"]))
        ctx.state.collection("operator_log")

    def execute(self, action: str, params: Dict[str, Any], actor: str = "operator") -> Any:
        if action not in self.commands:
            raise ValueError(f"Unknown operator action '{action}'. Known: {sorted(self.commands)}")
        params = dict(params or {})
        result = self.commands[action](actor=actor, **params)
        log = self.ctx.state.collection("operator_log")
        entry = {"t": self.ctx.clock.time_s, "actor": actor, "action": action, "params": params}
        log[f"OP-{len(log) + 1:05d}"] = entry
        target = next((params[k] for k in ("target", "asset_id", "order_id", "alarm_id", "storage_id", "wo_id")
                       if params.get(k)), None)
        if target is None and params.get("loop_id") is not None:
            target = self.ctx.tep_mapping.loops.get(int(params["loop_id"]))
        self.ctx.bus.publish(EventType.OPERATOR_ACTION, actor, target, {"action": action, "params": params})
        return result

    def pre_step(self, t: int) -> None:
        while self._scheduled and int(self._scheduled[0]["time"]) <= t:
            a = self._scheduled.pop(0)
            params = {k: v for k, v in a.items() if k not in ("seq", "time", "action", "actor")}
            self.execute(a["action"], params, actor=a.get("actor", "scripted_operator"))
