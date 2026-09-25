# SCN-FAULT-LIBRARY: example definitions of every fault type

`scenarios/SCN-FAULT-LIBRARY.yaml`: seed 4242, 8 h, two production orders, one planned maintenance.

| Fault id | Type | Target | Trigger | Notes |
|---|---|---|---|---|
| F-IDV4-STEP | IDV4 | TEP | 3600 s, 1 h | reactor CW inlet +5 °C step (native) |
| F-IDV14-STICK | IDV14 | TEP | 14400 s, 1 h, intermittent 1200 s / 50 % | reactor CW valve sticking (native) |
| F-EQ-DEGR | equipment_degradation | EM-COMPRESSOR | manual | severity 0.4, gradual 1 h |
| F-PUMP-EFF | pump_efficiency_loss | WU-CWP-201A | manual | 0.6, gradual 30 min, hidden (no vibration) |
| F-FAIL-CWP | equipment_failure | WU-CWP-101A | manual | failure → auto-changeover |
| F-COOL-CT | cooling_degradation | WU-CT-101 | manual | thermal degradation 0.8 |
| F-SENS-BIAS | sensor_bias | EM-REACTOR.temperature (XMEAS 9) | manual | +2 °C |
| F-SENS-DRIFT | sensor_drift | XMEAS(12) | manual | 8 %/h |
| F-SENS-DROP | sensor_dropout | XMEAS(15) | manual, 15 min | — |
| F-UTIL-STEAM | utility_capacity_loss | UT-STEAM | manual, 1 h | 0.7 |
| F-UTIL-POWER | utility_capacity_loss | UT-POWER | manual, 30 min | 0.6 |
| F-RM-QUAL | raw_material_quality_deviation | MAT-AC | manual | +0.01 B fraction |
| F-RM-SHORT | raw_material_shortage | MAT-D | manual | 90 % stock write-off + supply disruption |
| F-MNT-DELAY | maintenance_delay | MAINTENANCE | manual | +5400 s |
| F-SPARE | spare_part_shortage | SP-IMP-101 | manual | — |
| F-ORDER | production_order_delay | PO-2026-0302 | manual | +3600 s |
| F-QUAL | quality_failure | E_IMPURITY | manual, 1 h | 0.5 |
| F-EVENT | maintenance_delay | MAINTENANCE | event: EQUIPMENT_FAILED | example event trigger |

The planned cooling-tower inspection (request at 21600 s, P3) isolates CT-101 when it starts, about
25,200 s. This raises the CW supply temperature by 12 °C
([maintenance_to_tep](../../04_coupling/maintenance_to_tep.md)).

Test coverage: `test_every_library_scenario_loads_and_runs` runs 120 s.
`test_every_library_fault_can_start_and_stop` starts every manual fault, runs 20 minutes, and stops
them. The combined effect of all faults is **not** checked; the test only checks that the lifecycle
works.

Source:
- `scenarios/SCN-FAULT-LIBRARY.yaml`
- `tests/test_reproducibility.py` — `test_every_library_fault_can_start_and_stop`
