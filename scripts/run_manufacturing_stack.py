#!/usr/bin/env python
"""Start the local manufacturing stack: MQTT broker, simulator (web UI + UNS publisher [+ Historian
writer]), UNS inspector [, Historian API].

    python scripts/run_manufacturing_stack.py [--scenario SCN-COOL-001] [--speed 10] [--start-broker]
                                              [--port 8000] [--inspector-port 8050]
                                              [--mqtt-host 127.0.0.1] [--mqtt-port 1883]
                                              [--historian PATH] [--historian-port 8060] [--no-browser]

                    ENTERPRISE SIMULATOR          (run.py --uns [--historian PATH]: one simulation)
                           │
          ┌────────────────┼─────────────────┐
          ▼                ▼                 ▼
   SIMULATOR WEB UI   UNS PUBLISHER    HISTORIAN WRITER      (observers of the same service)
                           │                 │
                           ▼                 ▼
                      MQTT BROKER         SQLite (WAL)
                           │                 │
                           ▼                 ▼
                      UNS INSPECTOR     HISTORIAN API        (read-only; current scope by default)

This script creates no simulation itself: the simulator server (run.py) owns the only one, and its UNS
publisher and Historian writer follow it; the inspector is an independent MQTT client and the
Historian API an independent reader of the file the writer records. Ctrl-C stops everything it
started. See docs/LOCAL_MANUFACTURING_STACK.md.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from uns.broker import LocalBroker, port_open  # noqa: E402  (no simulator import)

WINDOWS = os.name == "nt"


def get_json(url: str, timeout: float = 2.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


class Stack:
    def __init__(self, args) -> None:
        self.args = args
        self.broker: Optional[LocalBroker] = None
        self.procs: List[subprocess.Popen] = []
        self.sim_url = f"http://127.0.0.1:{args.port}/"
        self.inspector_url = f"http://127.0.0.1:{args.inspector_port}/"
        self.historian_url = f"http://127.0.0.1:{args.historian_port}/"
        self.historian = str(Path(args.historian).resolve()) if args.historian else None

    def spawn(self, name: str, argv: List[str]) -> subprocess.Popen:
        # Windows: own process group, so Ctrl-C reaches only this script, which then stops the children
        # in order (and the job object below kills them if this script dies). POSIX: same process group,
        # so Ctrl-C or a terminal hang-up reaches every part of the stack.
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0
        p = subprocess.Popen([sys.executable, *argv], cwd=ROOT, creationflags=flags)
        p.name = name
        self.procs.append(p)
        return p

    def wait_ready(self, name: str, proc: Optional[subprocess.Popen], ready, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc is not None and proc.poll() is not None:
                raise RuntimeError(f"{name} exited during start-up (code {proc.returncode}); see its output above")
            try:
                if ready():
                    return
            except Exception:
                pass
            time.sleep(0.25)
        raise RuntimeError(f"{name} was not ready within {timeout:.0f} s")

    def start(self) -> None:
        a = self.args
        ports = [(a.port, "--port"), (a.inspector_port, "--inspector-port")]
        if self.historian:
            ports.append((a.historian_port, "--historian-port"))
        for port, what in ports:
            if port_open(port):
                raise RuntimeError(f"port {port} is already in use; stop the process holding it or choose {what}")

        # 1. MQTT broker
        if a.start_broker:
            if port_open(a.mqtt_port, a.mqtt_host):
                raise RuntimeError(f"an MQTT broker already listens on {a.mqtt_host}:{a.mqtt_port}; "
                                   f"omit --start-broker to use it, or choose another --mqtt-port")
            self.broker = LocalBroker(port=a.mqtt_port).start()
            print(f"[stack] broker   mqtt://127.0.0.1:{a.mqtt_port}  (uns/mosquitto.conf, local development only)", flush=True)
        elif not port_open(a.mqtt_port, a.mqtt_host):
            raise RuntimeError(f"no MQTT broker at {a.mqtt_host}:{a.mqtt_port}; start one "
                               f"(mosquitto -c uns/mosquitto.conf) or pass --start-broker")
        else:
            print(f"[stack] broker   mqtt://{a.mqtt_host}:{a.mqtt_port}  (existing broker)", flush=True)

        # 2 + 3. the simulator server, publishing its own simulation to the UNS
        sim = ["run.py", "--no-browser", "--uns", "--port", str(a.port), "--scenario", a.scenario,
               "--mqtt-host", a.mqtt_host, "--mqtt-port", str(a.mqtt_port), "--inspector-url", self.inspector_url]
        if a.speed is not None:
            sim += ["--speed", str(a.speed)]
        if a.backend:
            sim += ["--backend", a.backend]
        if self.historian:
            sim += ["--historian", self.historian]       # the writer observes the same service
        p = self.spawn("simulator", sim)
        self.wait_ready("simulator + UNS publisher", p,
                        lambda: get_json(self.sim_url + "api/uns/status")["uns"].get("connected"), a.timeout)
        scope = get_json(self.sim_url + "api/uns/status")["operational_scope_id"]

        # the Historian API: a read-only reader of the file the simulator's writer records
        if self.historian:
            p = self.spawn("historian API", ["scripts/run_historian_api.py", "--database", self.historian,
                                             "--port", str(a.historian_port)])
            self.wait_ready("Historian API", p,
                            lambda: get_json(self.historian_url + "status")["current_scope"] == scope, a.timeout)

        # 4. the inspector: an independent MQTT client
        p = self.spawn("inspector", ["scripts/run_inspector.py", "--no-browser", "--port", str(a.inspector_port),
                                     "--mqtt-host", a.mqtt_host, "--mqtt-port", str(a.mqtt_port),
                                     "--simulator-url", self.sim_url])
        self.wait_ready("UNS inspector", p,
                        lambda: get_json(self.inspector_url + "api/status")["operational_scope_id"] == scope, a.timeout)

        print(f"[stack] simulator UI   {self.sim_url}   (press Start to run the scenario)")
        print(f"[stack] UNS inspector  {self.inspector_url}")
        if self.historian:
            print(f"[stack] Historian API  {self.historian_url}status   (records into {self.historian}; current scope)")
        print(f"[stack] operational scope {scope}  (shown by every view)")
        print("[stack] Ctrl-C stops the stack", flush=True)
        if not a.no_browser:
            webbrowser.open(self.sim_url)
            webbrowser.open(self.inspector_url + "#equipment_module:EM-REACTOR")

    def watch(self) -> None:
        while True:
            for p in self.procs:
                if p.poll() is not None:
                    raise RuntimeError(f"{p.name} exited unexpectedly (code {p.returncode})")
            if self.broker is not None and not port_open(self.args.mqtt_port):
                raise RuntimeError("the MQTT broker started by this script stopped")
            time.sleep(0.5)

    def stop(self) -> None:
        """Stop in reverse order: inspector, simulator (its publisher's last will marks the UNS
        offline), broker. Nothing is left running."""
        for p in reversed(self.procs):
            if p.poll() is None:
                try:
                    if WINDOWS:
                        p.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        p.terminate()
                    p.wait(10)
                except Exception:
                    pass
            if p.poll() is None:
                p.kill()
                p.wait(10)
        if self.broker is not None:
            self.broker.stop()
        print("[stack] stopped", flush=True)


_JOB = None


def kill_children_when_this_process_ends() -> None:
    """Windows: place this process in a job object with KILL_ON_JOB_CLOSE. Every child it starts
    (broker, simulator, inspector) inherits the job and is terminated when this process ends, however
    it ends: Ctrl-C, a closed console window or a hard kill. No orphaned processes."""
    global _JOB
    if not WINDOWS:
        return
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", ctypes.c_ulonglong * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = k32.CreateJobObjectW(None, None)
    info = ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = 0x2000                      # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not (job and k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))  # extended limits
            and k32.AssignProcessToJobObject(job, k32.GetCurrentProcess())):
        print("[stack] warning: could not create a job object; if this script is killed, stop its children by hand")
        return
    _JOB = job                                                          # the handle lives as long as this process


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scenario", default="SCN-COOL-001")
    ap.add_argument("--speed", type=float, default=None, help="simulator speed (simulated s per wall s)")
    ap.add_argument("--port", type=int, default=8000, help="simulator web UI port")
    ap.add_argument("--inspector-port", type=int, default=8050)
    ap.add_argument("--mqtt-host", default="127.0.0.1")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--start-broker", action="store_true", help="start a local Mosquitto (uns/mosquitto.conf)")
    ap.add_argument("--historian", metavar="PATH", default=None,
                    help="also record the simulation into this Historian SQLite file and serve it read-only")
    ap.add_argument("--historian-port", type=int, default=8060, help="Historian API port (default 8060)")
    ap.add_argument("--backend", default=None, choices=["auto", "fortran", "python"])
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--timeout", type=float, default=120.0, help=argparse.SUPPRESS)
    args = ap.parse_args()

    def stop_signal(signum, frame):
        raise KeyboardInterrupt
    for sig in ("SIGTERM", "SIGBREAK"):
        if hasattr(signal, sig):
            signal.signal(getattr(signal, sig), stop_signal)

    kill_children_when_this_process_ends()
    stack = Stack(args)
    code = 0
    try:
        stack.start()
        stack.watch()
    except KeyboardInterrupt:
        print("\n[stack] stopping...", flush=True)
    except RuntimeError as exc:
        print(f"[stack] ERROR: {exc}", flush=True)
        code = 1
    finally:
        stack.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
