#!/usr/bin/env python
"""Build the authoritative TEP Fortran shared library.

Compiles the unmodified teprob.f and temain_mod.f into
simulator/tep/fortran/lib/<tep_fortran.dll | libtep_fortran.so | libtep_fortran.dylib>.

Strategy:
  1. a local gfortran (Linux/macOS/MinGW on Windows), or
  2. Docker: debian + gfortran (Linux .so) or gfortran-mingw-w64 cross compiler (Windows .dll).

Flags keep IEEE semantics (no fast-math, no FMA contraction) so results are reproducible.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simulator.tep.fortran_backend import LIB_DIR, SOURCE_DIR, SOURCES, library_filename  # noqa: E402

COMMON_FLAGS = ["-O2", "-fno-fast-math", "-ffp-contract=off", "-std=legacy", "-shared"]
DOCKER_IMAGE = "debian:bookworm-slim"


def _flags_for(target_windows: bool) -> list:
    if target_windows:
        return COMMON_FLAGS + ["-static", "-static-libgfortran", "-static-libgcc", "-Wl,--export-all-symbols"]
    return COMMON_FLAGS + ["-fPIC"]


def build_local(out: Path) -> bool:
    fc = shutil.which("gfortran")
    if not fc:
        return False
    windows = sys.platform.startswith("win")
    cmd = [fc, *_flags_for(windows), "-o", str(out), *[str(SOURCE_DIR / s) for s in SOURCES]]
    print("[build] local:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    return True


def build_docker(out: Path) -> bool:
    docker = shutil.which("docker")
    if not docker:
        return False
    windows = sys.platform.startswith("win")
    if sys.platform == "darwin":
        print("[build] Docker cannot produce a macOS dylib; install gfortran (brew install gcc).")
        return False
    compiler = "x86_64-w64-mingw32-gfortran" if windows else "gfortran"
    package = "gfortran-mingw-w64-x86-64" if windows else "gfortran"
    workdir = out.parent.parent  # simulator/tep/fortran
    flags = " ".join(_flags_for(windows))
    script = (f"apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends {package} "
              f">/dev/null 2>&1 && cd /work && {compiler} {flags} -o lib/{out.name} "
              + " ".join(f"src/{s}" for s in SOURCES))
    cmd = [docker, "run", "--rm", "-v", f"{workdir}:/work", DOCKER_IMAGE, "bash", "-c", script]
    print(f"[build] docker ({compiler}) ... this downloads the compiler on first use")
    env = dict(**__import__("os").environ, MSYS_NO_PATHCONV="1")
    subprocess.run(cmd, check=True, env=env)
    return out.exists()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="rebuild even if the library exists")
    ap.add_argument("--method", choices=["auto", "local", "docker"], default="auto")
    args = ap.parse_args()
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    out = LIB_DIR / library_filename()
    if out.exists() and not args.force:
        print(f"[build] {out} already exists (use --force to rebuild)")
        return 0
    ok = False
    if args.method in ("auto", "local"):
        ok = build_local(out)
    if not ok and args.method in ("auto", "docker"):
        ok = build_docker(out)
    if not ok:
        print("[build] FAILED: no gfortran on PATH and Docker unavailable. "
              "Install gfortran or Docker, or run with the python backend (TEP_BACKEND=python).")
        return 1
    print(f"[build] OK -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
