#!/usr/bin/env python
"""Record a simulation into a Historian SQLite file, headless.

    python scripts/record_history.py [--scenario SCN-COOL-001] [--db history.sqlite]
                                     [--speed 0] [--sample-period 1]

The simulation is the normal simulator service (api/service.py) with a HistorianWriter attached as an
observer; nothing else is created. ``--speed 0`` runs as fast as possible; ``--speed N`` paces the run
at N simulated seconds per wall-clock second with the service's real-time runner. Pacing never changes
what is recorded: the Historian uses simulation time only. An existing file is appended to (a new
operational scope). Ctrl-C stops the run and keeps what was recorded. See docs/HISTORIAN.md.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.service import SimulatorService  # noqa: E402
from historian.reader import HistorianReader  # noqa: E402
from historian.writer import HistorianWriter  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 epilog="Example: python scripts/record_history.py --scenario SCN-COOL-001 "
                                        "--db exports/SCN-COOL-001.sqlite")
    ap.add_argument("--scenario", default="SCN-COOL-001", help="scenario to run (default SCN-COOL-001)")
    ap.add_argument("--db", default="history.sqlite", help="Historian SQLite file (created or appended to)")
    ap.add_argument("--speed", type=float, default=0.0,
                    help="simulated seconds per wall-clock second; 0 = as fast as possible (default)")
    ap.add_argument("--sample-period", type=int, default=1,
                    help="step-state sampling grid in simulated seconds (default 1: every simulator step)")
    ap.add_argument("--commit-interval", type=int, default=60, help="commit every N simulated seconds (default 60)")
    args = ap.parse_args()

    svc = SimulatorService(start_runner=args.speed > 0)
    svc.create_simulation(args.scenario)
    eng = svc.engine
    writer = HistorianWriter(svc, args.db, sample_period_s=args.sample_period,
                             commit_interval_s=args.commit_interval).attach()
    print(f"[history] recording {args.scenario} into {args.db}  scope {writer.scope}  "
          f"({eng.duration_s} s, sampling every {args.sample_period} s)", flush=True)
    wall0 = time.monotonic()
    try:
        if args.speed > 0:
            svc.set_speed(args.speed)
            svc.start_simulation()
            while not svc.engine.completed:
                time.sleep(1.0)
                print(f"[history] t = {svc.engine.clock.time_s} s", flush=True)
        else:
            while not eng.completed:
                svc.run_until(min(eng.clock.time_s + 600, eng.duration_s))      # completes at the duration
                print(f"[history] t = {eng.clock.time_s} s", flush=True)
    except KeyboardInterrupt:
        print("[history] interrupted; keeping what was recorded", flush=True)
        if svc.runner.running:
            svc.pause_simulation()
    finally:
        writer.close()
        svc.shutdown()

    reader = HistorianReader(args.db)
    scope = reader.current_scope()
    counts = {t: reader.db.execute(f"SELECT COUNT(*) FROM {t} WHERE {c}",
                                   (scope,)).fetchone()[0]
              for t, c in (("samples", "series_id IN (SELECT series_id FROM series WHERE operational_scope_id = ?)"),
                           ("state_changes", "operational_scope_id = ?"), ("events", "operational_scope_id = ?"))}
    print(f"[history] done in {time.monotonic() - wall0:.0f} s wall: scope {scope}, coverage "
          f"{[(c['from_t'], c['to_t']) for c in reader.coverage(scope)]}, {counts}, "
          f"{os.path.getsize(args.db) / 1e6:.1f} MB")
    reader.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
