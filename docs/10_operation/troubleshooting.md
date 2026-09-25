# Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `[run] WARNING: Fortran backend unavailable - using the Python DEVELOPMENT backend` | no library and neither gfortran nor Docker available | install gfortran or start Docker, then `python scripts/build_fortran.py` |
| `TEPAdapterError: Fortran TEP library not found` | backend `fortran` requested without the library | build it, or use `--backend python` |
| Docker build fails on Windows with path errors | Git-Bash path conversion | the script sets `MSYS_NO_PATHCONV=1`; run it from PowerShell if needed |
| Fortran tests skipped | library missing | build the library |
| `ConfigError ... ISA-95 violation` / `unknown entity` / `Causal cycle` | config edit broke validation | the message names the element or relation |
| HTTP 409 `No simulation created` | a call before `create_simulation` | create or load a scenario |
| HTTP 409 on start | simulation COMPLETED | reset or load |
| HTTP 400 `Setpoint of loop N is written by cascade master` | the loop is in CAS | put the master in MAN, or change the master's setpoint |
| HTTP 400 `XMV(n) is driven by loop N` | manual XMV write while the loop runs | put the loop in MAN |
| Plant "shut down" banner, nothing moves | TEP interlock trip; TEP freezes | reset; see [safety and shutdown](../03_tep/safety_and_shutdown.md) |
| The speed setting seems ignored at high values | runner capped at 2000 steps per tick | expected; results are unaffected |
| Two runs differ | different library, config (hash), seed, backend, or commands at different times | compare the manifests ([seeds and reproducibility](seeds_and_reproducibility.md)) |
| UI shows stale values after a server restart | browser cache | reload |
| Temp folders `tep_fortran_*` accumulate | private library copies for concurrent adapters are not deleted | delete them manually (safe when no simulation is running) |

Source:
- `run.py` — `ensure_fortran`
- `api/app.py` — `create_app`
- `simulator/tep/fortran_backend.py` — `FortranTEPAdapter._load`
