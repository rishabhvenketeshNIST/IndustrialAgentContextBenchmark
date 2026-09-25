# Adding a coupling

## Enterprise-internal relation

Add an entry to `configs/coupling.yaml → relations`:

```yaml
- id: CR-MY-RELATION
  category: utility_supply            # equipment_performance | power_distribution | utility_supply | tep_boundary | utility_observation
  description: What it models and why.
  inputs: {x: SOME-ENTITY.prop, loss: fault.SOME-ENTITY.capacity_loss}
  output: OTHER-ENTITY.prop
  expression: "x * (1 - loss)"
```

Rules enforced at start-up:
* every entity exists;
* each output is written by exactly one relation (no double writers);
* the graph has no cycles;
* expressions use only whitelisted syntax.

The category determines how causal labels propagate (`utility_observation` never propagates) and the
semantic edge type in the variable graph.

## New enterprise → TEP influence

Only through a boundary parameter.

1. **If a suitable parameter exists** in `simulator/tep/boundary.py`, write it with a `tep_boundary`
   relation. Use a form that gives exactly the TEINIT value when healthy, for example `nominal × fraction`
   with fraction clipped to 1, or `nominal + deviation`.
2. **If it does not exist:** identify a variable that TEFUNC reads as an input and does **not**
   recompute every call.
   * `VRNG`, `CPFLMX` and `XST(:,1..3)` qualify.
   * `SZERO(i)` qualifies as a walk mean.
   * `TCWR` and `TST(1)` do **not**: TEFUNC overwrites them.

   Then add a `BoundaryParameter` with its `locator`, plus handling in both `FortranTEPAdapter._set_boundary`
   / `_get_boundary` and `PythonTEPAdapter._set_boundary` / `_get_boundary` if it is a new locator
   kind. Document the physical meaning, the TEFUNC line that consumes it, and its limits.
3. Add its downstream measurements under `process_influences` so causal labels reach them.
4. **Test:** baseline equality (`test_coupling_baseline_is_native_and_graph_is_acyclic`), the
   healthy-native equivalence test, and a new test that an upstream change moves the parameter.
5. **Contract:** a new `tep_boundary` relation is a MINOR contract change. Add it to `tep_boundary.couplings`
   in `contract/canonical_contract.yaml` and to §17 of the [contract](../CANONICAL_SIMULATOR_CONTRACT.md);
   new output properties need an entry under `properties` (`tests/test_contract.py` fails otherwise).

**Never** add a coupling that writes XMEAS, a TEP state, XMV, SETPT or IDV.

Source:
- `configs/coupling.yaml`
- `simulator/coupling/__init__.py` — `CouplingEngine.setup`, `Ref.parse`
- `simulator/tep/boundary.py` — `BoundaryParameter`
- `simulator/tep/fortran_backend.py` — `FortranTEPAdapter._set_boundary`
