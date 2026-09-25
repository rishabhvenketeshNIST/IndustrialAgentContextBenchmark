"""REST API + static UI server.

Operational routes: /api/...           (candidate surface for a future agent interface)
Benchmark routes:   /api/benchmark/... (fault injection and ground truth - benchmark operator only)
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from simulator.common import ConfigError
from simulator.faults.types import FaultValidationError
from simulator.tep.interface import TEPAdapterError

from .service import BenchmarkFaultAPI, SimulatorService

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "ui"
log = logging.getLogger("acme.api")


class CreateRequest(BaseModel):
    scenario_id: Optional[str] = None
    scenario: Optional[Dict[str, Any]] = None
    duration_seconds: Optional[int] = Field(None, ge=1, le=30 * 24 * 3600)
    seed: Optional[int] = Field(None, ge=0)
    backend: Optional[str] = None


class StepRequest(BaseModel):
    n: int = Field(1, ge=1, le=86400)


class RunUntilRequest(BaseModel):
    time_s: int = Field(..., ge=0)


class SpeedRequest(BaseModel):
    speed: float = Field(..., gt=0)


class ResetRequest(BaseModel):
    duration_seconds: Optional[int] = Field(None, ge=1, le=30 * 24 * 3600)


class ScheduleRequest(BaseModel):
    start_time: int = Field(..., ge=0)


class DuplicateRequest(BaseModel):
    new_id: str
    new_name: Optional[str] = None


def create_app(service: Optional[SimulatorService] = None, autoload: Optional[str] = "SCN-COOL-001") -> FastAPI:
    svc = service or SimulatorService(backend=os.environ.get("TEP_BACKEND") or None)
    bench = BenchmarkFaultAPI(svc)
    @asynccontextmanager
    async def lifespan(_app):
        yield
        svc.shutdown()

    app = FastAPI(title="ACME Enterprise Simulator (TEP)", version="1.0.0", lifespan=lifespan,
                  description="Enterprise manufacturing simulation around the Tennessee Eastman Process.")
    app.state.service = svc
    app.state.bench = bench
    if autoload and svc.engine is None:
        try:
            svc.create_simulation(autoload)
        except Exception:  # pragma: no cover - the UI can still create one
            log.exception("autoload of scenario %s failed", autoload)

    @app.exception_handler(KeyError)
    async def _key(_: Request, exc: KeyError):
        return JSONResponse({"error": str(exc).strip("'\"")}, status_code=404)

    @app.exception_handler(ValueError)
    async def _val(_: Request, exc: ValueError):
        return JSONResponse({"error": str(exc)}, status_code=400)

    for exc_type in (ConfigError, FaultValidationError, TEPAdapterError):
        app.add_exception_handler(exc_type, _val)

    @app.exception_handler(RuntimeError)
    async def _rt(_: Request, exc: RuntimeError):
        return JSONResponse({"error": str(exc)}, status_code=409)

    # ---------------------------------------------------------------- simulation
    @app.get("/api/simulation")
    def get_simulation_state():
        return svc.get_simulation_state()

    @app.post("/api/simulation/create")
    def create_simulation(req: CreateRequest):
        return svc.create_simulation(req.scenario_id, req.scenario, req.duration_seconds, req.seed, req.backend)

    @app.post("/api/simulation/start")
    def start_simulation():
        return svc.start_simulation()

    @app.post("/api/simulation/pause")
    def pause_simulation():
        return svc.pause_simulation()

    @app.post("/api/simulation/resume")
    def resume_simulation():
        return svc.resume_simulation()

    @app.post("/api/simulation/reset")
    def reset_simulation(req: ResetRequest = Body(default=ResetRequest())):
        return svc.reset_simulation(req.duration_seconds)

    @app.post("/api/simulation/step")
    def step_simulation(req: StepRequest = Body(default=StepRequest())):
        return svc.step_simulation(req.n)

    @app.post("/api/simulation/run_until")
    def run_until(req: RunUntilRequest):
        return svc.run_until(req.time_s)

    @app.post("/api/simulation/speed")
    def set_speed(req: SpeedRequest):
        return svc.set_speed(req.speed)

    @app.get("/api/simulation/manifest")
    def manifest():
        return svc._eng().run_manifest()

    # ---------------------------------------------------------------- model
    @app.get("/api/enterprise")
    def get_enterprise():
        return svc.get_enterprise()

    @app.get("/api/site")
    def get_site():
        return svc.get_site()

    @app.get("/api/areas")
    def get_areas():
        return svc.get_areas()

    @app.get("/api/hierarchy")
    def get_hierarchy():
        return svc.get_hierarchy()

    @app.get("/api/equipment")
    def get_equipment(level: Optional[str] = None, parent: Optional[str] = None):
        return svc.get_equipment(level, parent)

    @app.get("/api/equipment/{entity_id}")
    def get_equipment_state(entity_id: str):
        return svc.get_equipment_state(entity_id)

    @app.get("/api/entities")
    def list_entities(kind: Optional[str] = None):
        return svc.list_entities(kind)

    @app.get("/api/entities/{entity_id}")
    def get_entity(entity_id: str):
        return svc.get_entity(entity_id)

    @app.get("/api/entities/{entity_id}/properties/{prop}")
    def get_property(entity_id: str, prop: str):
        return svc.get_property(entity_id, prop)

    # ---------------------------------------------------------------- process
    @app.get("/api/process/measurements")
    def get_measurements():
        return svc.get_measurements()

    @app.get("/api/process/setpoints")
    def get_setpoints():
        return svc.get_setpoints()

    @app.get("/api/process/manipulated-variables")
    def get_manipulated_variables():
        return svc.get_manipulated_variables()

    @app.get("/api/process/loops")
    def get_loops():
        return svc.get_loops()

    @app.get("/api/process/image")
    def get_process_image():
        return svc.get_process_image()

    @app.get("/api/process/catalog")
    def get_catalog():
        return svc.get_catalog()

    @app.get("/api/process/internal-states")
    def get_internal_states():
        return svc.get_internal_states()

    # ---------------------------------------------------------------- operations
    @app.get("/api/alarms")
    def get_alarms(active_only: bool = False):
        return svc.get_alarms(active_only)

    @app.get("/api/events")
    def get_events(since: Optional[int] = None, types: Optional[str] = None, limit: int = Query(500, le=20000),
                   after_id: Optional[str] = None, target: Optional[str] = None):
        return svc.get_events(since, types.split(",") if types else None, limit, after_id, False, target)

    @app.get("/api/utilities")
    def get_utilities():
        return svc.get_utilities()

    @app.get("/api/maintenance")
    def get_maintenance():
        return svc.get_maintenance()

    @app.get("/api/inventory")
    def get_inventory():
        return svc.get_inventory()

    @app.get("/api/quality")
    def get_quality():
        return svc.get_quality()

    @app.get("/api/production-orders")
    def get_production_orders():
        return svc.get_production_orders()

    @app.get("/api/production")
    def get_production():
        return svc.get_production()

    @app.get("/api/coupling")
    def get_coupling():
        return svc.get_coupling()

    @app.get("/api/history")
    def get_history(series: str, since: Optional[int] = None, max_points: int = Query(1500, ge=10, le=20000)):
        return svc.get_history([s for s in series.split("|") if s], since, max_points)

    @app.get("/api/history/catalog")
    def get_history_catalog():
        return svc.get_history_catalog()

    # ---------------------------------------------------------------- operator
    @app.get("/api/operator/actions")
    def operator_actions():
        return svc.operator_actions()

    @app.post("/api/operator/{action}")
    def operator_action(action: str, params: Dict[str, Any] = Body(default={})):
        return {"ok": True, "result": svc.operator_action(action, params, "operator")}

    # ---------------------------------------------------------------- scenarios
    @app.get("/api/scenarios")
    def list_scenarios():
        return svc.list_scenarios()

    @app.get("/api/scenarios/current")
    def current_scenario():
        return svc.current_scenario_with_faults()

    @app.get("/api/scenarios/{scenario_id}")
    def get_scenario(scenario_id: str):
        return svc.get_scenario(scenario_id)

    @app.post("/api/scenarios")
    def save_scenario(scenario: Dict[str, Any] = Body(...), overwrite: bool = False):
        return svc.save_scenario(scenario, overwrite)

    @app.post("/api/scenarios/{scenario_id}/duplicate")
    def duplicate_scenario(scenario_id: str, req: DuplicateRequest):
        return svc.duplicate_scenario(scenario_id, req.new_id, req.new_name)

    # ---------------------------------------------------------------- export
    @app.get("/api/export/json")
    def export_json():
        data = svc.export_json()
        rid = data["run_manifest"]["run_id"]
        return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{rid}.json"'})

    @app.get("/api/export/csv")
    def export_csv():
        rid = svc._eng().state.run["run_id"]
        return Response(svc.export_csv_zip(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{rid}.zip"'})

    # ---------------------------------------------------------------- UI polling snapshot
    @app.get("/api/ui/snapshot")
    def ui_snapshot(after_event: Optional[str] = None):
        with svc.lock:
            if svc.engine is None:
                return {"simulation": svc.get_simulation_state()}
            return {"simulation": svc.get_simulation_state(), "hierarchy": svc.get_hierarchy(),
                    "process": svc.get_process_image(), "alarms": svc.get_alarms(),
                    "utilities": svc.get_utilities(),
                    "equipment": {a: svc._eng().state.entity(a).properties for a in svc._eng().equipment.assets},
                    "production": svc.get_production(),
                    "events": svc.get_events(after_id=after_event, limit=300)}

    # ---------------------------------------------------------------- benchmark (fault injection)
    @app.get("/api/benchmark/faults/catalog")
    def fault_catalog():
        return bench.catalog()

    @app.get("/api/benchmark/faults")
    def list_faults():
        return bench.list_faults()

    @app.post("/api/benchmark/faults")
    def create_fault(spec: Dict[str, Any] = Body(...)):
        return bench.create_fault(spec)

    @app.get("/api/benchmark/faults/{fault_id}")
    def get_fault_status(fault_id: str):
        return bench.get_fault_status(fault_id)

    @app.post("/api/benchmark/faults/{fault_id}/schedule")
    def schedule_fault(fault_id: str, req: ScheduleRequest):
        return bench.schedule_fault(fault_id, req.start_time)

    @app.post("/api/benchmark/faults/{fault_id}/start")
    def start_fault(fault_id: str):
        return bench.start_fault(fault_id)

    @app.post("/api/benchmark/faults/{fault_id}/stop")
    def stop_fault(fault_id: str):
        return bench.stop_fault(fault_id)

    @app.post("/api/benchmark/faults/{fault_id}/reset")
    def reset_fault(fault_id: str):
        return bench.reset_fault(fault_id)

    @app.get("/api/benchmark/ground-truth")
    def ground_truth(limit: int = 500):
        return {"events": bench.ground_truth_events(limit), "effects": bench.fault_effects()}

    # ---------------------------------------------------------------- UI
    if UI_DIR.exists():
        app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")

        @app.get("/")
        def index():
            return FileResponse(str(UI_DIR / "index.html"))

    return app
