# Process units

The five TEP unit operations and how each appears in the simulator. Physics is entirely in TEFUNC; the
right-hand columns are the enterprise-layer representation.

| Unit | TEFUNC representation (teprob.f) | Measurements | MVs | ISA-95 element | Enterprise coupling |
|---|---|---|---|---|---|
| **Reactor** | vapour/liquid holdups YY(1-8), energy YY(9); four reactions with Arrhenius rates `RR(1..4)`; heat removal `QUR = UAR·(TWR−TCR)` | XMEAS 6-9 | — | `EM-REACTOR` | — |
| **Reactor cooling bundle** | CW outlet temperature TWR = YY(37); `FWR = VPOS(10)·VRNG(10)/100`; inlet TCWR from random walk 5 (+ IDV 4, 11) | XMEAS 21 | XMV 10 | `EM-RX-COOLING` | VRNG(10) ← reactor CW capacity; SZERO(5) ← CW supply temperature |
| **Agitator** | `AGSP = (VPOS(12)+150)/100` scales UAR | — | XMV 12 | `EM-AGITATOR` | none |
| **Condenser** | separator-side heat removal `QUS`; CW outlet TWS = YY(38); `FWS = VPOS(11)·VRNG(11)/100`; inlet TCWS from walk 6 (+ IDV 5, 12) | XMEAS 22 | XMV 11 | `EM-CONDENSER` | VRNG(11) ← condenser CW capacity; SZERO(6) |
| **Separator** | holdups YY(10-17), energy YY(18); liquid FTM(11) via XMV 7 | XMEAS 11-14 | XMV 7 | `EM-SEPARATOR` | — |
| **Compressor** | recycle flow from compressor curve `FLMS = CPFLMX + FLCOEF·(1−PR³) − VPOS(5)·…`; work CPDH | XMEAS 5, 20 | XMV 5 | `EM-COMPRESSOR` | CPFLMX ← compressor capability |
| **Purge** | purge flow FTM(10) through XMV 6 | XMEAS 10, 29-36 | XMV 6 | `EM-PURGE`, `CM-AT-PURGE` | — |
| **Stripper** | holdups YY(19-26), energy YY(27); separation factors SFR; steam heat `QUC = UAC·(100−TCC)`, `UAC = VPOS(9)·VRNG(9)·…` | XMEAS 15-19, 37-41 | XMV 8, 9 | `EM-STRIPPER`, `EM-STRIPPER-STEAM`, `CM-AT-PRODUCT` | VRNG(9) ← steam supply |
| **Feeds** | streams 1-4: `FTM(k) = VPOS·VRNG/100`; compositions XST(:,1..4) | XMEAS 1-4, 23-28 | XMV 1-4 | `EM-FEED-*` | VRNG(1-4) ← storage supply; XST / SZERO(1,2) ← lot composition |

Two things to notice:

1. **The enterprise layer only ever touches the "edges" of each unit:** supply capacity, supply
   temperature and feed composition. It never touches holdups, reactions or heat-transfer
   coefficients.
2. **Reactor temperature (XMEAS 9) has no direct enterprise input.** Cooling affects it only through
   TWR and QUR inside TEFUNC.

Source:
- `simulator/tep/fortran/src/teprob.f`
- `configs/tep_mapping.yaml`
- `simulator/tep/boundary.py`
