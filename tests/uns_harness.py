"""Record the exact UNS publication of a scripted scenario, without a broker (regression harness).

The MQTT client of a real ``UNSPublisher`` is replaced by a recorder, so every
``publish(topic, payload, qos, retain)`` call is captured byte for byte and in call order (stronger than
a broker round-trip, where topics may interleave). Only two things are controlled:
``observed_at`` (wall clock) is fixed, and the random operational scope ids are replaced by
``<scope-1>``, ``<scope-2>`` in order of appearance.

The scenario drives the simulation only through the service API (as the web UI does): run-until,
an operator action, start/pause/resume, a step ending between sampling ticks, the 01:20:33 alarm and
maintenance, and a reset into a second scope.

    python -m tests.uns_harness      # print the digest (used to create tests/golden/uns_stream.json)
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).with_name("golden") / "uns_stream.json"
FIXED_OBSERVED_AT = "2000-01-01T00:00:00.000Z"
_SCOPE = re.compile(r"OS-[0-9a-f]{32}")


class _Info:
    rc = 0

    def __init__(self, mid):
        self.mid = mid

    def wait_for_publish(self, timeout=None):
        return True


class RecordingClient:
    """Stands in for paho's client: records publishes and acknowledges each one on the next call
    (after the publisher has registered it as pending), like an immediate PUBACK."""

    def __init__(self, publisher):
        self.publisher, self.calls, self._mid = publisher, [], 0

    def publish(self, topic, payload, qos=0, retain=False):
        if self._mid:
            self.publisher._on_publish(None, None, self._mid, None, None)
        self._mid += 1
        self.calls.append((topic, payload, qos, bool(retain)))
        return _Info(self._mid)

    def disconnect(self):
        pass

    def loop_stop(self):
        pass


def record(steps=None):
    """Run the scripted scenario and return the list of (topic, payload, qos, retain) calls."""
    sys.path.insert(0, str(ROOT))
    import uns.publisher as up
    from api.service import SimulatorService
    from simulator.tep import control_scheme as cs
    from uns.namespace import Namespace

    real_wall_clock = up.wall_clock
    up.wall_clock = lambda: FIXED_OBSERVED_AT          # restored below: other tests need the real clock
    svc = SimulatorService(start_runner=False)
    try:
        svc.create_simulation("SCN-COOL-001")
        pub = up.UNSPublisher(svc)
        client = RecordingClient(pub)
        pub._client = client
        pub._status_topic = Namespace(svc.engine, pub.cm, pub.root).publisher_status_topic()
        pub._connected.set()
        pub._resync = True                               # as after a real CONNACK
        svc.add_observer(pub.sync)
        pub.sync()
        lc_sep = next(l for l, d in cs.LOOPS.items() if d.tag == "LC-SEP")
        script = steps or [
            ("run_until", 1000), ("operator", ("set_setpoint", {"loop_id": lc_sep, "value": 52.0})),
            ("start", None), ("pause", None), ("resume", None), ("pause", None), ("step", 37),
            ("run_until", 5000), ("reset", None), ("run_until", 120)]
        for op, arg in script:
            if op == "run_until":
                svc.run_until(arg)
            elif op == "step":
                svc.step_simulation(arg)
            elif op == "operator":
                svc.operator_action(arg[0], arg[1])
            else:
                getattr(svc, f"{op}_simulation")()
        pub.close()
        return client.calls
    finally:
        up.wall_clock = real_wall_clock
        svc.shutdown()


def normalise(calls):
    """Replace the random scope ids by <scope-n> in order of appearance (the only random content)."""
    names = {}

    def sub(m):
        return names.setdefault(m.group(0), f"<scope-{len(names) + 1}>")
    return [(t, _SCOPE.sub(sub, p), q, r) for t, p, q, r in calls]


def summary(calls):
    calls = normalise(calls)
    h = hashlib.sha256()
    kinds = {}
    for t, p, q, r in calls:
        h.update(json.dumps([t, p, q, r], separators=(",", ":")).encode("utf-8"))
        h.update(b"\n")
        k = json.loads(p)["kind"] if p else "delete"
        kinds[k] = kinds.get(k, 0) + 1
    return {"sha256": h.hexdigest(), "messages": len(calls), "topics": len({c[0] for c in calls}),
            "kinds": dict(sorted(kinds.items())), "scopes": len({m for c in calls for m in re.findall(r"<scope-\d+>", c[1])})}


if __name__ == "__main__":
    print(json.dumps(summary(record()), indent=2))
