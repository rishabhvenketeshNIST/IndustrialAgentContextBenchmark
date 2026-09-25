"""SimulatorService - the backend API as plain Python operations.

REST routes in api/app.py are thin wrappers around these methods. Operations
are generic (entity/property based); there are no variable-specific getters.

Read operations return the *operational* view by default (api/operational.py): what plant systems
could observe, with no ground truth. ``truth=True`` returns the canonical, unredacted view; it is used
only by the benchmark/evaluator routes under /api/benchmark.

Fault-injection operations live in the separate ``BenchmarkFaultAPI`` so a
future agent interface can be given ``SimulatorService`` without any access to
fault injection or fault ground truth.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from simulator.common import SCENARIO_DIR, to_jsonable
from simulator.enterprise import EnterpriseView
from simulator.events import EventType, Visibility
from simulator.isa95 import EquipmentLevel
from simulator.scenarios import ScenarioStore, validate_scenario
from simulator.simulation.clock import validate_speed
from simulator.simulation.engine import SimulationEngine
from simulator.tep import catalog
from simulator.tep import control_scheme as cs
from simulator.tep import available_backends

from . import operational

log = logging.getLogger("acme.sim")


class Runner(threading.Thread):
    """Advances the engine in (scaled) real time on a background thread."""

    def __init__(self, service: "SimulatorService") -> None:
        super().__init__(daemon=True, name="sim-runner")
        self.svc = service
        self.running = False
        self._stop_evt = threading.Event()
        self._carry = 0.0
        self._last = time.perf_counter()

    def run(self) -> None:
        while not self._stop_evt.is_set():
            now = time.perf_counter()
            dt, self._last = now - self._last, now
            if self.running:
                budget = dt * self.svc.speed + self._carry
                n = int(budget)
                self._carry = budget - n
                n = min(n, self.svc.max_steps_per_tick)
                if n > 0:
                    with self.svc.lock:
                        eng = self.svc.engine
                        if eng is not None and not eng.completed:
                            try:
                                eng.step(n)
                            except Exception:  # pragma: no cover - surfaced in status
                                log.exception("simulation step failed")
                                self.svc.error = "simulation step failed - see server log"
                                self.running = False
                        if eng is None or eng.completed:
                            self.running = False
                            self._carry = 0.0
            time.sleep(0.02)

    def stop(self) -> None:
        self._stop_evt.set()


class SimulatorService:
    def __init__(self, scenario_dir=SCENARIO_DIR, backend: Optional[str] = None,
                 start_runner: bool = True) -> None:
        self.lock = threading.RLock()
        self.store = ScenarioStore(scenario_dir)
        self.backend_override = backend
        self.engine: Optional[SimulationEngine] = None
        self.scenario: Optional[Dict[str, Any]] = None
        self.duration_override: Optional[int] = None
        self.speed = 10.0
        self.max_steps_per_tick = 2000
        self.error: Optional[str] = None
        self._ops: Optional[operational.OperationalEventView] = None
        self.runner = Runner(self)
        if start_runner:
            self.runner.start()

    # ------------------------------------------------------------------ helpers
    def _eng(self) -> SimulationEngine:
        if self.engine is None:
            raise RuntimeError("No simulation created. Call create_simulation() first.")
        return self.engine

    @property
    def view(self) -> EnterpriseView:
        return EnterpriseView(self._eng())

    def _view(self, truth: bool) -> EnterpriseView:
        e = self._eng()
        return self.view if truth else EnterpriseView(e, lambda eid: operational.entity_status(e.state, eid))

    def _event_view(self) -> operational.OperationalEventView:
        bus = self._eng().bus
        if self._ops is None or self._ops.bus is not bus:
            self._ops = operational.OperationalEventView(bus)
        return self._ops

    # ------------------------------------------------------------------ simulation lifecycle
    def create_simulation(self, scenario_id: Optional[str] = None, scenario: Optional[dict] = None,
                          duration_seconds: Optional[int] = None, seed: Optional[int] = None,
                          backend: Optional[str] = None) -> dict:
        if scenario is None:
            scenario = self.store.load(scenario_id or "SCN-COOL-001")
        sc = validate_scenario(scenario)
        if seed is not None:
            sc["seed"] = int(seed)
        if backend or self.backend_override:
            sc["backend"] = backend or self.backend_override
        with self.lock:
            self.runner.running = False
            self.engine = SimulationEngine(sc, duration_override=duration_seconds)
            self.scenario = sc
            self.duration_override = duration_seconds
            self.error = None
            rcfg = self.engine.config["simulation"].get("runner", {})
            self.speed = float(rcfg.get("default_speed", self.speed))
            self.max_steps_per_tick = int(rcfg.get("max_steps_per_tick", 2000))
            log.info("created simulation %s", self.engine.state.run["run_id"])
            return self.get_simulation_state()

    def start_simulation(self) -> dict:
        with self.lock:
            e = self._eng()
            if e.completed:
                raise RuntimeError("Simulation completed; reset to run again")
            e.start()
            self.runner._last = time.perf_counter()
            self.runner.running = True
            return self.get_simulation_state()

    def pause_simulation(self) -> dict:
        with self.lock:
            e = self._eng()
            if self.runner.running:
                self.runner.running = False
                e.status = "PAUSED"
                e.bus.publish(EventType.SIMULATION_PAUSED, "simulation", "SITE-TE", {"time_s": e.clock.time_s})
            return self.get_simulation_state()

    def resume_simulation(self) -> dict:
        with self.lock:
            e = self._eng()
            if e.completed:
                raise RuntimeError("Simulation completed; reset to run again")
            if not self.runner.running:
                if e.status == "READY":
                    e.start()
                else:
                    e.bus.publish(EventType.SIMULATION_RESUMED, "simulation", "SITE-TE", {"time_s": e.clock.time_s})
                e.status = "RUNNING"
                self.runner._last = time.perf_counter()
                self.runner.running = True
            return self.get_simulation_state()

    def reset_simulation(self, duration_seconds: Optional[int] = None) -> dict:
        """Rebuild the run from the same scenario/seed: state, history and events return to t = 0."""
        with self.lock:
            if self.scenario is None:
                raise RuntimeError("No simulation to reset")
            self.runner.running = False
            if self.engine is not None and hasattr(self.engine.adapter, "close"):
                self.engine.adapter.close()
            dur = duration_seconds if duration_seconds is not None else self.duration_override
            self.engine = SimulationEngine(self.scenario, duration_override=dur)
            self.duration_override = dur
            self.engine.bus.publish(EventType.SIMULATION_RESET, "simulation", "SITE-TE",
                                    {"scenario_id": self.scenario["id"]})
            return self.get_simulation_state()

    def step_simulation(self, n: int = 1) -> dict:
        if not 1 <= int(n) <= 86400:
            raise ValueError("n must be within 1..86400")
        with self.lock:
            self.runner.running = False
            e = self._eng()
            e.step(int(n))
            if not e.completed:
                e.status = "PAUSED"
            return self.get_simulation_state()

    def run_until(self, time_s: int) -> dict:
        with self.lock:
            self.runner.running = False
            e = self._eng()
            e.run_until(int(time_s))
            if not e.completed:
                e.status = "PAUSED"
            return self.get_simulation_state()

    def set_speed(self, speed: float) -> dict:
        self.speed = validate_speed(speed)
        return self.get_simulation_state()

    def get_simulation_state(self, truth: bool = False) -> dict:
        e = self.engine
        if e is None:
            return {"status": "NO_SIMULATION", "backends": available_backends()}
        out = {
            "status": e.status, "running": self.runner.running, "speed": self.speed, "clock": e.clock.to_dict(),
            "duration_s": e.duration_s, "progress": e.clock.time_s / e.duration_s if e.duration_s else 0,
            "manifest": e.manifest() if truth else operational.manifest(e.manifest()),
            "process": {"shutdown": e.state.process.shutdown, "shutdown_reason": e.state.process.shutdown_reason,
                        "control_mode": e.state.process.control_mode},
            "production": e.state.production, "alarms": e.alarms.summary(), "error": self.error,
            "backends": available_backends()}
        if truth:
            out["scenario"] = {"id": e.scenario["id"], "name": e.scenario["name"],
                               "description": e.scenario["description"]}
        return to_jsonable(out)

    def get_manifest(self, truth: bool = False) -> dict:
        with self.lock:
            m = self._eng().run_manifest()
            return to_jsonable(m if truth else operational.manifest(m))

    # ------------------------------------------------------------------ enterprise model
    def get_enterprise(self, truth: bool = False) -> dict:
        with self.lock:
            return to_jsonable(self._view(truth).enterprise())

    def get_site(self, truth: bool = False) -> dict:
        with self.lock:
            return to_jsonable(self._view(truth).site())

    def get_areas(self, truth: bool = False) -> List[dict]:
        with self.lock:
            return to_jsonable(self._view(truth).elements(level=EquipmentLevel.AREA.value))

    def get_hierarchy(self, truth: bool = False) -> dict:
        with self.lock:
            return to_jsonable(self._view(truth).tree())

    def get_equipment(self, level: Optional[str] = None, parent: Optional[str] = None,
                      truth: bool = False) -> List[dict]:
        with self.lock:
            return to_jsonable(self._view(truth).elements(level=level, parent=parent))

    def get_equipment_state(self, entity_id: str, truth: bool = False) -> dict:
        with self.lock:
            d = self._view(truth).entity(entity_id)
            return d if truth else operational.entity_dict(self._eng().state.entity(entity_id), d)

    def get_entity(self, entity_id: str, truth: bool = False) -> dict:
        return self.get_equipment_state(entity_id, truth)

    def list_entities(self, kind: Optional[str] = None) -> List[dict]:
        with self.lock:
            return [{"id": e.id, "kind": e.kind, "name": e.name} for e in self._eng().state.entities.values()
                    if kind is None or e.kind == kind]

    def get_property(self, entity_id: str, prop: str, truth: bool = False) -> dict:
        with self.lock:
            if truth:
                return self.view.property(entity_id, prop)
            rec = self._eng().state.entity(entity_id)
            if not operational.property_visible(rec, prop):
                raise KeyError(f"Entity '{entity_id}' has no property '{prop}'")
            d = self.view.property(entity_id, prop)
            if prop == "status":
                d["value"] = operational.status_value(rec, d["value"])
            return d

    # ------------------------------------------------------------------ process data
    def get_measurements(self) -> List[dict]:
        with self.lock:
            e = self._eng()
            p = e.state.process
            return to_jsonable([{**e.mapping.xmeas[i + 1].to_dict(), "value": float(p.xmeas[i]),
                                 "quality": p.quality[i]} for i in range(catalog.NUM_XMEAS)])

    def get_setpoints(self) -> List[dict]:
        with self.lock:
            e = self._eng()
            return to_jsonable([{"loop_id": l["loop_id"], "tag": l["tag"], "name": l["name"],
                                 "setpoint": l["setpoint"], "pv_id": l["pv_id"], "pv": l["pv"], "mode": l["mode"],
                                 "cascade_parent": l["cascade_parent"],
                                 "control_module": e.mapping.loops[l["loop_id"]],
                                 "unit": catalog.XMEAS[cs.LOOPS[l["loop_id"]].pv - 1].unit}
                                for l in e.state.process.loops])

    def get_manipulated_variables(self) -> List[dict]:
        with self.lock:
            e = self._eng()
            p = e.state.process
            return to_jsonable([{**e.mapping.xmv[i + 1].to_dict(), "value": float(p.xmv[i]),
                                 "controlled_by_loop": cs.loop_for_xmv(i + 1)} for i in range(catalog.NUM_XMV)])

    def get_loops(self) -> List[dict]:
        with self.lock:
            e = self._eng()
            return to_jsonable([dict(l, control_module=e.mapping.loops[l["loop_id"]]) for l in e.state.process.loops])

    def get_process_image(self, truth: bool = False) -> dict:
        with self.lock:
            e = self._eng()
            p = e.state.process
            out = {"xmeas": p.xmeas, "quality": p.quality, "xmv": p.xmv, "loops": p.loops,
                   "control_mode": p.control_mode, "shutdown": p.shutdown, "shutdown_reason": p.shutdown_reason}
            if truth:
                out.update({"xmeas_true": p.xmeas_true, "idv": p.idv, "boundary": p.boundary,
                            "boundary_nominal": e.process.boundary_nominal})
            return to_jsonable(out)

    def get_catalog(self) -> dict:
        with self.lock:
            e = self._eng()
            return to_jsonable({"xmeas": [e.mapping.xmeas[i].to_dict() for i in sorted(e.mapping.xmeas)],
                                "xmv": [e.mapping.xmv[i].to_dict() for i in sorted(e.mapping.xmv)],
                                "idv": [dict(v.to_dict(), equipment=e.mapping.idv.get(v.index, []),
                                             code_effect=e.mapping.idv_notes.get(v.index))
                                        for v in catalog.IDV],
                                "states": [v.to_dict() for v in catalog.STATES],
                                "loops": [{"loop_id": l, "tag": d.tag, "name": d.name, "pv": f"XMEAS({d.pv})",
                                           "output": f"{d.output_kind}({d.output_index})", "gain": d.gain,
                                           "taui_h": d.taui_h, "period_s": d.period_steps,
                                           "control_module": e.mapping.loops[l]} for l, d in cs.LOOPS.items()],
                                "shutdown_limits": catalog.shutdown_limits_in_measurement_units()})

    def get_internal_states(self) -> dict:
        """Diagnostic only - the 50 TEP states are not operational data."""
        with self.lock:
            e = self._eng()
            return to_jsonable({"diagnostic_only": True, "states": [
                {**v.to_dict(), "value": float(x)} for v, x in zip(catalog.STATES, e.adapter.get_states())]})

    # ------------------------------------------------------------------ operations data
    def get_alarms(self, active_only: bool = False) -> List[dict]:
        with self.lock:
            out = [a.to_dict() for a in self._eng().state.collection("alarms").values()
                   if (a.state != "NORMAL" if active_only else a.count > 0 or a.state != "NORMAL")]
            rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
            return to_jsonable(sorted(out, key=lambda a: (not a["active"], rank[a["priority"]],
                                                          -(a["activation_time"] or 0))))

    def get_events(self, since: Optional[int] = None, types: Optional[List[str]] = None, limit: int = 500,
                   after_id: Optional[str] = None, include_benchmark: bool = False,
                   target: Optional[str] = None, truth: bool = False) -> List[dict]:
        """Operational stream by default. ``truth=True`` returns the canonical events (true correlation;
        benchmark-visibility events only with ``include_benchmark``), each with its ``operational_id``
        (null when the operational stream withholds it) so an evaluator can map operational references."""
        with self.lock:
            if not truth:
                return self._event_view().query(types=types, since=since, target=target, limit=limit,
                                                after_id=after_id)
            ops = self._event_view()
            return [dict(ev.to_dict(), operational_id=ops.operational_id(ev.event_id))
                    for ev in self._eng().bus.query(since=since, types=types, limit=limit, after_id=after_id,
                                                    include_benchmark=include_benchmark, target=target)]

    def get_utilities(self, truth: bool = False) -> dict:
        with self.lock:
            e = self._eng()
            out = {}
            for uid in sorted(e.utilities.services):
                rec = e.state.entity(uid)
                out[uid] = rec.to_dict() if truth else operational.entity_dict(rec, rec.to_dict())
            return to_jsonable(out)

    def get_maintenance(self, truth: bool = False) -> dict:
        with self.lock:
            st = self._eng().state
            e = self._eng()
            keys = ("status", "health", "efficiency", "vibration", "role", "run_hours", "is_running")
            assets = []
            for a in sorted(e.equipment.assets):
                rec = st.entity(a)
                props = rec.properties if truth else operational.properties(rec)
                assets.append({"id": a, "name": rec.name, **{k: props.get(k) for k in keys if k in props}})
            return to_jsonable({
                "work_orders": [w.to_dict() for w in sorted(st.collection("work_orders").values(),
                                                            key=lambda w: w.wo_id, reverse=True)],
                "technicians": [t.to_dict() for t in st.collection("technicians").values()],
                "assets": assets,
                "spare_parts": [p.to_dict() for p in st.collection("spare_parts").values()]})

    def get_inventory(self, truth: bool = False) -> dict:
        with self.lock:
            e = self._eng()
            st = e.state
            storage = []
            for sid in sorted(e.inventory.storage):
                rec = st.entity(sid)
                props = rec.properties if truth else operational.properties(rec)
                storage.append({"id": sid, "name": rec.name, **props})
            return to_jsonable({
                "storage": storage,
                "material_lots": [l.to_dict() for l in st.collection("material_lots").values()],
                "purchase_orders": [p.to_dict() for p in st.collection("purchase_orders").values()],
                "spare_parts": [p.to_dict() for p in st.collection("spare_parts").values()],
                "materials": [st.entity(m).to_dict() if truth else
                              operational.entity_dict(st.entity(m), st.entity(m).to_dict())
                              for m in sorted(e.materials.materials)],
                "warehouse": e.warehouse.summary(),
                "shipments": [s.to_dict() for s in st.collection("shipments").values()]})

    def get_quality(self) -> dict:
        with self.lock:
            e = self._eng()
            st = e.state
            return to_jsonable({"specifications": e.quality.summary()["specifications"],
                                "samples": [s.to_dict() for s in sorted(st.collection("quality_samples").values(),
                                                                        key=lambda s: s.sample_id, reverse=True)],
                                "lots": [l.to_dict() for l in st.collection("production_lots").values()],
                                "laboratory": st.entity(e.quality.lab).properties})

    def get_production_orders(self) -> List[dict]:
        with self.lock:
            return to_jsonable([o.to_dict() for o in sorted(self._eng().state.collection("production_orders")
                                                            .values(), key=lambda o: o.planned_start)])

    def get_production(self) -> dict:
        with self.lock:
            return to_jsonable(self._eng().state.production)

    def get_coupling(self) -> dict:
        with self.lock:
            e = self._eng()
            return to_jsonable({"relations": e.coupling.summary()["relations"], "graph": e.coupling.graph(),
                                "boundary": e.state.process.boundary, "boundary_nominal": e.process.boundary_nominal,
                                "boundary_definitions": {k: v.to_dict() for k, v in
                                                         e.adapter.get_boundary_parameter_definitions().items()}})

    def get_history(self, series: List[str], since: Optional[int] = None, max_points: int = 1500,
                    truth: bool = False) -> dict:
        with self.lock:
            e = self._eng()
            if not truth:
                hidden = [s for s in series if not operational.series_visible(e.state, s)]
                if hidden:
                    raise KeyError(f"Unknown series {hidden}")
            return e.history.query(series, since, max_points)

    def get_history_catalog(self, truth: bool = False) -> List[dict]:
        with self.lock:
            e = self._eng()
            return [m for m in e.history.meta.values() if truth or operational.series_visible(e.state, m["name"])]

    # ------------------------------------------------------------------ operator actions
    def operator_action(self, action: str, params: Dict[str, Any], actor: str = "operator") -> Any:
        with self.lock:
            return to_jsonable(self._eng().operator.execute(action, params, actor))

    def operator_actions(self) -> List[str]:
        with self.lock:
            return sorted(self._eng().operator.commands)

    # ------------------------------------------------------------------ scenarios
    def list_scenarios(self) -> List[dict]:
        return self.store.list()

    def get_scenario(self, scenario_id: str) -> dict:
        return self.store.load(scenario_id)

    def save_scenario(self, scenario: dict, overwrite: bool = False) -> dict:
        self.store.save(scenario, overwrite=overwrite)
        return validate_scenario(scenario)

    def duplicate_scenario(self, scenario_id: str, new_id: str, new_name: Optional[str] = None) -> dict:
        return self.store.duplicate(scenario_id, new_id, new_name)

    def current_scenario_with_faults(self) -> dict:
        """Current scenario including faults created interactively (for 'save as')."""
        with self.lock:
            e = self._eng()
            sc = dict(self.scenario)
            faults = []
            for f in e.state.collection("faults").values():
                d = f.to_dict()
                faults.append({"id": d["id"], "type": d["type"], "target": d["target"], "trigger": {
                    "mode": "simulation_time", "time": d["activated_at"] if d["activated_at"] is not None
                    else d["start_time"] or 0} if d["activated_at"] is not None or d["start_time"] is not None
                    else {"mode": "manual"}, "severity": d["severity"], "duration": d["duration"],
                    "progression": {k: v for k, v in d["progression"].items() if v is not None},
                    "parameters": d["parameters"], "observability": d["observability"],
                    "expected_effects": d["expected_effects"], "description": d["description"]})
            sc["faults"] = faults
            return sc

    # ------------------------------------------------------------------ export
    def export_json(self) -> dict:
        from .export import build_bundle
        with self.lock:
            return build_bundle(self._eng())

    def export_csv_zip(self) -> bytes:
        from .export import build_csv_zip
        with self.lock:
            return build_csv_zip(self._eng())

    def shutdown(self) -> None:
        self.runner.stop()


class BenchmarkFaultAPI:
    """Benchmark-operator fault injection. NEVER expose this to an agent interface."""

    def __init__(self, service: SimulatorService) -> None:
        self.svc = service

    def _fe(self):
        return self.svc._eng().faults

    def catalog(self) -> List[dict]:
        with self.svc.lock:
            return to_jsonable(self._fe().catalog())

    def create_fault(self, spec: dict) -> dict:
        with self.svc.lock:
            return to_jsonable(self._fe().create_fault(spec).to_dict())

    def schedule_fault(self, fault_id: str, start_time: int) -> dict:
        with self.svc.lock:
            return to_jsonable(self._fe().schedule_fault(fault_id, start_time).to_dict())

    def start_fault(self, fault_id: str) -> dict:
        with self.svc.lock:
            return to_jsonable(self._fe().start_fault(fault_id).to_dict())

    def stop_fault(self, fault_id: str) -> dict:
        with self.svc.lock:
            return to_jsonable(self._fe().stop_fault(fault_id).to_dict())

    def reset_fault(self, fault_id: str) -> dict:
        with self.svc.lock:
            return to_jsonable(self._fe().reset_fault(fault_id).to_dict())

    def get_fault_status(self, fault_id: str) -> dict:
        with self.svc.lock:
            return to_jsonable(self._fe().get_fault_status(fault_id))

    def list_faults(self) -> List[dict]:
        with self.svc.lock:
            return to_jsonable(self._fe().list_faults())

    def ground_truth_events(self, limit: int = 500) -> List[dict]:
        with self.svc.lock:
            return [e.to_dict() for e in self.svc._eng().bus.log if e.visibility == Visibility.BENCHMARK][-limit:]

    def fault_effects(self) -> dict:
        with self.svc.lock:
            e = self.svc._eng()
            return to_jsonable({"channels": e.state.fault_effects.to_dict(), "causal": e.state.causal.to_dict(),
                                "sensor_overlays": {f"XMEAS({k})": v for k, v in
                                                    e.instrumentation.active_channels().items()}})
