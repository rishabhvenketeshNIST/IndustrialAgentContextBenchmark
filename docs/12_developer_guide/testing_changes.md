# Testing changes

## Always

```bash
python -m pytest                          # 83 tests, ~1 min, needs the Fortran library for full coverage
python scripts/check_docs.py              # doc links and source references still resolve
```

## Which tests protect what (quick map)

| If you change … | Watch these tests |
|---|---|
| anything in `simulator/tep/` | `tests/test_tep_adapter.py`, `test_healthy_enterprise_reproduces_native_tep_exactly` |
| `configs/coupling.yaml` or equipment ratings | `test_coupling_baseline_is_native_and_graph_is_acyclic`, `test_healthy_enterprise_reproduces_native_tep_exactly`, `test_equipment_degradation_propagates_through_coupling_to_tep` |
| module order, random draws, event publishing | `test_same_scenario_same_seed_identical_results`, `test_fault_injection_is_deterministic`, `test_reset_returns_to_initial_state` |
| demo scenario, maintenance, alarms, quality | `test_demo_scenario_causal_chain` |
| API routes | `tests/test_api_ui.py`, especially `test_fault_injection_is_benchmark_only`; classify every new route in `contract/canonical_contract.yaml` (`test_every_api_route_is_classified`) |
| event types, entity properties, collections, TEP couplings | `tests/test_contract.py`: update the canonical contract deliberately |

## Determinism hygiene

* **Draw randomness only from `ctx.rng.get("<module>")`.** Never use `random` or unseeded numpy.
* **Iterate over sorted keys or ids** when order matters.
* **Never read the wall clock** in simulation code.
* **Add new random draws at the end of a module's existing sequence where possible.** Inserting draws
  changes that module's later numbers, but not other modules'.

## After behaviour changes

Regenerate the evidence-based docs, and check whether numbers quoted in hand-written docs (demo times,
peak values) still match the regenerated trace:

```bash
python scripts/generate_docs_tables.py
python scripts/build_variable_graph.py
```

Source:
- `tests/conftest.py`
- `scripts/check_docs.py`
