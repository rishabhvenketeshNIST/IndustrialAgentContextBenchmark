"""The Historian: historical operational context, a peer of the UNS (docs/HISTORIAN.md).

Both consume the transport-neutral operational projection (projection/operational.py); the Historian
stores it, over simulation time, in one SQLite file per benchmark session. It never depends on MQTT.

    historian/schema.sql   the database schema (SCHEMA)
    historian/writer.py    HistorianWriter: an observer of SimulatorService that records the projection
    historian/reader.py    HistorianReader: bounded, scope-limited queries
"""
from pathlib import Path

SCHEMA = "acme-historian/1"
SCHEMA_SQL = Path(__file__).with_name("schema.sql")


class HistorianError(Exception):
    """Base class of Historian errors."""


class HistorianIntegrityError(HistorianError):
    """Conflicting data for a key that already holds a different observation."""


class HistorianSchemaError(HistorianError):
    """The database has no, or an incompatible, Historian schema version."""
