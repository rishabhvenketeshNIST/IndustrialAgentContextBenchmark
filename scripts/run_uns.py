#!/usr/bin/env python
"""Run a scenario and publish it to the MQTT Unified Namespace.

    python scripts/run_uns.py [--scenario SCN-COOL-001] [--host 127.0.0.1] [--port 1883]
                              [--speed 10] [--measurement-period 10] [--start-broker]

The simulator is stepped one simulated second at a time and the UNS publisher synchronises after every
step (docs/UNS_OPERATING_MODEL.md). ``--speed`` paces simulated seconds per wall-clock second (0 = as
fast as possible); pacing changes when messages are sent and their wall-clock ``observed_at``, never
their manufacturing content or simulation time. ``--start-broker`` runs a local Eclipse Mosquitto from
uns/mosquitto.conf (a local-development configuration, not a production one) for the run.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.service import SimulatorService  # noqa: E402
from uns import DEFAULT_ROOT  # noqa: E402
from uns.broker import LocalBroker  # noqa: E402
from uns.publisher import UNSPublisher  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scenario", default="SCN-COOL-001")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--speed", type=float, default=10.0, help="simulated seconds per wall second; 0 = max")
    ap.add_argument("--measurement-period", type=int, default=10, help="measurement sampling period (sim s)")
    ap.add_argument("--duration", type=int, default=None, help="override the scenario duration (s)")
    ap.add_argument("--backend", default=None)
    ap.add_argument("--start-broker", action="store_true", help="start a local mosquitto on --port")
    args = ap.parse_args()

    broker = LocalBroker(port=args.port).start() if args.start_broker else None
    svc = SimulatorService(start_runner=False, backend=args.backend)
    svc.create_simulation(args.scenario, duration_seconds=args.duration)
    pub = UNSPublisher(svc, host=args.host, port=args.port, root=args.root,
                       measurement_period_s=args.measurement_period)
    pub.connect()
    print(f"[uns] publishing {args.scenario} to mqtt://{args.host}:{args.port}/{args.root}/#")
    try:
        pub.sync()
        eng = svc.engine
        wall0 = time.monotonic()
        while not eng.completed:
            eng.step(1)
            if args.speed > 0:
                # pace before publishing: observed_at then follows the pacing schedule, not the time the
                # step took to compute (compute time could depend on hidden scenario content)
                lag = eng.clock.time_s / args.speed - (time.monotonic() - wall0)
                if lag > 0:
                    time.sleep(lag)
            pub.sync()
            if eng.clock.time_s % 600 == 0:
                print(f"[uns] t = {eng.clock.time_s} s  messages {pub.sent}")
        pub.sync()
        pub.flush()
        print(f"[uns] completed at t = {eng.clock.time_s} s  messages {pub.sent}")
    except KeyboardInterrupt:
        print("[uns] interrupted")
    finally:
        pub.close()
        svc.shutdown()
        if broker:
            broker.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
