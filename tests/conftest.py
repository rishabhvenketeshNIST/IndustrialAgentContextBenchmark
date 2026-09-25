import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.scenarios import ScenarioStore, load_base_config  # noqa: E402
from simulator.simulation.engine import SimulationEngine  # noqa: E402
from simulator.tep import fortran_backend  # noqa: E402

requires_fortran = pytest.mark.skipif(not fortran_backend.is_available(),
                                      reason="Fortran TEP library not built (python scripts/build_fortran.py)")

_BASE = None


def base_config():
    global _BASE
    if _BASE is None:
        _BASE = load_base_config()
    return copy.deepcopy(_BASE)


def scenario(**kw):
    sc = {"id": "SCN-TEST", "name": "test", "seed": 7, "duration_seconds": 3600, "backend": "auto",
          "production_orders": [{"order_id": "PO-T1", "product_id": "PROD-GH-M1", "quantity": 8000,
                                 "planned_start": 0, "planned_end": 3600}]}
    sc.update(kw)
    return sc


def make_engine(**kw):
    return SimulationEngine(scenario(**kw), base_config())


@pytest.fixture
def engine():
    return make_engine()


@pytest.fixture(scope="session")
def demo_run():
    """The full 3 h demonstration scenario, run once per session."""
    sc = ScenarioStore().load("SCN-COOL-001")
    e = SimulationEngine(sc, base_config())
    e.run()
    return e
