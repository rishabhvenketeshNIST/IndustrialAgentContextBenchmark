# TEP limitations

| Limitation | Why | Impact | Benchmark validity |
|---|---|---|---|
| Freezes after a trip (all derivatives zero); no restart | TEFUNC design | a run cannot continue meaningfully after a trip | only pre-trip behaviour is meaningful |
| Only Mode 1 initial state | TEINIT has one steady state | other operating modes are not available | none for current scenarios |
| Explicit Euler, 1 s step | `temain_mod.f` INTGTR | fixed resolution; stiff events at 1 s granularity | none: native |
| Measurement noise and disturbances from one LCG (G) | `teprob.f` | noise is reproducible from the seed; noise and disturbance sequences are coupled | none |
| Native control without anti-windup | original scheme | wind-up and undershoot after saturation | reference behaviour |
| XMV(12) agitator term `AGSP = (VPOS(12)+150)/100` cannot represent a stopped agitator | TEP formulation | agitator failures cannot be coupled | documented gap |
| IDV binary | `IDV(i) > 0 → 1` in TEFUNC | no disturbance severity | severity must be 1 |
| IDV 16-20 undocumented in the paper | original | semantics come only from the code | flagged in the catalog |
| CW flow in internal units | TEP formulation | no physical unit for utility flows | documented |
| Python port not bit-identical | port | dev backend only | use Fortran |
| No published-dataset comparison implemented | not done | fidelity to the Braatz datasets is not demonstrated | open |

Source:
- `simulator/tep/fortran/src/teprob.f`
- `simulator/tep/fortran/src/temain_mod.f`
