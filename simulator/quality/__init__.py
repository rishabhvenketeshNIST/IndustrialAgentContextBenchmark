"""Quality module: samples, tests against specifications, product and material lot disposition.

Product quality is computed from the TEP product composition (XMEAS 37-41, the
transmitted analyzer values) using configurable test expressions and limits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..common import ConfigError, fmt_hms
from ..common.expressions import Expression
from ..events import Event, EventType
from ..simulation.module import ModuleContext, SimulationModule
from ..tep import catalog


@dataclass
class TestSpec:
    id: str
    name: str
    unit: str
    inputs: Dict[str, int]          # variable name -> XMEAS index
    expression: Expression
    low: Optional[float] = None
    high: Optional[float] = None
    target: Optional[float] = None

    def evaluate(self, values: Dict[str, float]) -> float:
        return self.expression.evaluate(values)

    def passes(self, v: float) -> bool:
        return (self.low is None or v >= self.low) and (self.high is None or v <= self.high)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "unit": self.unit, "low": self.low, "high": self.high,
                "target": self.target, "expression": self.expression.source,
                "inputs": {k: f"XMEAS({v})" for k, v in self.inputs.items()}}


@dataclass
class QualitySample:
    sample_id: str
    source: str
    subject_type: str             # product | material
    subject_id: str               # product id or material lot id
    lot_id: Optional[str]
    taken_s: int
    represents_s: int
    results: List[dict] = field(default_factory=list)
    overall: str = "PENDING"

    def to_dict(self) -> dict:
        return {"sample_id": self.sample_id, "source": self.source, "subject_type": self.subject_type,
                "subject_id": self.subject_id, "lot_id": self.lot_id, "taken_s": self.taken_s,
                "taken_hms": fmt_hms(self.taken_s), "represents_s": self.represents_s,
                "results": list(self.results), "overall": self.overall}


class QualityModule(SimulationModule):
    name = "quality"

    def setup(self, ctx: ModuleContext) -> None:
        super().setup(ctx)
        cfg = ctx.cfg("quality")
        self.lab = cfg.get("laboratory", "WU-QC-LAB")
        self.sources = cfg.get("sample_sources", {})
        self.disposition = cfg.get("lot_disposition", {})
        self.incoming_delay = int(cfg.get("raw_material_inspection", {}).get("delay_s", 600))
        self.specs: Dict[str, List[TestSpec]] = {}
        for pid, pspec in (cfg.get("product_specifications") or {}).items():
            tests = []
            for t in pspec.get("tests", []):
                inputs = {k: int(catalog.normalize_id(v)[6:-1]) for k, v in t["inputs"].items()}
                tests.append(TestSpec(t["id"], t.get("name", t["id"]), t.get("unit", ""), inputs,
                                      Expression(t["expression"], inputs.keys()), t.get("low"), t.get("high"),
                                      t.get("target")))
                if t.get("low") is None and t.get("high") is None:
                    raise ConfigError(f"Quality test {t['id']} needs a low and/or high limit")
            self.specs[pid] = tests
        self._seq = 0
        self._pending_material: List[tuple] = []
        self._result_events: Dict[str, Event] = {}
        ctx.state.collection("quality_samples")
        if ctx.state.has_entity(self.lab):
            ctx.state.entity(self.lab).properties.update({"last_result": "NONE", "last_result_fail": 0.0,
                                                          "consecutive_failures": 0.0})
        ctx.bus.subscribe(self._on_material_received, [EventType.MATERIAL_RECEIVED])

    # ------------------------------------------------------------------ product samples
    def post_step(self, t: int) -> None:
        st = self.ctx.state
        for sname, src in self.sources.items():
            if not st.process.analyzer_updates.get(src.get("analyzer_group", "product")) or t == 0:
                continue
            if st.process.shutdown:
                continue
            self._product_sample(t, sname, src)
        self._decide_product_lots(t)
        self._inspect_materials(t)

    def _find_lot(self, when_s: int) -> Optional[str]:
        for lot in self.ctx.state.collection("production_lots").values():
            end = lot.end_s if lot.end_s is not None else 10 ** 12
            if lot.start_s <= when_s < end:
                return lot.lot_id
        return None

    def _product_sample(self, t: int, sname: str, src: dict) -> None:
        st = self.ctx.state
        pid = src.get("product_id")
        tests = self.specs.get(pid, [])
        self._seq += 1
        represents = t - int(src.get("dead_time_s", 0))
        lot_id = self._find_lot(represents)
        s = QualitySample(f"QS-{self._seq:05d}", src.get("analyzer", sname), "product", pid, lot_id, t, represents)
        self.ctx.bus.publish(EventType.QUALITY_SAMPLE_TAKEN, self.name, src.get("analyzer"),
                             {"sample_id": s.sample_id, "lot_id": lot_id, "product_id": pid})
        failing = []
        for ts in tests:
            vals = {k: float(st.process.xmeas[i - 1]) for k, i in ts.inputs.items()}
            v = ts.evaluate(vals) + st.fault_effects.value(ts.id, "test_offset")
            ok = ts.passes(v)
            s.results.append({"test_id": ts.id, "name": ts.name, "value": round(v, 5), "unit": ts.unit,
                              "low": ts.low, "high": ts.high, "target": ts.target, "pass": ok})
            if not ok:
                failing.append(ts.id)
        s.overall = "FAIL" if failing else "PASS"
        st.collection("quality_samples")[s.sample_id] = s
        if lot_id:
            st.collection("production_lots")[lot_id].samples.append(s.sample_id)
        props = st.entity(self.lab).properties if st.has_entity(self.lab) else {}
        props["last_result"] = s.overall
        props["last_result_fail"] = 1.0 if failing else 0.0
        props["consecutive_failures"] = (props.get("consecutive_failures", 0.0) + 1.0) if failing else 0.0
        for r in s.results:
            props[r["test_id"].lower()] = r["value"]
        ref = st.causal.first(["CM-AT-PRODUCT"] + [f"XMEAS({i})" for i in range(37, 42)]) if failing else None
        kw = {"correlation_id": ref.correlation_id, "causation_id": ref.event_id} if ref else {}
        self._result_events[s.sample_id] = self.ctx.bus.publish(
            EventType.QUALITY_RESULT_CREATED, self.name, s.sample_id,
            {"sample_id": s.sample_id, "lot_id": lot_id, "overall": s.overall, "failing_tests": failing,
             "results": {r["test_id"]: r["value"] for r in s.results}},
            severity="warning" if failing else "info", **kw)

    def _decide_product_lots(self, t: int) -> None:
        st = self.ctx.state
        samples = st.collection("quality_samples")
        for lot in sorted(st.collection("production_lots").values(), key=lambda l: l.lot_id):
            if lot.status != "AWAITING_QC" or lot.qc_due_s is None or t < lot.qc_due_s:
                continue
            res = [samples[sid] for sid in lot.samples if sid in samples]
            if not res:
                new, reason = self.disposition.get("no_samples", "QUARANTINE"), "no analyzer results for lot"
            else:
                f = sum(1 for s in res if s.overall == "FAIL") / len(res)
                if f <= float(self.disposition.get("release_if_failed_fraction_at_most", 0.0)):
                    new, reason = "RELEASED", f"{len(res)} samples, all within specification"
                elif f <= float(self.disposition.get("quarantine_if_failed_fraction_at_most", 0.25)):
                    new, reason = "QUARANTINE", f"{f:.0%} of {len(res)} samples out of specification"
                else:
                    new, reason = "REJECTED", f"{f:.0%} of {len(res)} samples out of specification"
            fails = [s for s in res if s.overall == "FAIL"]
            ref = self._result_events.get(fails[-1].sample_id) if fails else None
            old = lot.status
            lot.status = new
            self.ctx.bus.publish(EventType.LOT_STATE_CHANGED, self.name, lot.lot_id,
                                 {"lot_id": lot.lot_id, "lot_type": "product", "old": old, "new": new,
                                  "reason": reason, "quantity_kg": round(lot.quantity_kg, 1),
                                  "order_id": lot.order_id},
                                 cause=ref, severity="warning" if new != "RELEASED" else "info")

    # ------------------------------------------------------------------ raw materials
    def _on_material_received(self, ev: Event) -> None:
        if ev.payload.get("item_type") == "material" and ev.payload.get("lot_id"):
            self._pending_material.append((ev.simulation_time + self.incoming_delay, ev.payload["lot_id"], ev))

    def _inspect_materials(self, t: int) -> None:
        due = [p for p in self._pending_material if p[0] <= t]
        if not due:
            return
        self._pending_material = [p for p in self._pending_material if p[0] > t]
        st = self.ctx.state
        for _, lot_id, ev in due:
            lot = st.collection("material_lots").get(lot_id)
            if lot is None:
                continue
            spec = st.entity(lot.material_id).meta.get("specification", {})
            self._seq += 1
            s = QualitySample(f"QS-{self._seq:05d}", self.lab, "material", lot.material_id, lot_id, t, t)
            failing = []
            for attr, lim in sorted(spec.items()):
                v = float(lot.attributes.get(attr, 0.0)) + st.fault_effects.value(f"{lot.material_id}.{attr}",
                                                                                   "value")
                ok = (lim.get("min") is None or v >= lim["min"]) and (lim.get("max") is None or v <= lim["max"])
                s.results.append({"test_id": attr.upper(), "name": attr, "value": round(v, 6), "unit": "",
                                  "low": lim.get("min"), "high": lim.get("max"), "target": None, "pass": ok})
                if not ok:
                    failing.append(attr.upper())
            s.overall = "FAIL" if failing else "PASS"
            st.collection("quality_samples")[s.sample_id] = s
            self.ctx.bus.publish(EventType.QUALITY_RESULT_CREATED, self.name, s.sample_id,
                                 {"sample_id": s.sample_id, "lot_id": lot_id, "overall": s.overall,
                                  "failing_tests": failing, "subject": "raw_material"}, cause=ev)
            new = "REJECTED" if failing else "RELEASED"
            self.ctx.bus.publish(EventType.LOT_STATE_CHANGED, self.name, lot_id,
                                 {"lot_id": lot_id, "lot_type": "material", "old": lot.quality_status,
                                  "new": new, "reason": "incoming inspection"}, cause=ev)

    def summary(self) -> dict:
        return {"specifications": {pid: [t.to_dict() for t in ts] for pid, ts in self.specs.items()}}
