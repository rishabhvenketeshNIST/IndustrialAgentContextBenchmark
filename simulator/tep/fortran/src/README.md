# Authoritative TEP Fortran sources (unmodified)

| File | Origin |
|------|--------|
| `teprob.f` | https://github.com/camaramm/tennessee-eastman-profBraatz/blob/master/teprob.f (commit 0643318858ce7c87292bd59820b459e12a13d009). Identical to `teprob.f` at the root of https://github.com/jkitchin/tennessee-eastman-profbraatz. Downs & Vogel process model: TEINIT, TEFUNC, TESUB1-8. |
| `temain_mod.f` | Same repository. Russell, Chiang & Braatz closed-loop control scheme: CONTRL1-22, INTGTR, CONSHAND. |
| `LICENSE.camaramm` | License of the repository above (University of Illinois). |

These files are compiled as-is into a shared library by `scripts/build_fortran.py`.
They are never edited. The MAIN program in `temain_mod.f` is compiled but never
executed; its controller parameter assignments are reproduced in
`simulator/tep/control_scheme.py`.
