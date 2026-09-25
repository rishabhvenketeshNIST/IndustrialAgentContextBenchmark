# TEP equivalence

## Three levels of evidence

1. **Same code.** `teprob.f` and `temain_mod.f` are byte-identical to upstream (hashes in
   [provenance](../03_tep/tep_version_and_provenance.md)) and compiled with IEEE-preserving flags.
2. **Same initialisation and loop semantics as MAIN.** The adapter replicates MAIN's assignments,
   single-precision constant folding included, and its loop body order: controllers → INTGTR →
   CONSHAND.
3. **Tested behaviour:**
   * `test_initial_state_matches_downs_vogel_base_case`: all 41 XMEAS at t = 0 within 0.1 % of the
     published base case (both backends).
   * `test_closed_loop_holds_base_case_for_two_hours`: stable closed loop at the base case.
   * `test_healthy_enterprise_reproduces_native_tep_exactly`: with no faults, the complete enterprise
     simulator produces **bit-identical** XMEAS every second for 1 h, and identical states, compared
     with a bare native adapter using the same TEP seed. This is the strongest statement: every
     enterprise mechanism is inert when healthy, so boundary writes, sensor-layer swaps and coupling
     change nothing.

## What "bare native adapter" means

It is the adapter only: TEINIT, the native controllers and Euler, with MAIN's parameters, and no
enterprise layer. It is **not** the original `temain_mod.f` executable, which writes files, hard-codes
IDV(12) at 8 h and uses its own seed. No test runs the original executable and compares output files.

## Python backend

It is statistically similar (means within 1 %) but not bit-identical. Use it only for development
without a compiler.

## Not established

* **Equality with the published Braatz datasets.** No comparison is implemented.
* **Equality with runs by other TEP wrappers.** The jkitchin package, for example, uses a Python
  controller port and a different step order.
* **Cross-platform bit-equality.** Different compilers or flags may change the last bits
  ([determinism](determinism.md)).

Source:
- `tests/test_tep_adapter.py`
- `tests/test_reproducibility.py` — `test_healthy_enterprise_reproduces_native_tep_exactly`
