"""TEP process-boundary parameters.

The TEP model has no notion of utilities, equipment health or raw-material
lots. It does, however, contain physical *boundary conditions* that the
surrounding plant determines: hydraulic capacity of supply lines (valve ranges
VRNG), the recycle compressor capacity limit (CPFLMX), the mean of the supply
temperature / composition random walks (SZERO), and fixed feed-stream
compositions (XST(:,1..3)). Changing these values changes *inputs* of the
authoritative equations in TEFUNC; it does not change the equations and does
not overwrite any measurement.

Every parameter declared here is read by TEFUNC on every call, or - for the
random-walk means - at the next walk knot, so the effect emerges through the
native dynamics. See docs/04_coupling/boundary_variables.md for the justification of each binding.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class BoundaryParameter:
    name: str
    fortran_symbol: str        # human-readable location in teprob.f
    block: str                 # common block
    locator: Tuple             # backend-neutral locator, see adapters
    nominal: float             # value assigned by TEINIT
    unit: str
    description: str
    teprob_usage: str          # where TEFUNC consumes it
    min_value: float = 0.0
    max_value: float = float("inf")
    kind: str = "scalar"       # "scalar" | "impurity" (writes the complement too)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "fortran_symbol": self.fortran_symbol, "block": self.block,
            "nominal": self.nominal, "unit": self.unit, "description": self.description,
            "teprob_usage": self.teprob_usage, "min_value": self.min_value,
            "max_value": None if self.max_value == float("inf") else self.max_value,
        }


def _vrng(name: str, idx: int, nominal: float, desc: str, usage: str, unit: str = "lbmol/h at 100% valve"):
    return BoundaryParameter(name, f"VRNG({idx})", "TEPROC", ("VRNG", idx), nominal, unit, desc, usage)


def _szero(name: str, idx: int, nominal: float, unit: str, desc: str, usage: str, lo: float, hi: float):
    return BoundaryParameter(name, f"SZERO({idx})", "WLK", ("SZERO", idx), nominal, unit, desc, usage, lo, hi)


BOUNDARY_PARAMETERS: Dict[str, BoundaryParameter] = {p.name: p for p in [
    _vrng("d_feed_max_flow", 1, 400.0, "Hydraulic capacity of the D feed line (stream 2) at full valve opening",
          "FTM(1)=VPOS(1)*VRNG(1)/100"),
    _vrng("e_feed_max_flow", 2, 400.0, "Hydraulic capacity of the E feed line (stream 3) at full valve opening",
          "FTM(2)=VPOS(2)*VRNG(2)/100"),
    _vrng("a_feed_max_flow", 3, 100.0, "Hydraulic capacity of the A feed line (stream 1) at full valve opening",
          "FTM(3)=VPOS(3)*(1-IDV(6))*VRNG(3)/100"),
    _vrng("ac_feed_max_flow", 4, 1500.0, "Hydraulic capacity of the A and C feed line (stream 4)",
          "FTM(4)=VPOS(4)*(1-IDV(7)*0.2)*VRNG(4)/100"),
    _vrng("stripper_steam_valve_range", 9, 0.03,
          "Stripper steam heating capacity at full steam valve opening",
          "UAC=VPOS(9)*VRNG(9)*(1+TESUB8(9,TIME))/100; QUC=UAC*(100-TCC)", unit="heat-transfer units"),
    _vrng("reactor_cw_max_flow", 10, 1000.0, "Reactor cooling water flow at full valve opening",
          "FWR=VPOS(10)*VRNG(10)/100 -> YP(37) cooling water energy balance"),
    _vrng("condenser_cw_max_flow", 11, 1200.0, "Condenser cooling water flow at full valve opening",
          "FWS=VPOS(11)*VRNG(11)/100 -> YP(38) cooling water energy balance"),
    BoundaryParameter("compressor_max_flow", "CPFLMX", "TEPROC", ("CPFLMX",), 280275.0, "lb/h",
                      "Recycle compressor maximum mass flow (motor/drive capability)",
                      "FLMS=CPFLMX+FLCOEF*(1-PR**3), FLCOEF=CPFLMX/1.197"),
    _szero("stream4_a_fraction_mean", 1, 0.485, "mol fraction",
           "Mean A mole fraction of the A and C feed (stream 4) supplied by the header",
           "XST(1,4)=TESUB8(1,TIME)-IDV(1)*0.03-IDV(2)*2.43719E-3", 0.30, 0.70),
    _szero("stream4_b_fraction_mean", 2, 0.005, "mol fraction",
           "Mean B (inert) mole fraction of the A and C feed (stream 4)",
           "XST(2,4)=TESUB8(2,TIME)+IDV(2)*0.005", 0.0, 0.10),
    _szero("d_feed_temperature_mean", 3, 45.0, "degC", "Mean D feed (stream 2) supply temperature",
           "TST(1)=TESUB8(3,TIME)+IDV(3)*5", 0.0, 120.0),
    _szero("stream4_temperature_mean", 4, 45.0, "degC", "Mean A and C feed (stream 4) supply temperature",
           "TST(4)=TESUB8(4,TIME)", 0.0, 120.0),
    _szero("reactor_cw_inlet_temperature_mean", 5, 35.0, "degC",
           "Mean reactor cooling water supply (inlet) temperature",
           "TCWR=TESUB8(5,TIME)+IDV(4)*5", 5.0, 80.0),
    _szero("condenser_cw_inlet_temperature_mean", 6, 40.0, "degC",
           "Mean condenser cooling water supply (inlet) temperature",
           "TCWS=TESUB8(6,TIME)+IDV(5)*5", 5.0, 80.0),
    BoundaryParameter("d_feed_b_impurity", "XST(2,1)", "TEPROC", ("XST_IMPURITY", 2, 1, 4), 0.0001,
                      "mol fraction", "B impurity in the D feed (stream 2); D fraction is 1 - impurity",
                      "FCM(I,1)=XST(I,1)*FTM(1)", 0.0, 0.2, "impurity"),
    BoundaryParameter("e_feed_f_impurity", "XST(6,2)", "TEPROC", ("XST_IMPURITY", 6, 2, 5), 0.0001,
                      "mol fraction", "F impurity in the E feed (stream 3); E fraction is 1 - impurity",
                      "FCM(I,2)=XST(I,2)*FTM(2)", 0.0, 0.2, "impurity"),
    BoundaryParameter("a_feed_b_impurity", "XST(2,3)", "TEPROC", ("XST_IMPURITY", 2, 3, 1), 0.0001,
                      "mol fraction", "B impurity in the A feed (stream 1); A fraction is 1 - impurity",
                      "FCM(I,3)=XST(I,3)*FTM(3)", 0.0, 0.2, "impurity"),
]}


def get_parameter(name: str) -> BoundaryParameter:
    try:
        return BOUNDARY_PARAMETERS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown TEP boundary parameter '{name}'. "
                       f"Known: {sorted(BOUNDARY_PARAMETERS)}") from exc
