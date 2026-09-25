# SCN-BASELINE: normal operation

`scenarios/SCN-BASELINE.yaml`: seed 1001, 3 h, no faults, two 20 t production orders.

What a run shows (observed during development, and consistent with the tests):

* **The process stays at the Mode 1 base case.** No trip and no process alarms. Every boundary
  parameter equals its TEINIT value for the whole run: pumps rated 1100 against a 1000 design, the
  tower above its 0.9 threshold, and natural wear of ≈ 0.001 health over 3 h.
* **Both orders complete.** Production is ≈ 42 t in 3 h (nominal 14,076 kg/h), and lots are RELEASED
  after QC.
* **One replenishment cycle for D feed.** TK-101 crosses its 24 t reorder point at about 1.6 h, and the
  lot is delivered about 1 h later (± 0.25 h seeded jitter), then passes incoming inspection.

Uses: reference for reproducibility (`test_reset_returns_to_initial_state` uses it), a baseline to
compare faulted runs against, and the default scenario for the variable-graph generator.

Source:
- `scenarios/SCN-BASELINE.yaml`
- `tests/test_reproducibility.py` — `test_reset_returns_to_initial_state`
