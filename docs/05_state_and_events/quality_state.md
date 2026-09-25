# Quality state

## Quality sample (`QualitySample`)

| Field | Meaning |
|---|---|
| `sample_id` | QS-nnnnn, sequential per run |
| `source` | analyzer (`CM-AT-PRODUCT`) or lab (`WU-QC-LAB`) for raw material |
| `subject_type` / `subject_id` | product + product id, or material + material id |
| `lot_id` | production lot (by `represents_s`) or material lot |
| `taken_s` / `represents_s` | result time / time the product was made (taken − 900 s for product) |
| `results[]` | per test: `test_id, name, value, unit, low, high, target, pass` |
| `overall` | PASS / FAIL |

## Lab entity `WU-QC-LAB` (observable summary)

`last_result` (PASS/FAIL/NONE), `last_result_fail` (0/1), `consecutive_failures`, and one property per
test (`g_mass_pct`, `gh_purity`, `e_impurity`, `f_byproduct`) holding the last value.

## Lot status

Product lots: see [production state](production_state.md). Material lots: `quality_status` RELEASED /
QUARANTINE / REJECTED / CONSUMED (`MaterialLot`).

Events: QUALITY_SAMPLE_TAKEN (product only), QUALITY_RESULT_CREATED, LOT_STATE_CHANGED (`lot_type`
product or material).

Source:
- `simulator/quality/__init__.py` — `QualitySample`, `QualityModule`
- `simulator/materials/__init__.py` — `MaterialLot`
