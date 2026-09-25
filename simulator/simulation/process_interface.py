"""Process interface: the enterprise side's single, audited path to the TEP adapter.

* the engine steps the process and synchronises the canonical process image;
* operator actions (setpoints, loop modes, manual outputs) are validated here;
* the coupling engine writes boundary parameters here;
* only the fault engine may toggle TEP-native disturbances (IDV).
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np

from ..equipment.instrumentation import Instrumentation
from ..events import EventBus, EventType
from ..isa95.tep_mapping import TEPMapping
from ..state import CanonicalState
from ..tep import catalog
from ..tep import control_scheme as cs
from ..tep.interface import TEPProcessAdapter

ANALYZER_GROUPS = {"reactor_feed": range(23, 29), "purge": range(29, 37), "product": range(37, 42)}


class ProcessInterface:
    def __init__(self, adapter: TEPProcessAdapter, state: CanonicalState, bus: EventBus,
                 mapping: TEPMapping, instrumentation: Instrumentation) -> None:
        self.adapter = adapter
        self.state = state
        self.bus = bus
        self.mapping = mapping
        self.instrumentation = instrumentation
        self._prev_true: Optional[np.ndarray] = None
        self._boundary_nominal: Dict[str, float] = {}
        self._boundary_cache: Dict[str, float] = {}

    # ---- lifecycle -------------------------------------------------------
    def initialize(self, tep_seed: int, control_mode: str = "CLOSED_LOOP") -> None:
        self.adapter.initialize(tep_seed=tep_seed)
        if control_mode != "CLOSED_LOOP":
            self.adapter.set_control_mode(cs.ControlMode(control_mode))
        self._boundary_nominal = self.adapter.get_boundary_parameters()
        self._boundary_cache = dict(self._boundary_nominal)
        self._prev_true = None
        self.instrumentation.reset()
        self.sync()

    @property
    def boundary_nominal(self) -> Dict[str, float]:
        return dict(self._boundary_nominal)

    def step(self) -> None:
        self.adapter.step(1, measurement_filter=self.instrumentation.filter)

    # ---- state synchronisation ----------------------------------------------
    def sync(self) -> None:
        a, st, p = self.adapter, self.state, self.state.process
        true = a.get_measurements()
        observed = self.instrumentation.observe(true)
        updates = {}
        for group, rng in ANALYZER_GROUPS.items():
            idx = [i - 1 for i in rng]
            updates[group] = self._prev_true is None or bool(np.any(true[idx] != self._prev_true[idx]))
        self._prev_true = true.copy()
        p.xmeas_true = true
        p.xmeas = observed
        p.quality = list(self.instrumentation.quality)
        p.xmv = a.get_manipulated_variables()
        p.idv = a.get_disturbances()
        p.setpoints = a.get_setpoints()
        p.loops = a.get_loop_states()
        # the loop table reports the PV the controller acts on -> transmitted value
        for row in p.loops:
            pv_idx = int(row["pv_id"][6:-1])
            row["pv"] = float(observed[pv_idx - 1])
            row["pv_quality"] = p.quality[pv_idx - 1]
        p.control_mode = a.get_control_mode().value
        p.tep_time_h = a.get_time()
        p.boundary = dict(self._boundary_cache)
        p.analyzer_updates = updates
        if a.is_shutdown() and not p.shutdown:
            p.shutdown = True
            p.shutdown_reason = a.get_shutdown_reason()
            p.shutdown_time_s = st.clock.time_s
        # equipment properties (transmitted values - what the plant sees)
        for idx, b in self.mapping.xmeas.items():
            if st.has_entity(b.equipment_id):
                rec = st.entities[b.equipment_id]
                rec.properties[b.property] = float(observed[idx - 1])
                rec.units[b.property] = b.unit
        for idx, b in self.mapping.xmv.items():
            if st.has_entity(b.equipment_id):
                rec = st.entities[b.equipment_id]
                rec.properties[b.property] = float(p.xmv[idx - 1])
                rec.units[b.property] = "%"
        for row in p.loops:
            cm = self.mapping.loops.get(row["loop_id"])
            if cm and st.has_entity(cm):
                props = st.entities[cm].properties
                props.update({"process_value": row["pv"], "setpoint": row["setpoint"], "output": row["output"],
                              "mode": row["mode"], "saturated": row["saturated"]})

    # ---- operator-level control actions ------------------------------------------
    def set_setpoint(self, loop_id: int, value: float, actor: str = "operator") -> None:
        old = self.adapter.get_setpoints()[loop_id]
        self.adapter.set_setpoint(loop_id, value)
        self.bus.publish(EventType.SETPOINT_CHANGED, actor, self.mapping.loops.get(loop_id),
                         {"loop_id": loop_id, "tag": cs.LOOPS[loop_id].tag, "old": old, "new": float(value)})
        self.sync()

    def set_loop_mode(self, loop_id: int, mode: str, actor: str = "operator") -> None:
        old = self.adapter.get_loop_modes()[loop_id].value
        self.adapter.set_loop_mode(loop_id, cs.LoopMode(mode))
        new = self.adapter.get_loop_modes()[loop_id].value
        self.bus.publish(EventType.CONTROL_MODE_CHANGED, actor, self.mapping.loops.get(loop_id),
                         {"loop_id": loop_id, "tag": cs.LOOPS[loop_id].tag, "old": old, "new": new})
        self.sync()

    def set_control_mode(self, mode: str, actor: str = "operator") -> None:
        old = self.adapter.get_control_mode().value
        self.adapter.set_control_mode(cs.ControlMode(mode))
        self.bus.publish(EventType.CONTROL_MODE_CHANGED, actor, "SITE-TE",
                         {"scope": "plant", "old": old, "new": mode})
        self.sync()

    def set_manipulated_variable(self, index: int, value: float, actor: str = "operator") -> None:
        old = float(self.adapter.get_manipulated_variables()[index - 1])
        self.adapter.set_manipulated_variable(index, value)
        self.bus.publish(EventType.MANIPULATED_VARIABLE_CHANGED, actor, self.mapping.xmv[index].equipment_id,
                         {"xmv": index, "name": catalog.XMV[index - 1].name, "old": old, "new": float(value)})
        self.sync()

    # ---- restricted: coupling engine ------------------------------------------------
    def get_boundary(self, name: str) -> float:
        return self._boundary_cache[name]

    def set_boundary(self, name: str, value: float) -> bool:
        """Returns True when the value actually changed."""
        value = float(value)
        if self._boundary_cache.get(name) == value:
            return False
        self.adapter.set_boundary_parameter(name, value)
        self._boundary_cache[name] = self.adapter.get_boundary_parameters()[name]
        return True

    # ---- restricted: fault engine ------------------------------------------------
    def set_disturbance(self, index: int, active: bool) -> None:
        self.adapter.set_disturbance(index, active)
        self.state.process.idv = self.adapter.get_disturbances()
