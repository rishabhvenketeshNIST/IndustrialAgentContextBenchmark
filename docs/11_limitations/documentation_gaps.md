# Documentation gaps

Things that could not be fully determined, are ambiguous, or are unverified. Each needs a decision or
confirmation.

## Unclear or unverified behaviour

| # | Item | Status | How to resolve |
|---|---|---|---|
| U1 | Minimum Python version (README says ≥ 3.10) | **unverified**; developed on 3.11.9 only | run the tests on 3.10 and 3.12 |
| U2 | Dockerfile (Linux build and run) | **never built or run** | build it and run the tests in the container |
| U3 | Linux and macOS Fortran builds | **never built** (only the Windows DLL via Docker cross-compile) | build and run the tests |
| U4 | Bit-reproducibility across machines with the same DLL | **unverified** (single machine) | run on a second machine and compare traces |
| U5 | numpy PCG64 stream stability across numpy versions | unverified; the numpy version is not recorded | record it in the manifest; test |
| U6 | Real-time runner pacing accuracy at each speed | **not measured** | measure |
| U7 | UI behaviour in browsers other than Chrome | **unverified** | manual check |
| U8 | Long-run behaviour (> 3 h, up to 30 days allowed) | not exercised; memory: the event log caps at 250,000 events and the trend buffer at 20,000 samples | run 24 h and 7 d scenarios |
| U9 | Behaviour of `raw_material_quality_deviation` and `stream4` SZERO couplings on the process | implemented; **effect not observed** | run and document |
| U10 | Steam, power and cooling-tower fault effects on product quality | not observed | run and document |
| U11 | Concurrent API writes during real-time running (e.g. an operator action between runner batches) | serialised by the lock; the landing simulation time depends on wall timing | documented as a limitation |

## Ambiguous semantics

| # | Item | Detail |
|---|---|---|
| A1 | `efficiency` on TX-401, MCC-401 and the agitator | a placeholder 1.0, never updated; looks like a real value |
| A2 | Utility `availability` vs `capacity_fraction` | CW and steam use `capacity_fraction`; power uses available/capacity |
| A3 | "Causal relation" chip in the variable-graph viewer | includes derivations and observations ([edge types](../07_variable_graph/edge_types.md)) |
| A4 | Maintenance cancellation | published as MAINTENANCE_WAITING with status CANCELLED |
| A5 | Production "rejected_kg" after an order completed | reduces net quantity but the order stays COMPLETED |
| A6 | Fault status after remediation | a fault already STOPPED by duration is marked `remediated`, but keeps stop reason "duration elapsed" |
| A8 | Event target types | usually entity ids; for orders, lots and samples the target is a record id |

## Undocumented or unused configuration

`suppliers`, scenario `tags`, `products.*.name/material`, `services.*.serves` are descriptive metadata
only. See [configuration](../10_operation/configuration.md).

## Inconsistent naming

* `simulator/scheduling` owns order release, but orders live in `simulator/production/orders.py`.
* `WarehouseModule` handles the product tanks in `AREA-PRODUCT`; `SZ-WAREHOUSE` (MRO, FG dispatch)
  is used only for spare-part location strings and dispatch events.
* Asset ids use ISA-95 ids (`WU-CWP-101A`) while names use plant tags ("Reactor CW Pump P-101A").
* `CM-FIC-PRD` (product flow loop) sits under the condenser because its valve is the condenser CW
  valve. This is correct per the native scheme, but surprising.

## Code paths not covered by tests

* **Coupling paths:** SZERO(1, 2, 5, 6), XST impurity, VRNG(2, 3, 4, 9) couplings end to end; negative
  tests for coupling validation (cycles, double writers).
* **Sensor faults:** sensor drift and dropout effects on control; the `stuck` overlay (unreachable).
* **Maintenance:** inspection work orders and findings; planned maintenance; technician shift changes;
  `WAITING_TECHNICIAN`; cancellation.
* **Production and warehouse:** the QUARANTINE disposition threshold; material-lot REJECTED at
  incoming inspection; warehouse dispatch and DELAYED shipments.
* **Faults:** `supply_disruption` delaying POs; the `production_order_delay` fault; alarm
  acknowledgement states (`RTN_UNACK`).
* **Runtime:** the real-time runner (the thread is disabled in tests); the UI in a browser; the
  `/api/ui/snapshot` event cursor; exports' content values.

## Assumptions that need confirmation (by the project owner)

1. The quality limits (47.5-52.5 G mass %, etc.) are acceptable benchmark defaults.
2. The demo's severity (0.62) near the trip threshold is intended.
3. Ground-truth leakage on operational routes (G1–G12) is acceptable until an observable view exists.
4. The cooling-tower planned maintenance in SCN-FAULT-LIBRARY is meant to disturb the process.

## Minor implementation observations found while documenting

* `/api/ui/snapshot?after_event=` uses the id of the last returned event as a cursor. When that last
  event is a lifecycle `LC-` event, the cursor becomes an `LC-` number, and older `EV-` events can be
  re-sent. The effect is limited to the UI; there is no data impact.
* Private copies of the Fortran library (temp directories) are never deleted.

Source:
- `docs/DOCUMENTATION_AUDIT.md`
