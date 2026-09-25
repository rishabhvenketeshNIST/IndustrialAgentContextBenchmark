#!/usr/bin/env python
"""One-command launcher: python run.py

1. checks Python dependencies,
2. builds the authoritative Fortran TEP library if it is missing (gfortran or Docker),
   falling back to the pure-Python development backend only if that is impossible,
3. starts the API + web UI with the scenario loaded (READY; press Start to run it) and opens
   the browser.
"""
from __future__ import annotations

import argparse
import importlib
import logging
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIRED = ["numpy", "yaml", "fastapi", "uvicorn", "pydantic"]


def check_deps() -> None:
    missing = []
    for mod in REQUIRED:
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        print(f"[run] missing packages: {missing}\n[run] install with: python -m pip install -r requirements.txt")
        sys.exit(1)


def ensure_fortran() -> bool:
    sys.path.insert(0, str(ROOT))
    from simulator.tep.fortran_backend import is_available
    if is_available():
        return True
    print("[run] Fortran TEP library not found - building it (first run only)...")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_fortran.py")])
    return r.returncode == 0 and is_available()


def main() -> None:
    ap = argparse.ArgumentParser(description="ACME TEP enterprise simulator")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--scenario", default="SCN-COOL-001", help="scenario loaded at start-up")
    ap.add_argument("--backend", choices=["auto", "fortran", "python"], default="auto")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    check_deps()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    if args.backend in ("auto", "fortran"):
        if not ensure_fortran():
            if args.backend == "fortran":
                print("[run] Fortran backend requested but could not be built.")
                sys.exit(1)
            print("[run] WARNING: Fortran backend unavailable - using the Python DEVELOPMENT backend. "
                  "Results are not the authoritative TEP trajectories.")
            args.backend = "python"
    if args.backend != "auto":
        os.environ["TEP_BACKEND"] = args.backend

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import uvicorn
    from api.app import create_app

    app = create_app(autoload=args.scenario)
    url = f"http://{args.host}:{args.port}/"
    print(f"[run] UI:  {url}\n[run] API: {url}docs")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
