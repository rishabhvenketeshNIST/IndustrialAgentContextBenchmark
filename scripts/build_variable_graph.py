#!/usr/bin/env python
"""Build the TEP Site Variable Graph (documentation / explanation tooling).

Writes:
  docs/07_variable_graph/variable_graph.json   nodes + edges with display kind and semantic type
  docs/07_variable_graph/variable_graph.html   standalone interactive viewer (open in a browser)

The graph is assembled from four sources of different authority - see
docs/07_variable_graph/graph_generation.md:

  1. CouplingEngine.graph()  -> relations executed every step (configs/coupling.yaml)
                                plus the documented `process_influences` (NOT executed; TEP computes them)
  2. control_scheme.LOOPS    -> native loop wiring transcribed from temain_mod.f
  3. DIRECT_VALVE_EFFECTS    -> direct TEFUNC effect of each XMV (read from teprob.f equations)
  4. MODULE_EDGES            -> hand-written summary of module logic (inventory, quality, production,
                                equipment, maintenance); these are NOT generated from code

Running this script does not change the simulator.
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.scenarios import ScenarioStore  # noqa: E402
from simulator.simulation.engine import SimulationEngine  # noqa: E402
from simulator.tep import catalog  # noqa: E402
from simulator.tep import control_scheme as cs  # noqa: E402
from simulator.tep.boundary import BOUNDARY_PARAMETERS  # noqa: E402

OUT_DIR = ROOT / "docs" / "07_variable_graph"
TEMPLATE = ROOT / "scripts" / "variable_graph_template.html"
CONST = re.compile(r"\.(rated_|nominal_)|^UT-[A-Z-]+\.capacity$")   # constants omitted from the graph

# Semantic edge types (docs/07_variable_graph/edge_types.md)
SEM_BY_RELATION_CATEGORY = {
    "equipment_performance": "capability_constraint",
    "power_distribution": "supply_dependency",
    "utility_supply": "supply_dependency",
    "tep_boundary": "tep_boundary_interface",
    "utility_observation": "derivation",
}

# Direct TEFUNC effect of each manipulated variable (teprob.f); XMV index -> (XMEAS indices, equation)
DIRECT_VALVE_EFFECTS = {
    1: ([2], "FTM(1)=VPOS(1)*VRNG(1)/100"), 2: ([3], "FTM(2)=VPOS(2)*VRNG(2)/100"),
    3: ([1], "FTM(3)=VPOS(3)*(1-IDV(6))*VRNG(3)/100"), 4: ([4], "FTM(4)=VPOS(4)*(1-IDV(7)*0.2)*VRNG(4)/100"),
    5: ([5], "recycle flow: FLMS - VPOS(5)*53.349*sqrt(DLP)"), 6: ([10], "purge FTM(10) from VPOS(6)"),
    7: ([14], "separator underflow FTM(11)=VPOS(7)*VRNG(7)/100"),
    8: ([17], "stripper product FTM(13)=VPOS(8)*VRNG(8)/100"), 9: ([19], "UAC=VPOS(9)*VRNG(9)*(...); QUC"),
    10: ([21], "FWR=VPOS(10)*VRNG(10)/100 -> YP(37)"), 11: ([22], "FWS=VPOS(11)*VRNG(11)/100 -> YP(38)"),
    12: ([21, 9], "AGSP=(VPOS(12)+150)/100 -> UAR -> QUR"),
}
EXTRA_PHYSICS = [
    ("XMEAS(21)", "XMEAS(9)", "QUR = UAR*(TWR-TCR) couples cooling-water and reactor temperature"),
    ("XMEAS(9)", "XMEAS(40)", "reaction rates RR(1), RR(2) depend on TKR and set the G/H selectivity"),
    ("XMEAS(9)", "XMEAS(7)", "component vapour pressures depend on reactor temperature"),
]
FEED_STORAGE = {1: "SU-SPH-103", 2: "SU-TK-101", 3: "SU-TK-102", 4: "SU-SPH-104"}
QUALITY_TESTS = {"G_MASS_PCT": [40, 41], "GH_PURITY": [40, 41], "E_IMPURITY": [38], "F_BYPRODUCT": [39]}


def build(engine: SimulationEngine) -> dict:
    st = engine.state
    rels = {str(r.output): r for r in engine.coupling.relations}
    nodes, edges = {}, []

    def name(eid):
        return st.entities[eid].name if eid in st.entities else eid

    def add(nid, layer, cat, label, sub="", detail="", authority=""):
        if nid not in nodes:
            nodes[nid] = {"id": nid, "layer": layer, "cat": cat, "label": label, "sub": sub, "detail": detail,
                          "authority": authority}
        return nid

    def node(nid):
        if nid.startswith("fault."):
            t, c = nid[6:].rsplit(".", 1)
            return add(nid, 0, "fault", c.replace("_", " "), name(t),
                       f"Fault cause channel written only by the benchmark fault engine: {c} on {t}.",
                       "benchmark ground truth (simulator/faults/effects.py)")
        if nid.startswith("tep.boundary."):
            n = nid[13:]
            p = BOUNDARY_PARAMETERS[n]
            return add(nid, 3, "boundary", f"{p.fortran_symbol} {n.replace('_', ' ')}", "TEP boundary parameter",
                       f"{p.description}. Used in TEFUNC: {p.teprob_usage}. TEINIT value {p.nominal} ({p.unit}).",
                       "TEP common block, written only by the coupling engine")
        m = re.match(r"^(XMEAS|XMV)\((\d+)\)$", nid)
        if m:
            v = catalog.variable(nid)
            if m.group(1) == "XMV":
                return add(nid, 4, "xmv", f"{nid} {v.name}", "manipulated variable, %",
                           f"{v.name}: valve or drive position in %, written by a native controller or the operator.",
                           "TEP /PV/ XMV")
            extra = f", sampled every {v.sample_period_h} h with {v.dead_time_h} h dead time" if v.sample_period_h else ""
            return add(nid, 5, "xmeas", f"{nid} {v.name}", v.unit,
                       f"{v.name} [{v.unit}], {v.measurement_type.value}{extra}. Computed by TEFUNC.",
                       "TEP /PV/ XMEAS (true); transmitted copy via instrumentation")
        eid, prop = nid.split(".", 1)
        r = rels.get(nid)
        detail = ((r.description + " ") if r and r.description else "") + (f"{prop} = {r.expression.source}" if r else "")
        if r and r.category == "utility_observation":
            layer, cat = 7, "utility"
        elif eid.startswith("UT-"):
            layer, cat = 2, "utility"
        elif eid.startswith("SU-"):
            layer, cat = 2, "material"
        else:
            layer, cat = 1, "equipment"
        if not detail:
            detail = {"health": "Asset condition 0..1 owned by the equipment module: wear, fault damage, repair.",
                      "is_running": "1 when the asset runs: equipment state machine, changeover, maintenance isolation."
                      }.get(prop, "Owned by its enterprise module.")
        auth = f"coupling relation {r.id}" if r else "enterprise module state"
        return add(nid, layer, cat, prop.replace("_", " "), name(eid), detail, auth)

    def edge(s, t, kind, sem, why, source):
        edges.append({"s": s, "t": t, "kind": kind, "semantics": sem, "why": why, "source": source})

    # 1. executed coupling relations + documented process influences
    g = engine.coupling.graph()
    for e in g["edges"]:
        a, b = e["from"], e["to"]
        if CONST.search(a) or CONST.search(b):
            continue
        if e["category"] == "tep_process":
            edge(node(a), node(b), "physics", "process_response",
                 "documented consequence inside TEP (process_influences; not executed by Python)",
                 "configs/coupling.yaml#process_influences")
        elif a.startswith("fault."):
            edge(node(a), node(b), "coupling", "cause_injection", f"fault cause read by relation {e['relation']}",
                 "configs/coupling.yaml")
        else:
            edge(node(a), node(b), "coupling", SEM_BY_RELATION_CATEGORY.get(e["category"], "derivation"),
                 f"coupling relation {e['relation']}", "configs/coupling.yaml")

    # 2. native control loops (temain_mod.f)
    for lid in cs.EXECUTION_ORDER:
        d = cs.LOOPS[lid]
        ln = add(f"LOOP:{d.tag}", 6, "control", d.tag, d.name,
                 f"CONTRL{lid}: {d.algorithm}, gain {d.gain:.4g}" + (f", Ti {d.taui_h:.4g} h" if d.taui_h else "") +
                 f", runs every {d.period_steps} s, setpoint SETPT({d.sp}).", "native Fortran subroutine")
        edge(node(f"XMEAS({d.pv})"), ln, "control", "control_measurement", f"process value of CONTRL{lid}",
             "simulator/tep/control_scheme.py")
        if d.output_kind == "XMV":
            edge(ln, node(f"XMV({d.output_index})"), "control", "control_actuation", "controller output",
                 "simulator/tep/control_scheme.py")
    for lid in cs.EXECUTION_ORDER:
        d = cs.LOOPS[lid]
        if d.output_kind == "SETPT":
            slave = cs.LOOPS[cs.loop_for_setpoint(d.output_index)]
            edge(f"LOOP:{d.tag}", f"LOOP:{slave.tag}", "control", "control_cascade",
                 f"cascade: writes the setpoint SETPT({d.output_index})", "simulator/tep/control_scheme.py")

    # 3. direct TEFUNC effects of each valve (teprob.f)
    for x, (ms, eq) in DIRECT_VALVE_EFFECTS.items():
        for m in ms:
            edge(node(f"XMV({x})"), node(f"XMEAS({m})"), "physics", "process_response", f"TEFUNC: {eq}",
                 "simulator/tep/fortran/src/teprob.f")
    for a, b, why in EXTRA_PHYSICS:
        edge(node(a), node(b), "physics", "process_response", f"TEFUNC: {why}", "simulator/tep/fortran/src/teprob.f")

    # 4. module logic (hand-written summary of the module code)
    for x, su in FEED_STORAGE.items():
        c = add(f"{su}.consumption_kg_h", 7, "material", "consumption kg/h", name(su),
                "Inventory module: true feed flow x TEFUNC unit conversion, drawn FIFO from released lots.",
                "InventoryModule.post_step")
        q = add(f"{su}.quantity_kg", 7, "material", "quantity kg", name(su),
                "Stock on hand: falls with consumption, rises with deliveries.", "InventoryModule")
        edge(node(f"XMEAS({x})"), c, "module", "derivation", "inventory metering (true flow)",
             "simulator/inventory/__init__.py")
        edge(c, q, "module", "state_transition", "FIFO consumption", "simulator/inventory/__init__.py")
        edge(q, node(f"{su}.supply_availability"), "module", "derivation",
             "heel + ramp: supply is lost when the storage runs empty", "simulator/inventory/__init__.py")
    lot = add("LOT.disposition", 7, "enterprise", "lot disposition", "Quality",
              "RELEASED, QUARANTINE or REJECTED from the share of failing samples in the lot.",
              "QualityModule._decide_product_lots")
    for t, xs in QUALITY_TESTS.items():
        n = add(f"QT:{t}", 7, "enterprise", t, "quality test",
                "Test from configs/quality.yaml, evaluated on every product analyzer result.",
                "QualityModule._product_sample")
        for x in xs:
            edge(node(f"XMEAS({x})"), n, "module", "derivation", "quality test input (transmitted value)",
                 "configs/quality.yaml")
        edge(n, lot, "module", "state_transition", "sample PASS/FAIL", "simulator/quality/__init__.py")
    rate = add("PROD.rate_kg_h", 7, "enterprise", "production rate", "Production",
               "XMEAS(17) m3/h x 613.4 kg/m3 (Downs and Vogel base case); zero after a trip.",
               "ProductionModule.post_step")
    orders = add("PROD.order_progress", 7, "enterprise", "order progress", "Production",
                 "Produced minus rejected kg, credited to the running production order.", "ProductionModule")
    edge(node("XMEAS(17)"), rate, "module", "derivation", "metered production (transmitted value)",
         "simulator/production/__init__.py")
    edge(rate, orders, "module", "state_transition", "order progress", "simulator/production/__init__.py")
    edge(lot, orders, "module", "state_transition", "rejected lots reduce the net quantity",
         "simulator/production/__init__.py")
    vib = add("WU-CWP-101A.vibration", 1, "equipment", "vibration", name("WU-CWP-101A"),
              "Observable condition indicator: 1.8 + 10*(1-health)^2 mm/s (+ noise while running).",
              "EquipmentModule._resolve")
    wo = add("MAINT.work_order", 7, "enterprise", "work order", "Maintenance",
             "Raised by vibration or utility alarms and by failures; needs a technician and parts, then repairs.",
             "MaintenanceModule")
    edge(node("WU-CWP-101A.health"), vib, "module", "derivation", "condition monitoring",
         "simulator/equipment/__init__.py")
    edge(vib, wo, "event", "event_trigger", "alarm VAH-CWP101A raises a corrective work order",
         "configs/alarms.yaml")
    edge(node("UT-CW-REACTOR.utilization"), wo, "event", "event_trigger", "alarm UA-CWRX-CAP raises an inspection",
         "configs/alarms.yaml")
    edge(wo, node("WU-CWP-101A.health"), "event", "state_transition", "repair restores health",
         "simulator/equipment/__init__.py")
    edge(wo, node("WU-CWP-101B.is_running"), "event", "state_transition", "planned changeover to the standby pump",
         "simulator/equipment/__init__.py")
    for nid in list(nodes):
        if nid.endswith(".health") and nodes[nid]["layer"] == 1:
            eid = nid.split(".")[0]
            f = add(f"fault.{eid}.damage", 0, "fault", "damage", name(eid),
                    f"Fault cause channel: health loss on {eid} (degradation faults).",
                    "benchmark ground truth (simulator/faults/effects.py)")
            edge(f, nid, "module", "cause_injection", "health = intrinsic health - fault damage",
                 "simulator/equipment/__init__.py")

    seen, E = set(), []
    for e in edges:
        k = (e["s"], e["t"])
        if k not in seen and e["s"] in nodes and e["t"] in nodes and e["s"] != e["t"]:
            seen.add(k)
            E.append(e)
    used = {e["s"] for e in E} | {e["t"] for e in E}
    N = [n for n in nodes.values() if n["id"] in used]
    return {"nodes": N, "edges": E,
            "stats": {"nodes": len(N), "edges": len(E),
                      "by_kind": dict(sorted(collections.Counter(e["kind"] for e in E).items())),
                      "by_semantics": dict(sorted(collections.Counter(e["semantics"] for e in E).items())),
                      "nodes_by_category": dict(sorted(collections.Counter(n["cat"] for n in N).items())),
                      "nodes_by_layer": dict(sorted(collections.Counter(n["layer"] for n in N).items()))}}


def main() -> None:
    engine = SimulationEngine(ScenarioStore().load("SCN-BASELINE"), duration_override=1)
    graph = build(engine)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "variable_graph.json").write_text(json.dumps(graph, indent=1), encoding="utf-8")
    if TEMPLATE.exists():
        payload = json.dumps({"nodes": graph["nodes"], "edges": graph["edges"]}).replace("</", "<\\/")
        html = TEMPLATE.read_text(encoding="utf-8").replace("__GRAPH__", payload)
        (OUT_DIR / "variable_graph.html").write_text(
            "<!doctype html><html><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"></head><body>"
            + html + "</body></html>", encoding="utf-8")
    print(json.dumps(graph["stats"], indent=1))


if __name__ == "__main__":
    main()
