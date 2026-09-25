"""Hybrid simulation engine.

Continuous dynamics: TEP (Fortran, 1 s Euler steps with the native controllers).
Discrete events: faults, operator actions, equipment, maintenance, inventory,
production, quality, alarms - all evaluated on the same deterministic 1 s clock.

Per simulated second t -> t+1::

    pre_step(t):   faults -> operator -> equipment -> maintenance -> inventory
                   -> scheduling -> coupling (writes TEP boundary parameters)
    TEP step:      native controllers (on transmitted values) + INTGTR + CONSHAND
    sync:          canonical process image, equipment properties, shutdown
    post_step(t+1): utilities -> inventory -> production -> quality -> warehouse -> alarms
    trends:        in-memory buffer (every trend interval)
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from .. import __version__
from ..alarms import AlarmModule
from ..common import RandomStreams, iso
from ..coupling import CouplingEngine
from ..equipment import EquipmentModule
from ..equipment.instrumentation import Instrumentation
from ..events import EventBus, EventType
from ..faults.engine import FaultEngine
from ..inventory import InventoryModule
from ..isa95 import Hierarchy
from ..isa95.tep_mapping import TEPMapping
from ..maintenance import MaintenanceModule
from ..materials import MaterialsModule
from ..operator import OperatorModule
from ..production import ProductionModule
from ..quality import QualityModule
from ..scenarios import assemble_config, configuration_hash, validate_scenario
from ..scheduling import SchedulingModule
from ..state import CanonicalState
from ..tep import create_adapter
from ..tep.interface import TEPProcessAdapter
from ..utilities import UtilitiesModule
from ..warehouse import WarehouseModule
from .clock import SimulationClock
from .history import TrendBuffer
from .module import ModuleContext
from .process_interface import ProcessInterface


class SimulationEngine:
    def __init__(self, scenario: Dict[str, Any], base_config: Optional[Dict[str, Any]] = None,
                 adapter: Optional[TEPProcessAdapter] = None, duration_override: Optional[int] = None) -> None:
        self.scenario = validate_scenario(scenario)
        if duration_override is not None:
            self.scenario["duration_seconds"] = int(duration_override)
        self.config = assemble_config(self.scenario, base_config)
        self.config_hash = configuration_hash(self.scenario, self.config)
        self.adapter = adapter or create_adapter(self.scenario.get("backend", "auto"))
        self._build()

    # ------------------------------------------------------------------ construction
    def _build(self) -> None:
        sc, cfg = self.scenario, self.config
        self.duration_s = int(sc["duration_seconds"])
        self.clock = SimulationClock.from_config(sc["simulation_start"])
        self.hierarchy = Hierarchy.from_config(cfg["site"])
        self.mapping = TEPMapping.from_config(cfg["tep_mapping"], self.hierarchy)
        self.state = CanonicalState(self.hierarchy, self.clock)
        self.bus = EventBus(self.clock, int(cfg["simulation"].get("event_log", {}).get("max_events", 250000)))
        self.rng = RandomStreams(sc["seed"])
        self.instrumentation = Instrumentation(self.clock)
        self.process = ProcessInterface(self.adapter, self.state, self.bus, self.mapping, self.instrumentation)
        self._register_hierarchy()
        tep_seed = sc.get("tep_seed") or self.rng.tep_seed()
        self.tep_seed = int(tep_seed)
        self.process.initialize(self.tep_seed, sc.get("control_mode", "CLOSED_LOOP"))
        self.ctx = ModuleContext(self.state, self.bus, self.clock, self.rng, cfg, self.process, self.mapping)

        self.equipment = EquipmentModule()
        self.utilities = UtilitiesModule()
        self.materials = MaterialsModule()
        self.inventory = InventoryModule()
        self.warehouse = WarehouseModule()
        self.production = ProductionModule()
        self.scheduling = SchedulingModule()
        self.quality = QualityModule()
        self.maintenance = MaintenanceModule()
        self.alarms = AlarmModule()
        self.coupling = CouplingEngine()
        self.faults = FaultEngine()
        self.operator = OperatorModule()
        self._register_commands()

        setup_order = [self.equipment, self.utilities, self.materials, self.inventory, self.warehouse,
                       self.production, self.scheduling, self.quality, self.maintenance, self.alarms,
                       self.coupling, self.faults, self.operator]
        for m in setup_order:
            m.setup(self.ctx)
        self.pre_modules = [self.faults, self.operator, self.equipment, self.maintenance, self.inventory,
                            self.scheduling, self.coupling]
        self.post_modules = [self.utilities, self.inventory, self.production, self.quality, self.warehouse,
                             self.alarms]
        self.modules = {m.name: m for m in setup_order}

        tcfg = cfg["simulation"].get("trend", {})
        self.history = TrendBuffer(int(tcfg.get("interval_s", 10)), int(tcfg.get("capacity", 20000)))
        storages = sorted(cfg["materials"].get("storage", {}).keys())
        self.history.build_default(self.state, sorted(self.equipment.assets),
                                   sorted(self.utilities.services), storages)
        self.status = "READY"
        self.wall_start: Optional[float] = None
        self.completed = False
        self.state.run.update(self.manifest())
        # t = 0 observation pass so every derived value exists before the first step
        for m in (self.utilities, self.warehouse, self.alarms):
            m.post_step(0)
        self.history.record(0, force=True)

    def _register_hierarchy(self) -> None:
        for e in self.hierarchy.elements.values():
            bindings = [b.var_id for b in self.mapping.bindings_for(e.id)]
            self.state.register_entity(
                e.id, "equipment", e.name, {},
                {}, {"level": e.level.value, "origin": e.origin.value, "parent_id": e.parent_id,
                     "path": self.hierarchy.path(e.id), "area_id": self.hierarchy.area_of(e.id),
                     "equipment_class": e.equipment_class, "description": e.description,
                     "attributes": dict(e.attributes), "tep_variables": bindings})

    def _register_commands(self) -> None:
        op, p = self.operator, self.process

        def set_setpoint(loop_id: int, value: float, actor: str = "operator"):
            p.set_setpoint(int(loop_id), float(value), actor)

        def set_loop_mode(loop_id: int, mode: str, actor: str = "operator"):
            p.set_loop_mode(int(loop_id), str(mode), actor)

        def set_control_mode(mode: str, actor: str = "operator"):
            p.set_control_mode(str(mode), actor)

        def set_xmv(index: int, value: float, actor: str = "operator"):
            p.set_manipulated_variable(int(index), float(value), actor)

        def equipment_command(asset_id: str, state: str, actor: str = "operator"):
            self.equipment.command(asset_id, state, actor)

        def acknowledge_alarm(alarm_id: str, actor: str = "operator"):
            return self.alarms.acknowledge(alarm_id, actor).to_dict()

        def acknowledge_all(actor: str = "operator"):
            n = 0
            for a in list(self.state.collection("alarms").values()):
                if a.state in ("ACTIVE_UNACK", "RTN_UNACK"):
                    self.alarms.acknowledge(a.alarm_id, actor)
                    n += 1
            return {"acknowledged": n}

        def request_maintenance(asset_id: str, kind: str = "corrective", priority: int = 3,
                                description: str = "", actor: str = "operator"):
            return self.maintenance.request(asset_id, kind, int(priority), actor, description).to_dict()

        def cancel_work_order(wo_id: str, actor: str = "operator"):
            return self.maintenance.cancel(wo_id, actor).to_dict()

        def order_command(order_id: str, command: str, actor: str = "operator"):
            return self.scheduling.command(order_id, command, actor).to_dict()

        def create_order(order: dict, actor: str = "operator"):
            return self.scheduling.create_order(order, actor).to_dict()

        def order_material(storage_id: str, quantity_kg: float, actor: str = "operator"):
            return self.inventory.order_material(storage_id, float(quantity_kg), actor).to_dict()

        for name, fn in list(locals().items()):
            if callable(fn) and name not in ("op", "p", "self"):
                op.register(name, fn)

    # ------------------------------------------------------------------ manifest
    def manifest(self) -> dict:
        tep = self.adapter.version_info()
        return {
            "run_id": f"RUN-{self.scenario['id']}-{self.scenario['seed']}-{self.config_hash[:10]}",
            "scenario_id": self.scenario["id"], "scenario_name": self.scenario["name"],
            "seed": self.scenario["seed"], "tep_seed": self.tep_seed,
            "tep_backend": self.adapter.backend_name, "tep_version": tep,
            "simulator_version": __version__, "configuration_hash": self.config_hash,
            "simulation_start": iso(self.clock.start), "duration_seconds": self.duration_s,
            "control_mode": self.scenario.get("control_mode"),
        }

    def run_manifest(self) -> dict:
        m = self.manifest()
        faults = [f.to_dict() for f in self.state.collection("faults").values()]
        m.update({"simulated_seconds": self.clock.time_s, "simulation_end": self.clock.timestamp(),
                  "status": self.status, "events": len(self.bus.log),
                  "faults": [{"id": f["id"], "type": f["type"], "target": f["target"], "status": f["status"],
                              "start_time": f["start_time"], "activated_at": f["activated_at"],
                              "stopped_at": f["stopped_at"], "severity": f["severity"]} for f in faults],
                  "active_faults": [f["id"] for f in faults if f["status"] == "ACTIVE"],
                  "process_shutdown": self.state.process.shutdown,
                  "process_shutdown_reason": self.state.process.shutdown_reason})
        return m

    # ------------------------------------------------------------------ execution
    def start(self) -> None:
        if self.status in ("READY",):
            self.status = "RUNNING"
            self.wall_start = time.time()
            self.bus.publish(EventType.SIMULATION_STARTED, "simulation", "SITE-TE",
                             {"run_id": self.state.run["run_id"], "scenario_id": self.scenario["id"],
                              "seed": self.scenario["seed"], "duration_seconds": self.duration_s})

    def step(self, n: int = 1) -> int:
        """Advance up to n seconds. Returns the number of seconds actually simulated."""
        if n < 1:
            raise ValueError("n must be >= 1")
        if self.status == "READY":
            self.start()
        done = 0
        for _ in range(n):
            if self.clock.time_s >= self.duration_s:
                self._complete()
                break
            self._step_once()
            done += 1
        if self.clock.time_s >= self.duration_s:
            self._complete()
        return done

    def _step_once(self) -> None:
        t = self.clock.time_s
        for m in self.pre_modules:
            m.pre_step(t)
        was_down = self.state.process.shutdown
        self.process.step()
        self.clock.advance(1)
        self.process.sync()
        t1 = self.clock.time_s
        if self.state.process.shutdown and not was_down:
            self.bus.publish(EventType.PROCESS_SHUTDOWN, "tep", "SITE-TE",
                             {"reason": self.state.process.shutdown_reason, "time_s": t1},
                             severity="critical", **self._shutdown_cause())
        for m in self.post_modules:
            m.post_step(t1)
        self.history.record(t1)

    def _shutdown_cause(self) -> dict:
        ref = self.state.causal.first(["XMEAS(7)", "XMEAS(9)", "EM-REACTOR", "EM-SEPARATOR", "EM-STRIPPER"])
        return {"correlation_id": ref.correlation_id, "causation_id": ref.event_id} if ref else {}

    def run_until(self, t_end: int) -> int:
        t_end = min(int(t_end), self.duration_s)
        if t_end <= self.clock.time_s:
            return 0
        return self.step(t_end - self.clock.time_s)

    def run(self) -> None:
        self.run_until(self.duration_s)

    def _complete(self) -> None:
        if not self.completed:
            self.completed = True
            self.status = "COMPLETED"
            self.bus.publish(EventType.SIMULATION_COMPLETED, "simulation", "SITE-TE",
                             {"run_id": self.state.run["run_id"], "simulated_seconds": self.clock.time_s})

    # ------------------------------------------------------------------ views
    def snapshot(self, include_truth: bool = True) -> dict:
        s = self.state.snapshot(include_truth=include_truth)
        s["status"] = self.status
        s["duration_s"] = self.duration_s
        s["manifest"] = self.manifest()
        return s
