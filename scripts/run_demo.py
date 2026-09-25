#!/usr/bin/env python
"""Run a scenario headless (as fast as possible), print the causal timeline and export results.

    python scripts/run_demo.py                      # SCN-COOL-001, exports to exports/
    python scripts/run_demo.py --scenario SCN-BASELINE --backend python
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.export import build_bundle, build_csv_zip  # noqa: E402
from simulator.common import fmt_hms  # noqa: E402
from simulator.scenarios import ScenarioStore  # noqa: E402
from simulator.simulation.engine import SimulationEngine  # noqa: E402

TIMELINE = {"FAULT_STARTED", "FAULT_STOPPED", "EQUIPMENT_DEGRADED", "EQUIPMENT_STATE_CHANGED", "EQUIPMENT_FAILED",
            "EQUIPMENT_REPAIRED", "UTILITY_STATE_CHANGED", "ALARM_ACTIVATED", "MAINTENANCE_REQUESTED",
            "MAINTENANCE_STARTED", "MAINTENANCE_COMPLETED", "QUALITY_RESULT_CREATED", "LOT_STATE_CHANGED",
            "PROCESS_SHUTDOWN", "PRODUCTION_ORDER_COMPLETED", "PRODUCTION_STATE_CHANGED", "MATERIAL_SHORTAGE"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="SCN-COOL-001")
    ap.add_argument("--backend", choices=["auto", "fortran", "python"])
    ap.add_argument("--duration", type=int)
    ap.add_argument("--out", default=str(ROOT / "exports"))
    args = ap.parse_args()
    sc = ScenarioStore().load(args.scenario)
    if args.backend:
        sc["backend"] = args.backend
    e = SimulationEngine(sc, duration_override=args.duration)
    m = e.manifest()
    print(f"run {m['run_id']}  backend={m['tep_backend']}  seed={m['seed']}  duration={fmt_hms(e.duration_s)}")
    t0 = time.time()
    e.run()
    print(f"simulated {fmt_hms(e.clock.time_s)} in {time.time() - t0:.1f} s wall time\n")
    print("time      event                        target               detail")
    for ev in e.bus.log:
        if ev.type.value not in TIMELINE:
            continue
        if ev.type.value == "QUALITY_RESULT_CREATED" and ev.payload.get("overall") == "PASS":
            continue
        if ev.type.value == "LOT_STATE_CHANGED" and ev.payload.get("new") in ("IN_PROCESS", "AWAITING_QC"):
            continue
        p = ev.payload
        detail = p.get("message") or p.get("new") or p.get("description") or p.get("reason") or ""
        if p.get("failing_tests"):
            detail = f"FAIL {p['failing_tests']}"
        corr = f"  [corr {ev.correlation_id}]" if not ev.correlation_id.startswith("EV-") else ""
        print(f"{fmt_hms(ev.simulation_time)}  {ev.type.value:28s} {str(ev.target):20s} {detail}{corr}")
    st = e.state
    print(f"\nprocess shutdown: {st.process.shutdown} {st.process.shutdown_reason or ''}")
    print(f"production: {st.production['total_kg']:.0f} kg, accepted {st.production['accepted_kg']:.0f} kg, "
          f"rejected {st.production['rejected_kg']:.0f} kg")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rid = m["run_id"]
    (out / f"{rid}.json").write_text(json.dumps(build_bundle(e)), encoding="utf-8")
    (out / f"{rid}.zip").write_bytes(build_csv_zip(e))
    print(f"exported {out / (rid + '.json')} and {out / (rid + '.zip')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
