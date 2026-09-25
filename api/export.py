"""Simulation output export (JSON bundle / CSV zip). Not a historian: exports a finished
or in-progress run's in-memory results on demand."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from typing import Any, Dict, List

from simulator.common import to_jsonable
from simulator.tep import catalog
from simulator.tep import control_scheme as cs


def _rows_to_csv(rows: List[Dict[str, Any]], columns: List[str] = None) -> str:
    buf = io.StringIO()
    if not rows:
        return ""
    cols = columns or sorted({k for r in rows for k in r})
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: (json.dumps(to_jsonable(v)) if isinstance(v, (dict, list)) else v) for k, v in r.items()})
    return buf.getvalue()


def build_bundle(engine) -> Dict[str, Any]:
    st = engine.state
    times, series = engine.history.to_columns()

    def table(prefixes):
        names = [n for n in engine.history.series_names() if any(n.startswith(p) for p in prefixes)]
        return {"time_s": times, **{n: series[n] for n in names}}

    return to_jsonable({
        "run_manifest": engine.run_manifest(),
        "events": [e.to_dict() for e in engine.bus.log],
        "measurements": table(["XMEAS(", "TRUE:XMEAS("]),
        "measurement_catalog": [v.to_dict() for v in catalog.XMEAS],
        "manipulated_variables": table(["XMV("]),
        "setpoints": table(["SP:"]),
        "equipment_states": {"trend": table([f"{a}." for a in sorted(engine.equipment.assets)]),
                             "final": {a: st.entity(a).properties for a in sorted(engine.equipment.assets)}},
        "utilities": {"trend": table([f"{u}." for u in sorted(engine.utilities.services)]),
                      "final": {u: st.entity(u).properties for u in sorted(engine.utilities.services)}},
        "alarms": [a.to_dict() for a in st.collection("alarms").values() if a.count > 0],
        "faults": [f.to_dict() for f in st.collection("faults").values()],
        "maintenance": {"work_orders": [w.to_dict() for w in st.collection("work_orders").values()],
                        "technicians": [t.to_dict() for t in st.collection("technicians").values()]},
        "inventory": {"storage": engine.inventory.summary()["storage"],
                      "material_lots": [l.to_dict() for l in st.collection("material_lots").values()],
                      "spare_parts": [p.to_dict() for p in st.collection("spare_parts").values()],
                      "purchase_orders": [p.to_dict() for p in st.collection("purchase_orders").values()],
                      "levels": table(["SU-"])},
        "quality": {"samples": [s.to_dict() for s in st.collection("quality_samples").values()],
                    "production_lots": [l.to_dict() for l in st.collection("production_lots").values()]},
        "production_orders": [o.to_dict() for o in st.collection("production_orders").values()],
        "production": st.production,
        "loops": [{"loop_id": l, "tag": d.tag, "name": d.name} for l, d in cs.LOOPS.items()],
    })


def _wide(table: Dict[str, list]) -> str:
    cols = list(table.keys())
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for i in range(len(table["time_s"])):
        w.writerow([table[c][i] for c in cols])
    return buf.getvalue()


def build_csv_zip(engine) -> bytes:
    b = build_bundle(engine)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("run_manifest.json", json.dumps(b["run_manifest"], indent=2))
        z.writestr("events.csv", _rows_to_csv(b["events"], ["event_id", "timestamp", "simulation_time", "type",
                                                            "source", "target", "severity", "visibility",
                                                            "correlation_id", "causation_id", "payload"]))
        z.writestr("measurements.csv", _wide(b["measurements"]))
        z.writestr("manipulated_variables.csv", _wide(b["manipulated_variables"]))
        z.writestr("setpoints.csv", _wide(b["setpoints"]))
        z.writestr("equipment_states.csv", _wide(b["equipment_states"]["trend"]))
        z.writestr("utilities.csv", _wide(b["utilities"]["trend"]))
        z.writestr("inventory_levels.csv", _wide(b["inventory"]["levels"]))
        z.writestr("alarms.csv", _rows_to_csv(b["alarms"]))
        z.writestr("faults.csv", _rows_to_csv(b["faults"]))
        z.writestr("work_orders.csv", _rows_to_csv(b["maintenance"]["work_orders"]))
        z.writestr("material_lots.csv", _rows_to_csv(b["inventory"]["material_lots"]))
        z.writestr("purchase_orders.csv", _rows_to_csv(b["inventory"]["purchase_orders"]))
        z.writestr("spare_parts.csv", _rows_to_csv(b["inventory"]["spare_parts"]))
        z.writestr("quality_samples.csv", _rows_to_csv(b["quality"]["samples"]))
        z.writestr("production_lots.csv", _rows_to_csv(b["quality"]["production_lots"]))
        z.writestr("production_orders.csv", _rows_to_csv(b["production_orders"]))
    return out.getvalue()
