#!/usr/bin/env python
"""Start the read-only UNS inspector: an MQTT client with a small web view.

    python scripts/run_inspector.py [--mqtt-host 127.0.0.1] [--mqtt-port 1883] [--port 8050] [--no-browser]

It subscribes to the Unified Namespace on the broker and serves http://127.0.0.1:8050. It imports
nothing from the simulator: start it against any broker the UNS publisher writes to
(scripts/run_uns.py). See docs/UNS.md, "Visual inspection".
"""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from uns import DEFAULT_ROOT  # noqa: E402
from uns.inspector import UNSInspector, create_app  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mqtt-host", default="127.0.0.1")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--root", default=DEFAULT_ROOT, help="UNS topic root")
    ap.add_argument("--host", default="127.0.0.1", help="web view address")
    ap.add_argument("--port", type=int, default=8050, help="web view port")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    import uvicorn

    inspector = UNSInspector(args.mqtt_host, args.mqtt_port, args.root).start()
    url = f"http://{args.host}:{args.port}/"
    print(f"[inspector] reading mqtt://{args.mqtt_host}:{args.mqtt_port}/{args.root}/#  ->  {url}")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.run(create_app(inspector), host=args.host, port=args.port, log_level="warning")
    finally:
        inspector.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
