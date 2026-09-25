"""TEP integration layer: adapter interface, backends, catalog and native control scheme."""
from __future__ import annotations

from typing import Optional

from .catalog import ALL_VARIABLES, IDV, STATES, XMEAS, XMV, normalize_id, variable
from .control_scheme import ControlMode, LoopMode
from .interface import MeasurementFilter, StepResult, TEPAdapterError, TEPProcessAdapter


def available_backends() -> list:
    from . import fortran_backend
    out = ["python"]
    if fortran_backend.is_available():
        out.insert(0, "fortran")
    return out


def create_adapter(backend: Optional[str] = "auto") -> TEPProcessAdapter:
    """Create a TEP adapter. ``auto`` prefers the authoritative Fortran backend."""
    backend = (backend or "auto").lower()
    if backend == "auto":
        backend = available_backends()[0]
    if backend == "fortran":
        from .fortran_backend import FortranTEPAdapter
        return FortranTEPAdapter()
    if backend == "python":
        from .python_backend import PythonTEPAdapter
        return PythonTEPAdapter()
    raise ValueError(f"Unknown TEP backend '{backend}' (expected auto|fortran|python)")


__all__ = ["create_adapter", "available_backends", "TEPProcessAdapter", "TEPAdapterError", "StepResult",
           "MeasurementFilter", "ControlMode", "LoopMode", "XMEAS", "XMV", "IDV", "STATES",
           "ALL_VARIABLES", "variable", "normalize_id"]
