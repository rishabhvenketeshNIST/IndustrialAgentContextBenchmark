"""Record the operational projection of a simulation into the Historian (SQLite).

``HistorianWriter`` is an observer of a ``SimulatorService`` (``add_observer``), exactly like the UNS
publisher and independent of it: it never uses MQTT, never creates or steps a simulation, and reads the
simulation only through ``projection.operational.OperationalProjection``. It sees every simulated
second (the service advances one second at a time while an observer is attached) and every lifecycle
or operator command.

What is recorded (docs/HISTORIAN_DATA_MODEL.md):

* step_state measurements on the sampling grid ``t % sample_period_s == 0`` (default every second);
* analyzer_sample measurements (XMEAS 23-41) at their catalog schedule: a result sampled at k*P is
  observed at the end of the one-second step that begins at k*P, i.e. at t = k*P + 1 (k >= 1), with
  the catalog dead time; every scheduled result is an observation, equal values included;
* operational state as property-level changes, by the projection's report-by-exception rule
  (``StateChangeFilter``) with this recording's grid as the tick;
* operational events (OE-/LC-) at their own simulation time; the lifecycle as the site's
  ``lifecycle_status``.

Nothing is backfilled: a writer attached to a running simulation records a baseline of the current
state and starts its coverage there. Writes are batched and committed every ``commit_interval_s``
simulated seconds and at every lifecycle change, reset and close; coverage is committed with the data,
so it only ever describes durable history.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from projection.operational import (PRODUCTION_MEASUREMENTS, STEP_STATE, OperationalProjection, StateChangeFilter,
                                    clean, load_context_model, load_contract)
from simulator.tep import catalog

from . import SCHEMA, SCHEMA_SQL, HistorianIntegrityError, HistorianSchemaError

LIFECYCLE_PROPERTY = "lifecycle_status"
ANALYZER_OBSERVATION_DELAY_S = 1     # a result sampled at k*P is observable at the end of that step
# the analyzer measurements (catalog measurement_type "sampled"): XMEAS 23-41
ANALYZER_VARIABLES = tuple(f"measurement:XMEAS({i})" for i, v in enumerate(catalog.XMEAS, start=1)
                           if v.measurement_type == catalog.MeasurementType.SAMPLED)


def canonical_json(value) -> str:
    return json.dumps(clean(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def analyzer_schedule(variable: str) -> Tuple[int, int]:
    """(sample period, dead time) in seconds of an analyzer XMEAS, from the TEP catalog."""
    v = catalog.XMEAS[int(variable[len("measurement:XMEAS("):-1]) - 1]
    return round(v.sample_period_h * 3600), round(v.dead_time_h * 3600)


def analyzer_due(variable: str, t: int) -> bool:
    """True at the times a new analyzer result is observed: t = k*P + 1, k >= 1."""
    period, _ = analyzer_schedule(variable)
    return t > period and (t - ANALYZER_OBSERVATION_DELAY_S) % period == 0


def series_kind(entity_id: str, variable: str, production_unit: str) -> str:
    if variable.startswith("measurement:XMEAS("):
        return "xmeas"
    if variable.startswith("manipulated_variable:XMV("):
        return "xmv"
    if variable in ("setpoint", "controller_output"):
        return variable
    if entity_id == production_unit and variable in PRODUCTION_MEASUREMENTS:
        return "production"
    return "property"


# ---------------------------------------------------------------------------- storage
class HistorianStore:
    """The SQLite file: schema, idempotent inserts, integrity checks. Not thread-safe by itself; the
    writer serialises access under the simulator service lock."""

    def __init__(self, path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)     # e.g. exports/ on a fresh clone
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA synchronous = NORMAL")
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not tables:
            self.db.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
            self.db.execute("INSERT INTO historian_meta (key, value) VALUES ('schema', ?)", (SCHEMA,))
            self.db.commit()
        else:
            check_schema(self.db)
        self._series: Dict[Tuple[str, str, str], int] = {}

    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    # ------------------------------------------------------------------ scopes and coverage
    def put_scope(self, scope: str, simulation_start: str, duration_s: int, sample_period_s: int) -> None:
        row = (scope, simulation_start, duration_s, sample_period_s, SCHEMA)
        cur = self.db.execute("INSERT INTO scopes VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING", row)
        if cur.rowcount == 0:
            have = self.db.execute("SELECT * FROM scopes WHERE operational_scope_id = ?", (scope,)).fetchone()
            if tuple(have) != row:
                raise HistorianIntegrityError(f"scope {scope} is already recorded with different settings: {have}")

    def has_history(self, scope: str) -> bool:
        return self.db.execute("SELECT 1 FROM coverage WHERE operational_scope_id = ? LIMIT 1", (scope,)).fetchone() \
            is not None

    def open_interval(self, scope: str, t: int) -> int:
        return self.db.execute("INSERT INTO coverage VALUES (?, ?, ?)", (scope, t, t)).lastrowid

    def extend_interval(self, rowid: int, t: int) -> None:
        self.db.execute("UPDATE coverage SET to_t = ? WHERE rowid = ?", (t, rowid))

    def max_seq(self, scope: str) -> int:
        a = self.db.execute("SELECT MAX(seq) FROM state_changes WHERE operational_scope_id = ?", (scope,)).fetchone()[0]
        b = self.db.execute("SELECT MAX(seq) FROM events WHERE operational_scope_id = ?", (scope,)).fetchone()[0]
        return max(a or 0, b or 0)

    # ------------------------------------------------------------------ entities and series
    def put_entity(self, scope: str, entity_id: str, entity_type: str, parent_id: Optional[str],
                   isa95_path: List[str], mapping_id: Optional[str], name: Optional[str], first_t: int) -> None:
        self.db.execute("INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                        (scope, entity_id, entity_type, parent_id, json.dumps(isa95_path), mapping_id, name, first_t))

    def series_id(self, scope: str, entity_id: str, variable: str, kind: str, unit: str, semantics: str,
                  sample_period_s: int, dead_time_s: Optional[int], source: str) -> int:
        key = (scope, entity_id, variable)
        sid = self._series.get(key)
        if sid is not None:
            return sid
        meta = (kind, unit, semantics, sample_period_s, dead_time_s, source)
        have = self.db.execute("SELECT series_id, kind, unit, semantics, sample_period_s, dead_time_s, source "
                               "FROM series WHERE operational_scope_id = ? AND entity_id = ? AND variable = ?",
                               key).fetchone()
        if have is None:
            sid = self.db.execute("INSERT INTO series (operational_scope_id, entity_id, variable, kind, unit, semantics, "
                                  "sample_period_s, dead_time_s, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                  key + meta).lastrowid
        elif tuple(have[1:]) != meta:
            raise HistorianIntegrityError(f"series {key} is already recorded with different metadata: {have}")
        else:
            sid = have[0]
        self._series[key] = sid
        return sid

    # ------------------------------------------------------------------ observations
    def put_samples(self, rows: List[Tuple[int, int, Optional[float], Optional[str]]]) -> None:
        """(series_id, t, value, quality). An existing identical sample is a no-op; an existing
        different one raises HistorianIntegrityError."""
        if not rows:
            return
        cur = self.db.executemany("INSERT INTO samples VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING", rows)
        if cur.rowcount != len(rows):
            for sid, t, value, quality in rows:
                have = self.db.execute("SELECT value, quality FROM samples WHERE series_id = ? AND t = ?",
                                       (sid, t)).fetchone()
                if have != (value, quality):
                    raise HistorianIntegrityError(
                        f"series {sid} at t={t} already holds {have}, not {(value, quality)}")

    def put_state_change(self, scope: str, entity_id: str, prop: str, t: int, seq: int, value: str,
                         unit: Optional[str], origin: str) -> None:
        self.db.execute("INSERT INTO state_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (scope, entity_id, prop, t, seq, value, unit, origin))

    def put_event(self, scope: str, ev: dict, seq: int) -> bool:
        """Insert an operational event; returns False (no-op) if the identical event is already
        recorded, raises HistorianIntegrityError if a different event has the same id."""
        row = (ev["simulation_time"], ev["event_type"], ev["entity_id"], ev["source"], ev["severity"],
               canonical_json(ev["payload"]), ev["causation_id"], ev["correlation_id"])
        cur = self.db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                              (scope, ev["event_id"], row[0], seq) + row[1:])
        if cur.rowcount:
            return True
        have = self.db.execute("SELECT t, event_type, entity_id, source, severity, payload, causation_id, "
                               "correlation_id FROM events WHERE operational_scope_id = ? AND event_id = ?",
                               (scope, ev["event_id"])).fetchone()
        if tuple(have) != row:
            raise HistorianIntegrityError(f"event {ev['event_id']} of {scope} is already recorded differently")
        return False


def check_schema(db: sqlite3.Connection) -> None:
    try:
        row = db.execute("SELECT value FROM historian_meta WHERE key = 'schema'").fetchone()
    except sqlite3.DatabaseError as exc:
        raise HistorianSchemaError(f"not a Historian database: {exc}") from None
    if row is None or row[0] != SCHEMA:
        raise HistorianSchemaError(f"unsupported Historian schema {row[0] if row else None!r}; expected {SCHEMA!r}")


# ---------------------------------------------------------------------------- the writer
class HistorianWriter:
    def __init__(self, service, path, sample_period_s: int = 1, commit_interval_s: int = 60) -> None:
        if int(sample_period_s) < 1 or int(commit_interval_s) < 1:
            raise ValueError("sample_period_s and commit_interval_s must be >= 1")
        self.service = service
        self.store = HistorianStore(path)
        self.period = int(sample_period_s)
        self.commit_interval = int(commit_interval_s)
        self.cm = load_context_model()
        self.contract = load_contract()
        self.projection: Optional[OperationalProjection] = None
        self._engine = None
        self.rows = {"samples": 0, "state_changes": 0, "events": 0}     # written by this writer (diagnostics)

    # ------------------------------------------------------------------ lifecycle
    def attach(self) -> "HistorianWriter":
        """Follow the service's simulation: register as an observer and record the current state."""
        self.service.add_observer(self.sync)
        self.sync()
        return self

    def flush(self) -> None:
        with self.service.lock:
            self._commit()

    def close(self) -> None:
        """Stop observing, commit everything recorded so far, and close the file."""
        self.service.remove_observer(self.sync)
        with self.service.lock:
            self._commit()
            self.store.close()

    def sync(self) -> None:
        """Record what the projection shows now. Called by the service after every simulated second
        and every lifecycle or operator command."""
        with self.service.lock:
            eng = self.service.engine
            if eng is None:
                return
            if eng is not self._engine:
                self._begin_scope(eng)
            self._observe()

    # ------------------------------------------------------------------ scope
    def _begin_scope(self, eng) -> None:
        if self._engine is not None:
            self._commit()                                   # the previous scope's history is complete
        self._engine = eng
        proj = self.projection = OperationalProjection(eng, self.cm, self.contract)
        self.scope = proj.scope_id
        t = proj.simulation_time()
        life = proj.lifecycle()
        self.store.put_scope(self.scope, life["simulation_start"], life["duration_seconds"], self.period)
        resumed = self.store.has_history(self.scope)
        self._seq = self.store.max_seq(self.scope)
        self._filter = StateChangeFilter()
        self._values: Dict[Tuple[str, str], str] = {}        # last recorded value per (entity, property)
        self._known: set = set()
        self._nodes = set(proj.entity_ids())
        self._interval: Optional[int] = None
        self._last_t: Optional[int] = None
        self._last_tick: Optional[int] = None
        self._last_analyzer_t: Optional[int] = None
        self._lifecycle: Optional[str] = None
        self._commit_t = t
        # events before this writer are history it did not observe: never backfilled
        self._events_done = 0 if (t == 0 and not resumed) else proj.event_count()
        for cid in proj.entity_ids():
            self._entity(cid, t)

    def _entity(self, cid: str, t: int) -> None:
        if cid in self._known:
            return
        pl = self.projection.placement
        name = self.projection.meta(cid).get("name") if cid in self._nodes else None     # records have no name
        self.store.put_entity(self.scope, cid, cid.split(":", 1)[0], pl.parent(cid), list(pl.path(cid)),
                              pl.isa95_of(cid)["mapping_id"], name, t)
        self._known.add(cid)

    # ------------------------------------------------------------------ one observation
    def _observe(self) -> None:
        proj = self.projection
        t = proj.simulation_time()
        if self._interval is None:
            self._interval = self.store.open_interval(self.scope, t)
        elif t > self._last_t + 1:                           # seconds this writer did not see: a gap
            self._interval = self.store.open_interval(self.scope, t)

        for ev in proj.events_since(self._events_done):
            self._events_done += 1
            self._entity(ev["entity_id"], t)
            if self.store.put_event(self.scope, ev, self._seq + 1):
                self._seq += 1
                self.rows["events"] += 1

        status = proj.lifecycle()["status"]
        lifecycle_changed = status != self._lifecycle
        if lifecycle_changed:
            self._lifecycle = status
            self._state(proj.placement.site, {LIFECYCLE_PROPERTY: status}, {}, t)

        tick = t % self.period == 0 and t != self._last_tick
        for cid in proj.entity_ids():
            s = proj.entity_state(cid)
            if s is not None and self._filter.report(cid, s, tick, False):
                self._state(cid, s["state"], s.get("units", {}), t)
        for coll, cid, fields in proj.records():
            self._entity(cid, t)
            if self._filter.report(cid, fields, tick, coll):
                self._state(cid, fields["state"], {}, t)

        analyzers = t != self._last_analyzer_t and any(analyzer_due(v, t) for v in ANALYZER_VARIABLES)
        if tick or analyzers:
            self._samples(t, tick, analyzers)
            if tick:
                self._last_tick = t
            if analyzers:
                self._last_analyzer_t = t

        self._last_t = t
        self.store.extend_interval(self._interval, t)
        if lifecycle_changed or t - self._commit_t >= self.commit_interval:
            self._commit()

    def _samples(self, t: int, tick: bool, analyzers: bool) -> None:
        proj, rows = self.projection, []
        pu = proj.placement.production_unit
        for cid in proj.entity_ids():
            for variable, f in proj.measurements(cid):
                semantics = proj.measurement_semantics(variable)
                if semantics == STEP_STATE:
                    if not tick:
                        continue
                    period, dead = self.period, None
                elif analyzers and analyzer_due(variable, t):
                    period, dead = analyzer_schedule(variable)
                else:
                    continue
                sid = self.store.series_id(self.scope, cid, variable, series_kind(cid, variable, pu), f.get("unit"),
                                           semantics, period, dead, f["source"])
                value = clean(f["value"])
                rows.append((sid, t, None if value is None else float(value), f.get("quality")))
        self.store.put_samples(rows)
        self.rows["samples"] += len(rows)

    def _state(self, cid: str, state: dict, units: dict, t: int) -> None:
        for prop in sorted(state):
            value = canonical_json(state[prop])
            key = (cid, prop)
            if self._values.get(key) == value:
                continue
            self._seq += 1
            self.store.put_state_change(self.scope, cid, prop, t, self._seq, value, units.get(prop),
                                        "change" if key in self._values else "baseline")
            self._values[key] = value
            self.rows["state_changes"] += 1

    def _commit(self) -> None:
        if self._engine is not None and self._last_t is not None:
            self._commit_t = self._last_t
        self.store.commit()

