#!/usr/bin/env python
"""Serve the read-only Historian HTTP API over an existing Historian SQLite file.

    python scripts/run_historian_api.py --database exports/SCN-COOL-001.sqlite [--port 8060]
                                        [--host 127.0.0.1] [--access current|evaluator]

The service reads the file through historian.reader.HistorianReader and only answers GET queries
(docs/HISTORIAN_QUERY_API.md). ``--access current`` (the default, agent-facing) serves the current
operational scope only; ``--access evaluator`` serves every recorded scope and is for the benchmark
evaluator only. It binds to localhost by default and has no authentication: a local benchmark service.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from historian import HistorianSchemaError  # noqa: E402
from historian.api import create_app  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 epilog="Record a file first: python scripts/record_history.py --db exports/SCN-COOL-001.sqlite")
    ap.add_argument("--database", required=True, help="Historian SQLite file (read only)")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8060, help="port (default 8060)")
    ap.add_argument("--access", choices=("current", "evaluator"), default="current",
                    help="current: the current scope only (default, agent-facing); evaluator: every scope "
                         "(benchmark evaluator only)")
    args = ap.parse_args()

    try:
        app = create_app(args.database, access=args.access)
    except FileNotFoundError:
        print(f"[historian-api] no such file: {args.database}")
        return 2
    except HistorianSchemaError as exc:
        print(f"[historian-api] {exc}")
        return 2

    import uvicorn
    print(f"[historian-api] http://{args.host}:{args.port}/status  (access {args.access}, read only)", flush=True)
    if args.access == "evaluator":
        print("[historian-api] EVALUATOR access: every recorded scope is readable. Do not give this service to an agent.",
              flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
