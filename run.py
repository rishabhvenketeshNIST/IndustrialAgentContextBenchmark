#!/usr/bin/env python
"""One-command launcher: python run.py

1. checks Python dependencies,
2. builds the authoritative Fortran TEP library if it is missing (gfortran or Docker),
   falling back to the pure-Python development backend only if that is impossible,
3. starts the API + web UI with the scenario loaded (READY; press Start to run it) and opens
   the browser.

With ``--uns`` the same server also publishes its simulation to the MQTT Unified Namespace, and with
``--historian PATH`` it also records it into a Historian SQLite file: one simulation for UI, API, UNS and
Historian, whose writer is an observer of this server's service. The whole local stack, broker,
inspector and Historian API included, is started by ``python scripts/run_manufacturing_stack.py``
(docs/LOCAL_MANUFACTURING_STACK.md).
"""
from __future__ import annotations

import argparse
import importlib
import logging
import os
import signal
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
    ap.add_argument("--speed", type=float, default=None, help="runner speed (simulated s per wall s); default: scenario")
    uns = ap.add_argument_group("Unified Namespace (docs/LOCAL_MANUFACTURING_STACK.md)")
    uns.add_argument("--uns", action="store_true",
                     help="also publish this server's simulation to the MQTT UNS (same simulation, one engine)")
    uns.add_argument("--mqtt-host", default="127.0.0.1")
    uns.add_argument("--mqtt-port", type=int, default=1883)
    uns.add_argument("--inspector-url", default=None, help="UNS inspector URL shown as a link in the UI")
    hist = ap.add_argument_group("Historian (docs/HISTORIAN.md)")
    hist.add_argument("--historian", metavar="PATH", default=None,
                      help="also record this server's simulation into a Historian SQLite file (created or appended to)")
    args = ap.parse_args()

    # Fail fast, before building the simulation, if the web port is taken (uvicorn would otherwise
    # report it only after start-up, after this script had already printed the URLs).
    if not port_free(args.host, args.port):
        print(f"[run] port {args.port} on {args.host} is already in use (another simulator server?). "
              f"Stop it or choose --port.")
        sys.exit(3)

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
    svc = app.state.service
    if args.speed is not None:
        svc.set_speed(args.speed)
    publisher = None
    if args.uns:
        # the UNS publisher follows this server's own simulation: one engine for UI, API and UNS
        from uns.publisher import UNSPublisher
        try:
            publisher = UNSPublisher(svc, host=args.mqtt_host, port=args.mqtt_port).attach()
        except (ConnectionError, OSError) as exc:
            print(f"[run] cannot reach the MQTT broker at {args.mqtt_host}:{args.mqtt_port} ({exc}). "
                  f"Start one (mosquitto -c uns/mosquitto.conf) or run without --uns.")
            svc.shutdown()
            sys.exit(1)
        app.state.uns = publisher
        print(f"[run] UNS: mqtt://{args.mqtt_host}:{args.mqtt_port}/uns/v1/#  (same simulation as the UI)")
    writer = None
    if args.historian:
        # the Historian writer observes this server's own simulation, like the UNS publisher; it never
        # creates or steps a simulation and does not use MQTT
        import sqlite3

        from historian import HistorianError
        from historian.writer import HistorianWriter
        try:
            writer = HistorianWriter(svc, args.historian).attach()
        except (HistorianError, OSError, sqlite3.Error) as exc:     # unopenable path, not a database, ...
            print(f"[run] cannot record into {args.historian}: {exc}")
            if publisher is not None:
                publisher.close()
            svc.shutdown()
            sys.exit(1)
        print(f"[run] Historian: recording into {args.historian}  (same simulation as the UI)")
    if args.inspector_url:
        app.state.links["uns_inspector"] = args.inspector_url
    url = f"http://{args.host}:{args.port}/"
    print(f"[run] UI:  {url}\n[run] API: {url}docs", flush=True)
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    # uvicorn stops gracefully on SIGTERM/SIGBREAK (Ctrl-Break, as the stack launcher sends on Windows) and
    # then re-raises the signal with the previous handler; make that handler a KeyboardInterrupt so the
    # process leaves through the finally below (publisher and Historian writer closed, nothing lost)
    # instead of being terminated by the default handler.
    def _stop(signum, frame):
        raise KeyboardInterrupt
    for name in ("SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _stop)
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        # the Historian's final commit is local and quick: do it before the UNS publisher, whose close may
        # wait for MQTT acknowledgements; a failing close must not prevent the other one
        try:
            if writer is not None:
                writer.close()               # commits everything recorded (graceful stop, Ctrl-C or Ctrl-Break)
        finally:
            if publisher is not None:
                publisher.close()


def port_free(host: str, port: int) -> bool:
    import socket
    with socket.socket() as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


if __name__ == "__main__":
    main()
