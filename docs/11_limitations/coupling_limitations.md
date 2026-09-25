# Coupling limitations

| Limitation | Detail | Impact | Possible improvement |
|---|---|---|---|
| Capability as scaling of existing TEP terms | pump, steam and compressor limits scale VRNG or CPFLMX | no pump-curve or valve interaction; limitation is linear in capacity | detailed hydraulic models (would still write only boundaries) |
| Design margins create dead zones | small degradations have zero process effect | early-stage faults are invisible in the process; only vibration shows them | intended; document per scenario |
| SZERO couplings delayed | effect at the next walk knot | supply temperature and composition changes lag 0.1-1.7 h | none: plausible |
| Two boundary parameters uncoupled | D-feed temperature, stream-4 temperature | no enterprise cause can change them | add heat tracing or storage temperature models |
| Utility observations are not TEP outputs | CW flow, header pressure, steam pressure and temperature, voltage | these values are approximations | label them as derived (done in the variable reference) |
| One-step lag | relations read the previous step's process values | 1 s delay in observations | none needed |
| Power model | proportional derating; no motor protection or restart | no trips from undervoltage | add protection logic |
| Lot composition switches abruptly | FIFO lot change steps XST immediately | composition discontinuity at a lot change (only if lots differ) | tank mixing model |
| `process_influences` hand-curated | not generated from TEP | causal labels for TEP-internal consequences are approximate | a sensitivity analysis to derive influences |
| No negative tests for coupling validation | cycle and double-writer rejection is untested | regressions possible | add tests |

Source:
- `configs/coupling.yaml`
- `simulator/coupling/__init__.py`
