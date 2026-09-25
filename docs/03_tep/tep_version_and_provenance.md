# TEP version and provenance

## Sources

| File in repo | Upstream | Commit | SHA-256 (as committed here) |
|---|---|---|---|
| `simulator/tep/fortran/src/teprob.f` | github.com/camaramm/tennessee-eastman-profBraatz `teprob.f` (byte-identical to the copy at the root of github.com/jkitchin/tennessee-eastman-profbraatz) | 0643318858ce7c87292bd59820b459e12a13d009 | `78b9939d08edeb2a9751e24f429a89b32c2886b73956fa7ce4255c8d46786455` |
| `simulator/tep/fortran/src/temain_mod.f` | same repository | same | `66e52af926d967f6c69636755089ae508f7309fc56595db37ad4e59501d889c0` |
| `simulator/tep/vendor/tep_python_backend.py` | jkitchin repository `src/tep/python_backend.py` (unmodified) | 9a6c8e5fcef4a2850778704e7793c87b0a187005 | `93ba3281980b9c47b882b84412c9cd0501f542ba9a9f214a84fcd6901d870598` |
| `simulator/tep/fortran/lib/tep_fortran.dll` | built from the two Fortran files by `scripts/build_fortran.py` (Docker, `gfortran-mingw-w64`, GCC 12) | — | `bef2fc8b40629414ccb8cec71a03ccf0a621d728b3ae8bce0f20ab274491ac3b` |

The source hashes and the library hash are recorded in every run manifest at run time
(`FortranTEPAdapter.version_info` → `manifest.tep_version`). The table above is a snapshot; rebuilding
the library changes its hash.

Licenses: `simulator/tep/fortran/src/LICENSE.camaramm` (University of Illinois) and
`simulator/tep/vendor/LICENSE.jkitchin` (BSD-3-Clause).

## The version chosen and why

* **The current 12-XMV layout** (XMV(12) = agitator speed), with the corrected XMV order noted in
  `teprob.f` ("Revised 4-4-91 to correct error in documentation of manipulated variables").
* **The camaramm copy of `teprob.f` rather than jkitchin's `fortran/teprob.f`.** The jkitchin copy adds
  a `/SHUTDN/` common block and renames a variable; camaramm is the unmodified original. The simulator
  therefore reproduces the shutdown test itself from `/TEPROC/` values ([safety_and_shutdown.md](safety_and_shutdown.md)).
* **`temain_mod.f` in the Russell/Chiang/Braatz form.** Its MAIN program writes data files and
  hard-codes an IDV(12) switch-on at 8 h. MAIN is never executed. Only its subroutines and the constants
  it assigns (transcribed in `control_scheme.py`) are used.

## What "unmodified" means in practice

* Not a single line of either `.f` file is changed.
* **Build flags keep IEEE semantics:** `-O2 -fno-fast-math -ffp-contract=off -std=legacy`.
* **The compiled MAIN program is ignored.** The adapter replicates what MAIN does before its loop:
  setpoints, gains, initial XMVs, DELTAT and FLAG6. It also replicates the loop body: the controller
  schedule, INTGTR and CONSHAND.
* **The adapter writes Fortran memory only through defined interfaces:**
  * common blocks at initialisation (seed, controller parameters);
  * boundary parameters (coupling);
  * IDV (faults);
  * XMV (operator);
  * SETPT (operator);
  * ERROLD (bumpless transfer);
  * a temporary swap of transmitted XMEAS values around controller calls.

  See [coupling contract](../04_coupling/coupling_contract.md).

Implementation status of cross-checks against the published Braatz datasets (`d00.dat` etc.):
**not implemented**. These files are in the upstream repository but not in this one, and no test
compares against them ([tep_equivalence.md](../09_validation/tep_equivalence.md)).

Source:
- `scripts/build_fortran.py` — `build_local`, `build_docker`
- `simulator/tep/fortran_backend.py` — `FortranTEPAdapter.version_info`, `source_hashes`
- `simulator/tep/fortran/src/README.md`
- `simulator/tep/vendor/README.md`
