"""The UNS topic namespace: MQTT topics over the ISA-95 placement of the operational projection.

No hierarchy is defined here. Where an entity or record sits comes from
``projection.operational.Isa95Placement`` (the simulator's ISA-95 hierarchy and the canonical context
model); this module only turns that placement into MQTT topics:

    <root>/<canonical id>/.../<canonical id>/<channel>[/<leaf>]

A topic segment is a canonical id ``<entity_type>:<native_id>`` escaped for MQTT (see ``escape``).
Channel segments (``meta``, ``state``, ``lifecycle``, ``measurement``, ``event``, ``uns``) never contain
':' and so cannot be confused with entity segments.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from projection.operational import CONTEXT_MODEL, ROOT_DIR, Isa95Placement, canonical_id, load_context_model

from . import DEFAULT_ROOT

# the context-model helpers are re-exported for existing callers of uns.namespace
__all__ = ["CONTEXT_MODEL", "ROOT_DIR", "Isa95Placement", "Namespace", "canonical_id", "escape",
           "load_context_model", "unescape"]

# Characters with a meaning in MQTT topics (level separator, wildcards, NUL) and the escape character.
_ESCAPES = {"%": "%25", "/": "%2F", "+": "%2B", "#": "%23", "\x00": "%00"}


def escape(segment: str) -> str:
    """Escape a canonical id for use as one MQTT topic level. Only '%', '/', '+', '#' and NUL are
    percent-encoded (plus a leading '$', reserved for broker topics). The payload always carries the
    unescaped canonical id."""
    out = "".join(_ESCAPES.get(c, c) for c in segment)
    return "%24" + out[1:] if out.startswith("$") else out


def unescape(segment: str) -> str:
    for plain, enc in [("$", "%24")] + [(k, v) for k, v in _ESCAPES.items() if k != "%"] + [("%", "%25")]:
        segment = segment.replace(enc, plain)
    return segment


class Namespace:
    """Topic paths for one engine (one operational scope). ``placement`` is shared with the
    operational projection, so records registered by either are placed once; the placement's lookups
    (``path``, ``parent``, ``entity_ids``, ``site``, ...) are available here unchanged."""

    def __init__(self, engine_or_placement, context_model: Optional[dict] = None, root: str = DEFAULT_ROOT) -> None:
        if isinstance(engine_or_placement, Isa95Placement):
            self.placement = engine_or_placement
        else:
            self.placement = Isa95Placement(engine_or_placement, context_model)
        self.root = root.strip("/")
        self._topics: Dict[Tuple[str, str, Optional[str]], str] = {}

    def __getattr__(self, name):
        # ISA-95 placement lookups (engine, site, production_unit, path, parent, entity_ids, record_ids,
        # register_record, isa95_of, collection_type, ...) belong to the placement
        if name == "placement":
            raise AttributeError(name)
        return getattr(self.placement, name)

    def topic(self, cid: str, channel: str, leaf: Optional[str] = None) -> str:
        key = (cid, channel, leaf)
        t = self._topics.get(key)
        if t is None:
            parts = [self.root] + [escape(s) for s in self.placement.path(cid)] + [channel]
            if leaf is not None:
                parts.append(escape(leaf))
            t = self._topics[key] = "/".join(parts)
        return t

    def publisher_status_topic(self) -> str:
        return self.topic(self.placement.site, "uns", "publisher")
