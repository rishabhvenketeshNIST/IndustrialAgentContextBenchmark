"""Scenario definitions and configuration assembly.

A scenario (YAML or JSON) selects seed, duration, start instant, backend and
control mode, and adds faults, production orders, planned maintenance and
scripted operator actions. ``config_overrides`` are deep-merged over the base
configuration files in ``configs/``.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..common import (CONFIG_DIR, SCENARIO_DIR, ConfigError, config_hash, deep_merge, dump_yaml, load_yaml)

CONFIG_SECTIONS = ("site", "tep_mapping", "equipment", "utilities", "coupling", "materials", "production",
                   "quality", "maintenance", "alarms", "warehouse", "simulation")

SCENARIO_KEYS = {"id", "name", "description", "seed", "tep_seed", "duration_seconds", "simulation_start", "backend",
                 "control_mode", "config_overrides", "faults", "production_orders", "planned_maintenance",
                 "operator_actions", "tags"}

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def load_base_config(config_dir: Path = CONFIG_DIR) -> Dict[str, Any]:
    cfg = {}
    for section in CONFIG_SECTIONS:
        path = config_dir / f"{section}.yaml"
        cfg[section] = load_yaml(path) or {}
    return cfg


def validate_scenario(sc: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(sc, dict):
        raise ConfigError("scenario must be a mapping")
    if "scenario" in sc and isinstance(sc["scenario"], dict):
        sc = sc["scenario"]
    unknown = set(sc) - SCENARIO_KEYS
    if unknown:
        raise ConfigError(f"Unknown scenario keys: {sorted(unknown)}")
    sc = copy.deepcopy(sc)
    if not _ID_RE.match(str(sc.get("id", ""))):
        raise ConfigError("scenario.id is required (letters, digits, _ . -)")
    try:
        sc["seed"] = int(sc.get("seed", 1))
    except (TypeError, ValueError) as exc:
        raise ConfigError("seed must be an integer") from exc
    if sc["seed"] < 0:
        raise ConfigError("seed must be >= 0")
    dur = int(sc.get("duration_seconds", 10800))
    if not 1 <= dur <= 30 * 24 * 3600:
        raise ConfigError("duration_seconds must be between 1 and 30 days")
    sc["duration_seconds"] = dur
    sc.setdefault("name", sc["id"])
    sc.setdefault("description", "")
    sc.setdefault("simulation_start", "2026-01-05T06:00:00Z")
    sc.setdefault("backend", "auto")
    if sc["backend"] not in ("auto", "fortran", "python"):
        raise ConfigError("backend must be auto | fortran | python")
    sc.setdefault("control_mode", "CLOSED_LOOP")
    if sc["control_mode"] not in ("CLOSED_LOOP", "MANUAL"):
        raise ConfigError("control_mode must be CLOSED_LOOP or MANUAL")
    for key in ("faults", "production_orders", "planned_maintenance", "operator_actions"):
        sc[key] = list(sc.get(key) or [])
        if any(not isinstance(x, dict) for x in sc[key]):
            raise ConfigError(f"{key} must be a list of mappings")
    sc["config_overrides"] = dict(sc.get("config_overrides") or {})
    bad = set(sc["config_overrides"]) - set(CONFIG_SECTIONS)
    if bad:
        raise ConfigError(f"config_overrides for unknown sections: {sorted(bad)}")
    ids = [f.get("id") for f in sc["faults"]]
    if len(ids) != len(set(ids)):
        raise ConfigError("duplicate fault ids in scenario")
    return sc


def assemble_config(scenario: Dict[str, Any], base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base = base if base is not None else load_base_config()
    cfg = copy.deepcopy(base)
    for section, override in scenario.get("config_overrides", {}).items():
        cfg[section] = deep_merge(cfg.get(section, {}), override)
    for key in ("faults", "production_orders", "planned_maintenance", "operator_actions"):
        cfg[key] = copy.deepcopy(scenario.get(key, []))
    return cfg


def configuration_hash(scenario: Dict[str, Any], config: Dict[str, Any]) -> str:
    return config_hash({"scenario": scenario, "config": config})


class ScenarioStore:
    """Load / save / duplicate scenario files in a directory (YAML or JSON)."""

    def __init__(self, directory: Path = SCENARIO_DIR) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, scenario_id: str) -> Optional[Path]:
        for ext in (".yaml", ".yml", ".json"):
            p = self.dir / f"{scenario_id}{ext}"
            if p.exists():
                return p
        for p in sorted(self.dir.glob("*")):
            if p.suffix in (".yaml", ".yml", ".json"):
                try:
                    if self._read(p).get("id") == scenario_id:
                        return p
                except ConfigError:
                    continue
        return None

    @staticmethod
    def _read(path: Path) -> Dict[str, Any]:
        if path.suffix == ".json":
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        else:
            raw = load_yaml(path)
        return validate_scenario(raw)

    def list(self) -> List[dict]:
        out = []
        for p in sorted(self.dir.glob("*")):
            if p.suffix not in (".yaml", ".yml", ".json"):
                continue
            try:
                sc = self._read(p)
                out.append({"id": sc["id"], "name": sc["name"], "description": sc["description"],
                            "seed": sc["seed"], "duration_seconds": sc["duration_seconds"],
                            "faults": len(sc["faults"]), "file": p.name})
            except ConfigError as exc:
                out.append({"id": p.stem, "error": str(exc), "file": p.name})
        return out

    def load(self, scenario_id: str) -> Dict[str, Any]:
        p = self._path(scenario_id)
        if p is None:
            raise KeyError(f"Scenario '{scenario_id}' not found in {self.dir}")
        return self._read(p)

    def save(self, scenario: Dict[str, Any], overwrite: bool = False) -> Path:
        sc = validate_scenario(scenario)
        existing = self._path(sc["id"])
        if existing is not None and not overwrite:
            raise ConfigError(f"Scenario '{sc['id']}' already exists (use overwrite)")
        path = existing if existing is not None and existing.suffix in (".yaml", ".yml") \
            else self.dir / f"{sc['id']}.yaml"
        dump_yaml({"scenario": sc}, path)
        return path

    def duplicate(self, scenario_id: str, new_id: str, new_name: Optional[str] = None) -> Dict[str, Any]:
        sc = self.load(scenario_id)
        sc["id"] = new_id
        sc["name"] = new_name or f"{sc['name']} (copy)"
        self.save(sc)
        return sc

    def delete(self, scenario_id: str) -> None:
        p = self._path(scenario_id)
        if p is None:
            raise KeyError(scenario_id)
        p.unlink()
