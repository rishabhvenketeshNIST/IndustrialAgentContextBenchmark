"""Shared utilities: deterministic RNG streams, safe expressions, config I/O, hashing."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "configs"
SCENARIO_DIR = ROOT / "scenarios"


class ConfigError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Deterministic randomness
# ---------------------------------------------------------------------------
def stable_hash32(text: str) -> int:
    """Process-independent 32-bit hash (Python's hash() is salted per process)."""
    return zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF


class RandomStreams:
    """Independent, named random streams derived from one scenario seed.

    Each module draws from its own stream, so adding draws in one module never
    perturbs another module's sequence.
    """

    def __init__(self, seed: int) -> None:
        self.seed = int(seed)
        self._streams: Dict[str, np.random.Generator] = {}

    def get(self, name: str) -> np.random.Generator:
        if name not in self._streams:
            ss = np.random.SeedSequence([self.seed & 0xFFFFFFFF, (self.seed >> 32) & 0xFFFFFFFF,
                                         stable_hash32(name)])
            self._streams[name] = np.random.Generator(np.random.PCG64(ss))
        return self._streams[name]

    def tep_seed(self) -> int:
        """Seed for the TEP LCG (G in /RANDSD/); odd, in [1, 2^32)."""
        ss = np.random.SeedSequence([self.seed & 0xFFFFFFFF, stable_hash32("tep.randsd")])
        return int(ss.generate_state(1, dtype=np.uint32)[0]) | 1


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------
def parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sim_timestamp(start: datetime, sim_seconds: float) -> str:
    return iso(start + timedelta(seconds=float(sim_seconds)))


def fmt_hms(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


# ---------------------------------------------------------------------------
# Config files
# ---------------------------------------------------------------------------
def load_yaml(path: Path) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"Configuration file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc


def dump_yaml(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)


def deep_merge(base: Any, override: Any) -> Any:
    """Recursively merge ``override`` into a copy of ``base``.

    Lists of dicts carrying an ``id`` are merged by id; other lists replace.
    """
    if isinstance(base, dict) and isinstance(override, dict):
        out = copy.deepcopy(base)
        for k, v in override.items():
            out[k] = deep_merge(out[k], v) if k in out else copy.deepcopy(v)
        return out
    if (isinstance(base, list) and isinstance(override, list) and base and override
            and all(isinstance(x, dict) and "id" in x for x in base + override)):
        out = [copy.deepcopy(x) for x in base]
        index = {x["id"]: i for i, x in enumerate(out)}
        for item in override:
            if item["id"] in index:
                out[index[item["id"]]] = deep_merge(out[index[item["id"]]], item)
            else:
                out.append(copy.deepcopy(item))
        return out
    return copy.deepcopy(override)


def canonical_json(data: Any) -> str:
    return json.dumps(to_jsonable(data), sort_keys=True, separators=(",", ":"))


def config_hash(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def to_jsonable(obj: Any) -> Any:
    """Convert numpy / dataclass / datetime values into JSON-safe structures."""
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return v if math.isfinite(v) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return [to_jsonable(x) for x in obj.tolist()]
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, datetime):
        return iso(obj)
    if hasattr(obj, "to_dict"):
        return to_jsonable(obj.to_dict())
    if hasattr(obj, "value") and hasattr(obj, "name"):  # Enum
        return obj.value
    raise TypeError(f"Not JSON serialisable: {type(obj)}")

