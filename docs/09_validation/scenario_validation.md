# Scenario validation

| Scenario | Automated checks | Manual observations recorded in the docs |
|---|---|---|
| SCN-COOL-001 | `test_demo_scenario_causal_chain` (ordering, correlation, thresholds, recovery, quality); `test_same_scenario_same_seed_identical_results` (2 h) | full timeline, peak values, wind-up, undershoot, incomplete recovery ([trace](../06_scenarios/scenario_examples/SCN-COOL-001_trace.md)); trip with maintenance blocked |
| SCN-BASELINE | loads and runs 120 s; reset test | no alarms, both orders complete, D replenishment |
| SCN-FAULT-LIBRARY | loads and runs 120 s; every manual fault starts and stops | cooling-tower planned maintenance raises the CW supply temperature by 12 °C |

**Scenario sensitivity.** The demo outcome depends on parameters near a threshold. Probing during
development found that reactor CW capacity losses up to ≈ 58 % are absorbed by control, ≈ 60 %
saturate the valve without a trip, and ≈ 62 % trip without intervention. The test asserts only that
the demo, with its specific seed and timing, does not trip. If you change severity, seed, maintenance
response or technician availability, re-validate.

Source:
- `tests/test_reproducibility.py` — `test_demo_scenario_causal_chain`, `test_every_library_scenario_loads_and_runs`, `test_every_library_fault_can_start_and_stop`
