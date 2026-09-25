# Installation

## Requirements

* **Python.** Developed and tested with **Python 3.11.9 on Windows 11**. The README says "≥ 3.10";
  that has **not been tested**.
* **Python packages** (`requirements.txt`): numpy, PyYAML, pydantic, fastapi, uvicorn; pytest and httpx
  for tests. Versions used during development: numpy 2.4.6, PyYAML 6.0.3, pydantic 2.13.4, fastapi
  0.141.1, uvicorn 0.53.0, pytest 9.1.1, httpx 0.28.1.
* **To build the Fortran library:** either `gfortran` on PATH (Linux, macOS, MinGW on Windows) or Docker.

```bash
python -m pip install -r requirements.txt
python scripts/build_fortran.py            # skip if the library already exists
```

## The Fortran library

| Platform | File | How it is built |
|---|---|---|
| Windows x64 | `simulator/tep/fortran/lib/tep_fortran.dll` (**included**, statically linked) | Docker `debian:bookworm-slim` + `gfortran-mingw-w64-x86-64` cross-compile, or MinGW gfortran |
| Linux | `libtep_fortran.so` (not included) | local gfortran, or Docker `debian` + gfortran |
| macOS | `libtep_fortran.dylib` (not included) | local gfortran only (`brew install gcc`); Docker cannot produce a dylib |

`scripts/build_fortran.py [--force] [--method auto|local|docker]`. The first Docker build downloads the
base image and the compiler.

## Without a Fortran library

`run.py` falls back to the Python development backend with a warning. Tests marked Fortran are skipped.
Results are then **not** the authoritative TEP trajectories.

## Docker (whole application)

```bash
docker build -t acme-tep .
docker run -p 8000:8000 acme-tep
```

The image builds the `.so` with native gfortran and serves on 0.0.0.0:8000. **Implementation status:
the Dockerfile has not been built or run during development.** Treat it as untested.

Source:
- `requirements.txt`
- `scripts/build_fortran.py` — `main`, `build_local`, `build_docker`
- `Dockerfile`
- `run.py` — `ensure_fortran`
