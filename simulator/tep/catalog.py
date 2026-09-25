"""Static catalog of Tennessee Eastman Process variables.

Everything in this module is transcribed from the header comments and code of
the authoritative Fortran source ``simulator/tep/fortran/src/teprob.f``
(Downs & Vogel, revised 4-4-91, with the corrected XMV order) and from the
base-case operating point published by Downs & Vogel (1993).

Nothing here is enterprise configuration; the mapping of these variables onto
ISA-95 equipment lives in ``configs/tep_mapping.yaml``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


class VariableKind(str, Enum):
    XMEAS = "XMEAS"
    XMV = "XMV"
    IDV = "IDV"
    STATE = "STATE"


class MeasurementType(str, Enum):
    CONTINUOUS = "continuous"
    SAMPLED = "sampled"


@dataclass(frozen=True)
class TEPVariable:
    kind: VariableKind
    index: int  # 1-based, as in the Fortran source
    name: str
    unit: str
    description: str
    stream: Optional[int] = None
    measurement_type: MeasurementType = MeasurementType.CONTINUOUS
    sample_period_h: Optional[float] = None
    dead_time_h: Optional[float] = None
    base_value: Optional[float] = None
    noise_std: Optional[float] = None  # XNS(i) from TEINIT

    @property
    def id(self) -> str:
        return f"{self.kind.value}({self.index})"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "index": self.index,
            "name": self.name,
            "unit": self.unit,
            "description": self.description,
            "stream": self.stream,
            "measurement_type": self.measurement_type.value,
            "sample_period_h": self.sample_period_h,
            "dead_time_h": self.dead_time_h,
            "base_value": self.base_value,
            "noise_std": self.noise_std,
        }


# XNS(1..41) measurement noise standard deviations, TEINIT lines 1256-1296.
_XNS = [
    0.0012, 18.000, 22.000, 0.0500, 0.2000, 0.2100, 0.3000, 0.5000, 0.0100, 0.0017,
    0.0100, 1.0000, 0.3000, 0.1250, 1.0000, 0.3000, 0.1150, 0.0100, 1.1500, 0.2000,
    0.0100, 0.0100, 0.250, 0.100, 0.250, 0.100, 0.250, 0.025, 0.250, 0.100,
    0.250, 0.100, 0.250, 0.025, 0.050, 0.050, 0.010, 0.010, 0.010, 0.500, 0.500,
]

# Downs & Vogel (1993) base case values for XMEAS(1..41).
_XMEAS_BASE = [
    0.25052, 3664.0, 4509.3, 9.3477, 26.902, 42.339, 2705.0, 75.000, 120.40, 0.33712,
    80.109, 50.000, 2633.7, 25.160, 50.000, 3102.2, 22.949, 65.731, 230.31, 341.43,
    94.599, 77.297, 32.188, 8.8933, 26.383, 6.8820, 18.776, 1.6567, 32.958, 13.823,
    23.978, 1.2565, 18.579, 2.2633, 4.8436, 2.2986, 0.01787, 0.83570, 0.09858, 53.724,
    43.828,
]

# (name, unit, stream) for continuous measurements XMEAS(1..22), teprob.f lines 111-132.
_CONTINUOUS = [
    ("A Feed", "kscmh", 1),
    ("D Feed", "kg/h", 2),
    ("E Feed", "kg/h", 3),
    ("A and C Feed", "kscmh", 4),
    ("Recycle Flow", "kscmh", 8),
    ("Reactor Feed Rate", "kscmh", 6),
    ("Reactor Pressure", "kPa gauge", None),
    ("Reactor Level", "%", None),
    ("Reactor Temperature", "degC", None),
    ("Purge Rate", "kscmh", 9),
    ("Product Separator Temperature", "degC", None),
    ("Product Separator Level", "%", None),
    ("Product Separator Pressure", "kPa gauge", None),
    ("Product Separator Underflow", "m3/h", 10),
    ("Stripper Level", "%", None),
    ("Stripper Pressure", "kPa gauge", None),
    ("Stripper Underflow", "m3/h", 11),
    ("Stripper Temperature", "degC", None),
    ("Stripper Steam Flow", "kg/h", None),
    ("Compressor Work", "kW", None),
    ("Reactor Cooling Water Outlet Temperature", "degC", None),
    ("Separator Cooling Water Outlet Temperature", "degC", None),
]

_COMPONENTS = ["A", "B", "C", "D", "E", "F", "G", "H"]


def _build_xmeas() -> List[TEPVariable]:
    out: List[TEPVariable] = []
    for i, (name, unit, stream) in enumerate(_CONTINUOUS, start=1):
        out.append(TEPVariable(VariableKind.XMEAS, i, name, unit, name, stream,
                               base_value=_XMEAS_BASE[i - 1], noise_std=_XNS[i - 1]))
    # Reactor feed analysis (stream 6): XMEAS(23..28), A..F, 0.1 h sample, 0.1 h dead time
    for k, comp in enumerate(_COMPONENTS[:6]):
        i = 23 + k
        out.append(TEPVariable(VariableKind.XMEAS, i, f"Reactor Feed Analysis {comp}", "mol%",
                               f"Component {comp} in reactor feed (stream 6)", 6,
                               MeasurementType.SAMPLED, 0.1, 0.1, _XMEAS_BASE[i - 1], _XNS[i - 1]))
    # Purge gas analysis (stream 9): XMEAS(29..36), A..H, 0.1 h sample, 0.1 h dead time
    for k, comp in enumerate(_COMPONENTS):
        i = 29 + k
        out.append(TEPVariable(VariableKind.XMEAS, i, f"Purge Gas Analysis {comp}", "mol%",
                               f"Component {comp} in purge gas (stream 9)", 9,
                               MeasurementType.SAMPLED, 0.1, 0.1, _XMEAS_BASE[i - 1], _XNS[i - 1]))
    # Product analysis (stream 11): XMEAS(37..41), D..H, 0.25 h sample, 0.25 h dead time
    for k, comp in enumerate(_COMPONENTS[3:]):
        i = 37 + k
        out.append(TEPVariable(VariableKind.XMEAS, i, f"Product Analysis {comp}", "mol%",
                               f"Component {comp} in product (stream 11)", 11,
                               MeasurementType.SAMPLED, 0.25, 0.25, _XMEAS_BASE[i - 1], _XNS[i - 1]))
    return out


# XMV(1..12) in the corrected order, teprob.f lines 96-107. Base values are the
# initial valve positions YY(39..50) set by TEINIT.
_XMV = [
    ("D Feed Flow", 2, 63.05263039),
    ("E Feed Flow", 3, 53.97970677),
    ("A Feed Flow", 1, 24.64355755),
    ("A and C Feed Flow", 4, 61.30192144),
    ("Compressor Recycle Valve", None, 22.21000000),
    ("Purge Valve", 9, 40.06374673),
    ("Separator Pot Liquid Flow", 10, 38.10034370),
    ("Stripper Liquid Product Flow", 11, 46.53415582),
    ("Stripper Steam Valve", None, 47.44573456),
    ("Reactor Cooling Water Flow", None, 41.10581288),
    ("Condenser Cooling Water Flow", None, 18.11349055),
    ("Agitator Speed", None, 50.00000000),
]


def _build_xmv() -> List[TEPVariable]:
    return [TEPVariable(VariableKind.XMV, i, name, "%", f"{name} (valve/drive position)", stream,
                        base_value=base)
            for i, (name, stream, base) in enumerate(_XMV, start=1)]


# IDV(1..20), teprob.f lines 172-191.
_IDV = [
    ("A/C Feed Ratio, B Composition Constant (Stream 4)", "Step"),
    ("B Composition, A/C Ratio Constant (Stream 4)", "Step"),
    ("D Feed Temperature (Stream 2)", "Step"),
    ("Reactor Cooling Water Inlet Temperature", "Step"),
    ("Condenser Cooling Water Inlet Temperature", "Step"),
    ("A Feed Loss (Stream 1)", "Step"),
    ("C Header Pressure Loss - Reduced Availability (Stream 4)", "Step"),
    ("A, B, C Feed Composition (Stream 4)", "Random Variation"),
    ("D Feed Temperature (Stream 2)", "Random Variation"),
    ("C Feed Temperature (Stream 4)", "Random Variation"),
    ("Reactor Cooling Water Inlet Temperature", "Random Variation"),
    ("Condenser Cooling Water Inlet Temperature", "Random Variation"),
    ("Reaction Kinetics", "Slow Drift"),
    ("Reactor Cooling Water Valve", "Sticking"),
    ("Condenser Cooling Water Valve", "Sticking"),
    ("Unknown", "Unknown"),
    ("Unknown", "Unknown"),
    ("Unknown", "Unknown"),
    ("Unknown", "Unknown"),
    ("Unknown", "Unknown"),
]


def _build_idv() -> List[TEPVariable]:
    return [TEPVariable(VariableKind.IDV, i, name, "on/off", f"{name} [{kind}]")
            for i, (name, kind) in enumerate(_IDV, start=1)]


IDV_TYPES: Dict[int, str] = {i: kind for i, (_, kind) in enumerate(_IDV, start=1)}


# 50 internal states YY(1..50), derived from the TEFUNC state unpacking
# (teprob.f lines 417-439). Diagnostic only - not operational data.
def _build_states() -> List[TEPVariable]:
    out: List[TEPVariable] = []
    for k, comp in enumerate(_COMPONENTS):
        phase = "vapor" if k < 3 else "liquid"
        out.append(TEPVariable(VariableKind.STATE, k + 1, f"Reactor holdup {comp} ({phase})", "lbmol",
                               f"UC{'V' if k < 3 else 'L'}R({k + 1})"))
    out.append(TEPVariable(VariableKind.STATE, 9, "Reactor internal energy", "energy units", "ETR"))
    for k, comp in enumerate(_COMPONENTS):
        phase = "vapor" if k < 3 else "liquid"
        out.append(TEPVariable(VariableKind.STATE, 10 + k, f"Separator holdup {comp} ({phase})", "lbmol",
                               f"UC{'V' if k < 3 else 'L'}S({k + 1})"))
    out.append(TEPVariable(VariableKind.STATE, 18, "Separator internal energy", "energy units", "ETS"))
    for k, comp in enumerate(_COMPONENTS):
        out.append(TEPVariable(VariableKind.STATE, 19 + k, f"Stripper holdup {comp}", "lbmol", f"UCLC({k + 1})"))
    out.append(TEPVariable(VariableKind.STATE, 27, "Stripper internal energy", "energy units", "ETC"))
    for k, comp in enumerate(_COMPONENTS):
        out.append(TEPVariable(VariableKind.STATE, 28 + k, f"Compressor/header vapor holdup {comp}", "lbmol",
                               f"UCVV({k + 1})"))
    out.append(TEPVariable(VariableKind.STATE, 36, "Compressor/header internal energy", "energy units", "ETV"))
    out.append(TEPVariable(VariableKind.STATE, 37, "Reactor cooling water outlet temperature", "degC", "TWR"))
    out.append(TEPVariable(VariableKind.STATE, 38, "Condenser cooling water outlet temperature", "degC", "TWS"))
    for k in range(12):
        out.append(TEPVariable(VariableKind.STATE, 39 + k, f"Actual valve position {k + 1}", "%",
                               f"VPOS({k + 1}) - first-order valve dynamics toward XMV({k + 1})"))
    return out


XMEAS: List[TEPVariable] = _build_xmeas()
XMV: List[TEPVariable] = _build_xmv()
IDV: List[TEPVariable] = _build_idv()
STATES: List[TEPVariable] = _build_states()

NUM_XMEAS = 41
NUM_XMV = 12
NUM_IDV = 20
NUM_STATES = 50

assert len(XMEAS) == NUM_XMEAS and len(XMV) == NUM_XMV
assert len(IDV) == NUM_IDV and len(STATES) == NUM_STATES

ALL_VARIABLES: Dict[str, TEPVariable] = {v.id: v for v in XMEAS + XMV + IDV + STATES}


def variable(var_id: str) -> TEPVariable:
    """Look up a variable by canonical id, e.g. ``"XMEAS(9)"``."""
    try:
        return ALL_VARIABLES[normalize_id(var_id)]
    except KeyError as exc:
        raise KeyError(f"Unknown TEP variable: {var_id}") from exc


def normalize_id(var_id: str) -> str:
    """Accept ``XMEAS(9)``, ``xmeas9``, ``XMEAS_9`` or ``XMEAS09``."""
    s = var_id.strip().upper().replace(" ", "")
    for kind in ("XMEAS", "XMV", "IDV", "STATE"):
        if s.startswith(kind):
            rest = s[len(kind):].strip("()_")
            if rest.isdigit():
                return f"{kind}({int(rest)})"
    return s


# ---------------------------------------------------------------------------
# Unit conversion constants taken from the TEFUNC measurement equations.
# ---------------------------------------------------------------------------
#   XMEAS(1) = FTM(3)*0.359/35.3145       -> kscmh from lbmol/h
#   XMEAS(2) = FTM(1)*XMWS(1)*0.454       -> kg/h from lbmol/h and lb/lbmol
LB_TO_KG = 0.454
KSCMH_TO_LBMOL_PER_H = 35.3145 / 0.359

# Component molecular weights XMW(1..8), TEINIT lines 941-948.
COMPONENT_MW = {"A": 2.0, "B": 25.4, "C": 28.0, "D": 32.0, "E": 46.0, "F": 48.0, "G": 62.0, "H": 76.0}
COMPONENTS = list(_COMPONENTS)

# Shutdown limits hard-coded in TEFUNC (teprob.f lines 702-710).
SHUTDOWN_LIMITS = {
    "reactor_pressure_high_kPa": 3000.0,
    "reactor_temperature_high_degC": 175.0,
    "reactor_liquid_volume_high_m3": 24.0,
    "reactor_liquid_volume_low_m3": 2.0,
    "separator_liquid_volume_high_m3": 12.0,
    "separator_liquid_volume_low_m3": 1.0,
    "stripper_liquid_volume_high_m3": 8.0,
    "stripper_liquid_volume_low_m3": 1.0,
}


def level_pct_from_volume(vessel: str, volume_m3: float) -> float:
    """Convert a vessel liquid volume to the XMEAS level % using TEFUNC equations.

    XMEAS(8)  = (VLR-84.6)/666.7*100    VLR in ft3
    XMEAS(12) = (VLS-27.5)/290.0*100
    XMEAS(15) = (VLC-78.25)/VTC*100      VTC = 156.5
    """
    ft3 = volume_m3 * 35.3145
    if vessel == "reactor":
        return (ft3 - 84.6) / 666.7 * 100.0
    if vessel == "separator":
        return (ft3 - 27.5) / 290.0 * 100.0
    if vessel == "stripper":
        return (ft3 - 78.25) / 156.5 * 100.0
    raise ValueError(vessel)


def shutdown_limits_in_measurement_units() -> Dict[str, Dict[str, float]]:
    """Shutdown interlock limits expressed against the XMEAS they correspond to."""
    L = SHUTDOWN_LIMITS
    return {
        "XMEAS(7)": {"high": L["reactor_pressure_high_kPa"]},
        "XMEAS(9)": {"high": L["reactor_temperature_high_degC"]},
        "XMEAS(8)": {"high": level_pct_from_volume("reactor", L["reactor_liquid_volume_high_m3"]),
                     "low": level_pct_from_volume("reactor", L["reactor_liquid_volume_low_m3"])},
        "XMEAS(12)": {"high": level_pct_from_volume("separator", L["separator_liquid_volume_high_m3"]),
                      "low": level_pct_from_volume("separator", L["separator_liquid_volume_low_m3"])},
        "XMEAS(15)": {"high": level_pct_from_volume("stripper", L["stripper_liquid_volume_high_m3"]),
                      "low": level_pct_from_volume("stripper", L["stripper_liquid_volume_low_m3"])},
    }
