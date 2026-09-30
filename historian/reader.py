"""Read the Historian: bounded queries over one operational scope at a time.

Every query names a scope; there are no cross-scope queries. With ``access="current"`` (the default,
for agent-facing use) only the scope most recently begun in the file is readable; with
``access="evaluator"`` any recorded scope is. Ranges are half-open ``[start, end)`` in simulation
seconds, spans are capped at ``MAX_SPAN_S`` and results at ``MAX_ROWS``; a result that hit its limit
says so (``truncated``) and gives the position to continue from.

Range queries page by keyset: pass the ``next_after`` of a truncated result as ``after`` (the key of
the last row returned: ``t`` for samples, ``(t, seq)`` for events and state changes) to continue in the
deterministic history order.

Errors are typed: ``HistorianNotFoundError`` (kind scope, entity or series), ``HistorianQueryError``
(code missing_scope, invalid_range, span_exceeded, invalid_limit) and ``HistorianAccessError``.

Values are returned as recorded: no interpolation, no aggregation. ``value_at`` returns the last sample
at or before t with its time, age and timestamp semantics, and whether coverage is continuous from
that sample to t, so an answer never silently spans a recording gap.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

from . import HistorianError, HistorianNotFoundError, HistorianQueryError
from .writer import check_schema

MAX_SPAN_S = 86400          # one simulated day per range query
DEFAULT_LIMIT = 1000
MAX_ROWS = 10000


class HistorianAccessError(HistorianError):
    """A query for a scope this reader may not read."""


class HistorianReader:
    def __init__(self, path, access: str = "current") -> None:
        if access not in ("current", "evaluator"):
            raise ValueError("access must be 'current' or 'evaluator'")
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)
        self.db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
        check_schema(self.db)
        self.access = access

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------ scopes
    def current_scope(self) -> Optional[str]:
        """The scope most recently begun by a writer in this file."""
        row = self.db.execute("SELECT operational_scope_id FROM scopes ORDER BY rowid DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def scopes(self) -> List[dict]:
        rows = self.db.execute("SELECT operational_scope_id, simulation_start, duration_seconds, sample_period_s, "
                               "schema FROM scopes ORDER BY rowid").fetchall()
        out = [dict(zip(("operational_scope_id", "simulation_start", "duration_seconds", "sample_period_s",
                         "schema"), r)) for r in rows]
        if self.access == "current":
            out = [s for s in out if s["operational_scope_id"] == self.current_scope()]
        return out

    def _scope(self, scope: str) -> str:
        if not scope:
            raise HistorianQueryError("missing_scope", "a scope is required")
        if self.db.execute("SELECT 1 FROM scopes WHERE operational_scope_id = ?", (scope,)).fetchone() is None:
            raise HistorianNotFoundError("scope", f"scope {scope} is not recorded")
        if self.access == "current" and scope != self.current_scope():
            raise HistorianAccessError(f"scope {scope} is not the current scope")
        return scope

    def coverage(self, scope: str) -> List[dict]:
        """Committed, continuously recorded intervals [from_t, to_t] (both inclusive)."""
        self._scope(scope)
        return [{"from_t": a, "to_t": b} for a, b in self.db.execute(
            "SELECT from_t, to_t FROM coverage WHERE operational_scope_id = ? ORDER BY from_t", (scope,))]

    def covered(self, scope: str, t0: int, t1: Optional[int] = None) -> bool:
        """True if [t0, t1] (default: the single time t0) lies inside one recorded interval."""
        t1 = t0 if t1 is None else t1
        return self.db.execute("SELECT 1 FROM coverage WHERE operational_scope_id = ? AND from_t <= ? AND to_t >= ?",
                               (self._scope(scope), t0, t1)).fetchone() is not None

    # ------------------------------------------------------------------ entities and series
    def entities(self, scope: str, entity_type: Optional[str] = None, parent_id: Optional[str] = None,
                 limit: int = DEFAULT_LIMIT) -> dict:
        limit = _limit(limit)
        q, args = "SELECT * FROM entities WHERE operational_scope_id = ?", [self._scope(scope)]
        if entity_type:
            q, args = q + " AND entity_type = ?", args + [entity_type]
        if parent_id:
            q, args = q + " AND parent_id = ?", args + [parent_id]
        rows = self.db.execute(q + " ORDER BY entity_id LIMIT ?", args + [limit + 1]).fetchall()
        cols = ("operational_scope_id", "entity_id", "entity_type", "parent_id", "isa95_path", "isa95_mapping_id",
                "name", "first_t")
        out = [dict(zip(cols, r), isa95_path=json.loads(r[4])) for r in rows[:limit]]
        return {"rows": out, "truncated": len(rows) > limit}

    def entity(self, scope: str, entity_id: str) -> dict:
        """One recorded entity of the scope (HistorianNotFoundError if it is not recorded)."""
        row = self.db.execute("SELECT * FROM entities WHERE operational_scope_id = ? AND entity_id = ?",
                              (self._scope(scope), entity_id)).fetchone()
        if row is None:
            raise HistorianNotFoundError("entity", f"entity {entity_id} is not recorded in scope {scope}")
        cols = ("operational_scope_id", "entity_id", "entity_type", "parent_id", "isa95_path", "isa95_mapping_id",
                "name", "first_t")
        return dict(zip(cols, row), isa95_path=json.loads(row[4]))

    def series(self, scope: str, entity_id: Optional[str] = None, variable: Optional[str] = None) -> List[dict]:
        q, args = "SELECT * FROM series WHERE operational_scope_id = ?", [self._scope(scope)]
        if entity_id:
            self.entity(scope, entity_id)
            q, args = q + " AND entity_id = ?", args + [entity_id]
        if variable:
            q, args = q + " AND variable = ?", args + [variable]
        cols = ("series_id", "operational_scope_id", "entity_id", "variable", "kind", "unit", "semantics",
                "sample_period_s", "dead_time_s", "source")
        return [dict(zip(cols, r)) for r in self.db.execute(q + " ORDER BY entity_id, variable", args)]

    def _one_series(self, scope: str, entity_id: str, variable: str) -> dict:
        s = self.series(scope, entity_id, variable)
        if not s:
            raise HistorianNotFoundError("series", f"no series {variable} of {entity_id} in scope {scope}")
        return s[0]

    # ------------------------------------------------------------------ samples
    def samples(self, scope: str, entity_id: str, variable: str, start: int, end: int,
                limit: int = DEFAULT_LIMIT, after: Optional[int] = None) -> dict:
        """Samples with start <= t < end (and t > after), in time order."""
        start, end, limit = _range(start, end), int(end), _limit(limit)
        s = self._one_series(scope, entity_id, variable)
        lo = start if after is None else max(start, int(after) + 1)
        rows = self.db.execute("SELECT t, value, quality FROM samples WHERE series_id = ? AND t >= ? AND t < ? "
                               "ORDER BY t LIMIT ?", (s["series_id"], lo, end, limit + 1)).fetchall()
        truncated = len(rows) > limit
        return {"series": s, "rows": [{"t": t, "value": v, "quality": q} for t, v, q in rows[:limit]],
                "truncated": truncated, "next_start": rows[limit][0] if truncated else None,
                "next_after": rows[limit - 1][0] if truncated else None}

    def value_at(self, scope: str, entity_id: str, variable: str, t: int) -> dict:
        """The last sample at or before t (no interpolation), with its time, age and semantics."""
        s = self._one_series(scope, entity_id, variable)
        row = self.db.execute("SELECT t, value, quality FROM samples WHERE series_id = ? AND t <= ? "
                              "ORDER BY t DESC LIMIT 1", (s["series_id"], int(t))).fetchone()
        out = {"entity_id": entity_id, "variable": variable, "t": int(t), "unit": s["unit"],
               "semantics": s["semantics"], "value": None, "quality": None, "sample_time": None, "age": None,
               "dead_time_s": s["dead_time_s"], "represents_time": None,
               "covered": self.covered(scope, int(t)), "continuous_since_sample": False}
        if row is not None:
            st, v, q = row
            out.update(value=v, quality=q, sample_time=st, age=int(t) - st,
                       continuous_since_sample=self.covered(scope, st, int(t)),
                       represents_time=st - s["dead_time_s"] if s["dead_time_s"] is not None else st)
        return out

    # ------------------------------------------------------------------ events
    def events(self, scope: str, start: int, end: int, entity_id: Optional[str] = None,
               event_type: Optional[str] = None, limit: int = DEFAULT_LIMIT,
               after: Optional[Tuple[int, int]] = None) -> dict:
        """Operational events with start <= t < end (and (t, seq) > after), in history order (t, seq)."""
        start, end, limit = _range(start, end), int(end), _limit(limit)
        q, args = ("SELECT event_id, t, seq, event_type, entity_id, source, severity, payload, causation_id, "
                   "correlation_id FROM events WHERE operational_scope_id = ? AND t >= ? AND t < ?",
                   [self._scope(scope), start, end])
        q, args = _after(q, args, after)
        if entity_id:
            self.entity(scope, entity_id)
            q, args = q + " AND entity_id = ?", args + [entity_id]
        if event_type:
            q, args = q + " AND event_type = ?", args + [event_type]
        rows = self.db.execute(q + " ORDER BY t, seq LIMIT ?", args + [limit + 1]).fetchall()
        cols = ("event_id", "t", "seq", "event_type", "entity_id", "source", "severity", "payload", "causation_id",
                "correlation_id")
        out = [dict(zip(cols, r), payload=json.loads(r[7])) for r in rows[:limit]]
        truncated = len(rows) > limit
        return {"rows": out, "truncated": truncated,
                "next_after": (out[-1]["t"], out[-1]["seq"]) if truncated else None}

    # ------------------------------------------------------------------ state
    def state_changes(self, scope: str, start: int, end: int, entity_id: Optional[str] = None,
                      prop: Optional[str] = None, limit: int = DEFAULT_LIMIT,
                      after: Optional[Tuple[int, int]] = None) -> dict:
        """Property-level state changes with start <= t < end (and (t, seq) > after), in history order."""
        start, end, limit = _range(start, end), int(end), _limit(limit)
        q, args = ("SELECT entity_id, property, t, seq, value, unit, origin FROM state_changes "
                   "WHERE operational_scope_id = ? AND t >= ? AND t < ?", [self._scope(scope), start, end])
        q, args = _after(q, args, after)
        if entity_id:
            self.entity(scope, entity_id)
            q, args = q + " AND entity_id = ?", args + [entity_id]
        if prop:
            q, args = q + " AND property = ?", args + [prop]
        rows = self.db.execute(q + " ORDER BY t, seq LIMIT ?", args + [limit + 1]).fetchall()
        cols = ("entity_id", "property", "t", "seq", "value", "unit", "origin")
        out = [dict(zip(cols, r), value=json.loads(r[4])) for r in rows[:limit]]
        truncated = len(rows) > limit
        return {"rows": out, "truncated": truncated,
                "next_after": (out[-1]["t"], out[-1]["seq"]) if truncated else None}

    def state_at(self, scope: str, entity_id: str, t: int) -> dict:
        """The operational state of an entity at t: for each property, its last recorded value at or
        before t, with the time it was recorded."""
        self.entity(scope, entity_id)
        rows = self.db.execute("SELECT property, value, unit, t, origin FROM state_changes WHERE operational_scope_id = ? "
                               "AND entity_id = ? AND t <= ? ORDER BY property, t, seq",
                               (self._scope(scope), entity_id, int(t))).fetchall()
        props = {}
        for prop, value, unit, since, origin in rows:
            props[prop] = {"value": json.loads(value), "unit": unit, "since": since, "origin": origin}
        return {"entity_id": entity_id, "t": int(t), "covered": self.covered(scope, int(t)), "state": props}


def _limit(limit: int) -> int:
    limit = int(limit)
    if not 1 <= limit <= MAX_ROWS:
        raise HistorianQueryError("invalid_limit", f"limit must be within 1..{MAX_ROWS}")
    return limit


def _range(start: int, end: int) -> int:
    start, end = int(start), int(end)
    if end <= start:
        raise HistorianQueryError("invalid_range", "the range [start, end) must have end > start")
    if end - start > MAX_SPAN_S:
        raise HistorianQueryError("span_exceeded", f"a range may span at most {MAX_SPAN_S} simulated seconds")
    return start


def _after(q: str, args: list, after: Optional[Tuple[int, int]]) -> Tuple[str, list]:
    """Keyset continuation after (t, seq) in history order."""
    if after is None:
        return q, args
    t, seq = int(after[0]), int(after[1])
    return q + " AND (t > ? OR (t = ? AND seq > ?))", args + [t, t, seq]
