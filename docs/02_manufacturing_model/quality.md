# Quality

## What it represents

Product quality control based on TEP's product analyzer (stream 11), plus incoming inspection of
delivered raw-material lots. It decides production-lot disposition: RELEASED, QUARANTINE or REJECTED.

## Why it exists

It turns process composition into a business consequence. TEP computes composition; the quality
module judges it against a specification.

## Product quality (`QualityModule.post_step`)

1. **When a sample is taken.** Whenever the product analyzer group (XMEAS 37-41) reports a new value,
   every 0.25 h. That is when TEP releases a new analyzer result after its 0.25 h dead time. No samples
   are taken at t = 0 or while the process is shut down.
2. **Tests** (`configs/quality.yaml`) are expressions over the **transmitted** analyzer values:

   | Test | Expression | Limits |
   |---|---|---|
   | G_MASS_PCT | `100·62·G / (62·G + 76·H)` (MW from TEINIT) | 47.5 – 52.5, target 50 |
   | GH_PURITY | `G + H` mol% | ≥ 95 |
   | E_IMPURITY | E mol% | ≤ 1.2 |
   | F_BYPRODUCT | F mol% | ≤ 0.2 |

   A `quality_failure` fault adds an offset to the *reported* value of one test. The product is
   unaffected.
3. **Lot assignment.** A sample represents product made 900 s earlier (the analyzer dead time) and is
   assigned to the lot that was open at that time.
4. **Disposition** 900 s after a lot closes:
   * 0 failing samples → RELEASED;
   * ≤ 25 % failing → QUARANTINE;
   * otherwise → REJECTED;
   * no samples → QUARANTINE.

   It publishes `LOT_STATE_CHANGED`, which the warehouse and production modules consume. Its
   causation is the last failing sample's result event.
5. **Lab entity `WU-QC-LAB`** properties: `last_result`, `last_result_fail`, `consecutive_failures`,
   and one property per test. Alarms QA-OFFSPEC and QA-OFFSPEC2 watch them.

## Raw-material incoming inspection

600 s after a lot is received, the lot's certificate attributes plus any active
`raw_material_quality_deviation` contribution are compared with the material specification. The lot is
then RELEASED or REJECTED. A rejected lot is never consumed.

## Worked example (demo)

The first failing sample is QS-00009 at 02:15:01 (G_MASS_PCT). It represents product made around
02:00. That is **after** the cooling capacity was restored (01:45), during the controller-windup
undershoot, not during the high-temperature excursion itself. Lot PL-0003 (01:25–02:25) had 1 failing
sample out of 4, so it becomes QUARANTINE at 02:40:10. The generated trace lists the sample events
([SCN-COOL-001_trace.md](../06_scenarios/scenario_examples/SCN-COOL-001_trace.md)).

## What it does NOT do

There is no lab turnaround model beyond the fixed delays, no retesting, and no release of quarantined
lots (they stay in QUARANTINE). Samples are not taken from the true composition: they use the transmitted analyzer
values, so an analyzer sensor fault would flow into quality results.

Source:
- `configs/quality.yaml`
- `simulator/quality/__init__.py` — `QualityModule._product_sample`, `QualityModule._decide_product_lots`, `QualityModule._inspect_materials`, `TestSpec`
- `simulator/simulation/process_interface.py` — `ProcessInterface.sync`
- `tests/test_enterprise.py` — `test_quality_calculation_from_tep_composition`, `test_quality_failure_fault_rejects_lot`
