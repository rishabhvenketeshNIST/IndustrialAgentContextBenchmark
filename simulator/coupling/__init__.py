"""Coupling / causality engine.

Evaluates the declarative causal relations of ``configs/coupling.yaml`` once per
simulated second, in dependency (topological) order. Relations read canonical
state, process values, TEP boundary values and fault cause channels, and write
canonical-state properties or TEP boundary parameters. No causal relationship is
hard-coded elsewhere in the simulator.

It also propagates *causal context*: when a relation output deviates from its
baseline and one of its inputs carries a cause (e.g. a started fault), the
output entity inherits that cause. Boundary parameters pass their cause to the
TEP measurements/equipment listed under ``process_influences``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..common import ConfigError
from ..common.expressions import Expression
from ..simulation.module import ModuleContext, SimulationModule
from ..state import CauseRef
from ..tep import catalog
from ..tep.boundary import BOUNDARY_PARAMETERS

_VAR_RE = re.compile(r"^(XMEAS|XMV)\((\d+)\)$")


@dataclass(frozen=True)
class Ref:
    kind: str            # entity | process | boundary | nominal | fault
    a: str               # entity id / var id / parameter / fault target
    b: str = ""          # property / channel

    @classmethod
    def parse(cls, text: str) -> "Ref":
        s = str(text).strip()
        if s.startswith("process."):
            var = catalog.normalize_id(s[len("process."):])
            if not _VAR_RE.match(var):
                raise ConfigError(f"Bad process reference '{text}' (use process.XMEAS(n) or process.XMV(n))")
            return cls("process", var)
        if s.startswith("tep.boundary."):
            name = s[len("tep.boundary."):]
            if name not in BOUNDARY_PARAMETERS:
                raise ConfigError(f"Unknown TEP boundary parameter in '{text}'")
            return cls("boundary", name)
        if s.startswith("tep.nominal."):
            name = s[len("tep.nominal."):]
            if name not in BOUNDARY_PARAMETERS:
                raise ConfigError(f"Unknown TEP boundary parameter in '{text}'")
            return cls("nominal", name)
        if s.startswith("fault."):
            rest = s[len("fault."):]
            if "." not in rest:
                raise ConfigError(f"Bad fault reference '{text}' (fault.<TARGET>.<channel>)")
            target, channel = rest.rsplit(".", 1)
            return cls("fault", target, channel)
        if "." not in s:
            raise ConfigError(f"Bad reference '{text}' (expected ENTITY.property)")
        ent, prop = s.split(".", 1)
        return cls("entity", ent, prop)

    @property
    def key(self) -> str:
        """Key used for dependency ordering and causal context."""
        if self.kind == "entity":
            return f"{self.a}.{self.b}"
        if self.kind == "boundary":
            return f"tep.boundary.{self.a}"
        if self.kind == "process":
            return self.a
        if self.kind == "fault":
            return f"fault.{self.a}.{self.b}"
        return f"tep.nominal.{self.a}"

    @property
    def cause_key(self) -> str:
        """Key in the causal registry (entity granularity)."""
        if self.kind in ("entity", "fault"):
            return self.a
        if self.kind == "boundary":
            return f"tep.boundary.{self.a}"
        return self.a

    def __str__(self) -> str:
        return self.key if self.kind != "entity" else f"{self.a}.{self.b}"


@dataclass
class Relation:
    id: str
    description: str
    category: str
    inputs: Dict[str, Ref]
    output: Ref
    expression: Expression
    template_id: str
    baseline: Optional[float] = None
    last_value: Optional[float] = None

    def to_dict(self) -> dict:
        return {"id": self.id, "template_id": self.template_id, "description": self.description,
                "category": self.category, "inputs": {k: str(v) for k, v in self.inputs.items()},
                "output": str(self.output), "expression": self.expression.source,
                "baseline": self.baseline, "value": self.last_value}


def _expand(spec: dict) -> List[dict]:
    items = spec.get("for_each")
    if not items:
        return [spec]
    out = []
    for item in items:
        def sub(x):
            return x.replace("{id}", item) if isinstance(x, str) else x
        s = {k: v for k, v in spec.items() if k != "for_each"}
        s["id"] = f"{spec['id']}[{item}]"
        s["inputs"] = {k: sub(v) for k, v in spec.get("inputs", {}).items()}
        s["output"] = sub(spec["output"])
        s["_template"] = spec["id"]
        out.append(s)
    return out


class CouplingEngine(SimulationModule):
    name = "coupling"
    DEVIATION_REL = 0.005

    def setup(self, ctx: ModuleContext) -> None:
        super().setup(ctx)
        cfg = ctx.cfg("coupling")
        rels: List[Relation] = []
        seen = set()
        for raw in cfg.get("relations", []):
            for spec in _expand(raw):
                rid = spec["id"]
                if rid in seen:
                    raise ConfigError(f"Duplicate relation id {rid}")
                seen.add(rid)
                inputs = {name: Ref.parse(r) for name, r in (spec.get("inputs") or {}).items()}
                output = Ref.parse(spec["output"])
                if output.kind not in ("entity", "boundary"):
                    raise ConfigError(f"{rid}: output must be an entity property or tep.boundary.*")
                expr = Expression(spec["expression"], variables=inputs.keys())
                rels.append(Relation(rid, spec.get("description", "").strip(), spec.get("category", "general"),
                                     inputs, output, expr, spec.get("_template", rid)))
        self.relations = self._toposort(rels)
        self.influences: Dict[str, dict] = cfg.get("process_influences", {}) or {}
        for name in self.influences:
            if name not in BOUNDARY_PARAMETERS:
                raise ConfigError(f"process_influences references unknown boundary '{name}'")
        self._validate_entities()
        self._nominal = ctx.process.boundary_nominal
        self._propagated: Dict[str, str] = {}   # output cause key -> relation id that set it
        # baseline evaluation (t = 0) - also initialises all derived properties
        self.evaluate(record_baseline=True)

    # ------------------------------------------------------------------ structure
    @staticmethod
    def _toposort(rels: List[Relation]) -> List[Relation]:
        producers: Dict[str, Relation] = {}
        for r in rels:
            k = r.output.key
            if k in producers:
                raise ConfigError(f"Both {producers[k].id} and {r.id} write {k}")
            producers[k] = r
        order: List[Relation] = []
        state: Dict[str, int] = {}

        def visit(r: Relation, stack: Tuple[str, ...]) -> None:
            if state.get(r.id) == 2:
                return
            if state.get(r.id) == 1:
                raise ConfigError(f"Causal cycle: {' -> '.join(stack + (r.id,))}")
            state[r.id] = 1
            for ref in r.inputs.values():
                p = producers.get(ref.key)
                if p is not None:
                    visit(p, stack + (r.id,))
            state[r.id] = 2
            order.append(r)

        for r in rels:  # config order keeps the result deterministic
            visit(r, ())
        return order

    def _validate_entities(self) -> None:
        st = self.ctx.state
        for r in self.relations:
            for ref in list(r.inputs.values()) + [r.output]:
                if ref.kind == "entity" and not st.has_entity(ref.a):
                    raise ConfigError(f"{r.id}: unknown entity '{ref.a}'")

    # ------------------------------------------------------------------ evaluation
    def _read(self, ref: Ref) -> float:
        st = self.ctx.state
        if ref.kind == "entity":
            v = st.get(ref.a, ref.b)
            if v is None:
                raise ConfigError(f"Property {ref.a}.{ref.b} has no value (not produced by any module/relation)")
            return float(v)
        if ref.kind == "process":
            m = _VAR_RE.match(ref.a)
            idx = int(m.group(2)) - 1
            return float(st.process.xmeas[idx] if m.group(1) == "XMEAS" else st.process.xmv[idx])
        if ref.kind == "boundary":
            return float(self.ctx.process.get_boundary(ref.a))
        if ref.kind == "nominal":
            return float(self._nominal[ref.a])
        return st.fault_effects.value(ref.a, ref.b)

    def evaluate(self, record_baseline: bool = False) -> None:
        st = self.ctx.state
        for r in self.relations:
            values = {name: self._read(ref) for name, ref in r.inputs.items()}
            v = r.expression.evaluate(values)
            if r.output.kind == "entity":
                st.set(r.output.a, r.output.b, v)
            else:
                self.ctx.process.set_boundary(r.output.a, v)
                v = self.ctx.process.get_boundary(r.output.a)   # after clamping
            r.last_value = v
            if record_baseline:
                r.baseline = v
            else:
                self._propagate_cause(r, v)

    def pre_step(self, t: int) -> None:
        self.evaluate()

    # ------------------------------------------------------------------ causal context
    def _deviates(self, r: Relation, v: float) -> bool:
        b = r.baseline if r.baseline is not None else v
        return abs(v - b) > max(1e-9, self.DEVIATION_REL * max(abs(b), 1e-6))

    def _propagate_cause(self, r: Relation, v: float) -> None:
        if r.category == "utility_observation" and r.output.kind == "entity":
            # observations follow the process; they don't create new causal paths
            return
        causal = self.ctx.state.causal
        out_key = r.output.cause_key
        if self._deviates(r, v):
            ref = causal.first(i.cause_key for i in r.inputs.values())
            if ref is not None and causal.get(out_key) is None:
                causal.set(out_key, CauseRef(ref.event_id, ref.correlation_id, self.ctx.clock.time_s,
                                             f"coupling:{r.id}"))
                self._propagated[out_key] = r.id
                if r.output.kind == "boundary":
                    infl = self.influences.get(r.output.a, {})
                    for k in list(infl.get("measurements", [])) + list(infl.get("manipulated", [])) + \
                            list(infl.get("equipment", [])):
                        causal.set_if_absent(k, CauseRef(ref.event_id, ref.correlation_id, self.ctx.clock.time_s,
                                                         f"tep:{r.output.a}"))
        elif self._propagated.get(out_key) == r.id:
            causal.clear(out_key)
            del self._propagated[out_key]

    # ------------------------------------------------------------------ introspection
    def graph(self) -> dict:
        nodes: Dict[str, dict] = {}
        edges = []

        def node(ref: Ref) -> str:
            nid = ref.key
            if nid not in nodes:
                nodes[nid] = {"id": nid, "kind": ref.kind, "entity": ref.a if ref.kind == "entity" else None}
            return nid

        for r in self.relations:
            out = node(r.output)
            for ref in r.inputs.values():
                if ref.kind == "nominal":
                    continue
                edges.append({"from": node(ref), "to": out, "relation": r.id, "category": r.category})
        for bname, infl in self.influences.items():
            src = f"tep.boundary.{bname}"
            if src not in nodes:
                nodes[src] = {"id": src, "kind": "boundary", "entity": None}
            for m in infl.get("measurements", []):
                if m not in nodes:
                    nodes[m] = {"id": m, "kind": "process", "entity": None}
                edges.append({"from": src, "to": m, "relation": "TEP physics", "category": "tep_process"})
        return {"nodes": list(nodes.values()), "edges": edges}

    def summary(self) -> dict:
        return {"relations": [r.to_dict() for r in self.relations]}
