# Validation strategy

## What validation means here

The simulator makes four kinds of claim, validated differently:

| Claim | Validation approach | Where |
|---|---|---|
| **TEP fidelity**: the process behaves as the original code | run the original code; compare to the D&V base case; bit-equivalence of the healthy enterprise run with a bare native run; determinism | [tep_equivalence.md](tep_equivalence.md) |
| **Coupling correctness**: enterprise state reaches TEP only as declared, and consistently | trace chains end to end; baseline = TEINIT; acyclic graph | [coupling_validation.md](coupling_validation.md) |
| **Enterprise behaviour**: modules implement their rules | behaviour tests per module | [test_catalog.md](test_catalog.md) |
| **Reproducibility** | double-run comparisons of full traces | [determinism.md](determinism.md) |
| **Scenario behaviour** | the demo's causal chain; library scenarios load and run | [scenario_validation.md](scenario_validation.md) |

## What is *not* validated

* **Enterprise realism.** Response times, wear rates, utility approximations and quality limits are
  plausible defaults. They are not calibrated against a real plant.
* **TEP against the published datasets** (Braatz `d00.dat` etc.).
* **Quantitative fault responses.** Tests assert direction and ordering, not magnitudes. The magnitudes
  in the docs are observations from runs.
* **The UI in a browser.** It was checked manually through screenshots during development. There is no
  automated browser test.

## Running

```bash
python -m pytest                       # all 83, ~1 min
python -m pytest tests/test_tep_adapter.py -k fortran
python -m pytest -x -q tests/test_reproducibility.py::test_demo_scenario_causal_chain
```

Source:
- `tests/`
- `pytest.ini`
