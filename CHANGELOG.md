# Changelog

Notable changes to this repository. The simulator version is `simulator.__version__`; the canonical
contract version is `contract.version` in `contract/canonical_contract.yaml`
(see [contract §23](docs/CANONICAL_SIMULATOR_CONTRACT.md#23-versioning)).

## [Unreleased]: operational information boundary (contract 0.2.0)

Closes the operational ground-truth leaks found by the contract audit. **Simulator behaviour is
unchanged** (simulator version 1.0.0). The full 3 h SCN-COOL-001 run produces the same internal event
log, trends, final TEP states and run id as before. Contract version 0.1.0 → 0.2.0: breaking for
operational consumers.

### Added

- **Boundary module:** `api/operational.py`, the operational information boundary. Every route
  outside `/api/benchmark/*` is built through it:
  - model-internal properties withheld (answer 404 like missing ones);
  - asset DEGRADED reported as RUNNING;
  - operational event stream: no FAULT_*, `UTILITY_STATE_CHANGED`, `EQUIPMENT_DEGRADED` or
    RUNNING↔DEGRADED transitions; correlation rebuilt from operational causation; gap-free `OE-` ids;
    redacted payloads;
  - redacted manifest;
  - no truth in history or the process image.
- **Evaluator / console routes:** `/api/benchmark/simulation`, `/manifest`, `/entities/{id}`,
  `/utilities`, `/maintenance`, `/inventory`, `/events` (true correlation plus `operational_id`),
  `/history`, `/history/catalog`, `/process/image`.
- **Regression tests:** `tests/test_operational_boundary.py`, 11 tests. They crawl every operational
  route before and after the first symptom. They also show that the operational event stream equals a
  fault-free run until the first symptom, and that evaluator routes retain the true cause.
- **Contract test:** `tests/test_contract.py::test_operational_boundary_rules_match_contract`. The
  metadata-tag test now checks both directions.

### Changed

- **Routes moved under `/api/benchmark/`:**
  - `/api/coupling` → `/api/benchmark/coupling`;
  - `/api/process/internal-states` → `/api/benchmark/process/internal-states`;
  - `/api/scenarios*` → `/api/benchmark/scenarios*`;
  - `/api/export/*` → `/api/benchmark/export/*`;
  - `/api/ui/snapshot` → `/api/benchmark/ui/snapshot`.
- **Operational responses:**
  - `/api/simulation` no longer has a `scenario` block;
  - `/api/simulation/manifest` returns the operational manifest;
  - `/api/events` returns the operational stream with `OE-` ids.
- **Web UI (benchmark console):** reads its truth-bearing data from `/api/benchmark/*`; its behaviour is
  unchanged.
- **Metadata tags (no effect on simulation):**
  - `UtilitiesModule` tags utility status, availability, capacity fields, health, and steam
    utilization as `model_internal`;
  - `InventoryModule` tags the `*_deviation` properties `unobservable` (A1) and no longer tags
    `supply_availability`, which is a function of released stock and is the source of the
    supply-lost alarms.
- **Read model:** `EnterpriseView` takes a status function, so hierarchy roll-ups can use the
  operational status.
- **Contract, invariants and audit:**
  - contract 0.2.0: operational boundary section, route table, utility and storage observability;
  - invariants I13 and I14 (formerly target invariants T1 and T2) now hold;
  - the audit classifies G1–G16 and records decisions D1–D5 and the power-demand investigation (A4:
    a legitimate coupling, not a leak).
- **Documentation:** API, limitations, learning guide, master specification, README and the
  generated reference tables were updated for the boundary.

### Validation

- **Test suite:** `python -m pytest`, 104 passed.
- **Behaviour unchanged:** the SCN-COOL-001 fingerprint is identical (148 internal events, 166 trend
  series, final TEP states, run id `RUN-SCN-COOL-001-18472-4f2f4816dd`). The regenerated demo trace
  and entity table are byte-identical.
- **Documentation check:** `python scripts/check_docs.py`: OK.

### Known limitations

- **No authentication:** the boundary is the route namespace. A system under test must be given the
  operational routes only.
- **Decisions D1–D5 to confirm:**
  - utility meter readings are noise-free functions of capability;
  - power utilization stays operational;
  - blocked spare parts stay operational;
  - `POST /api/simulation/create` accepts inline faults;
  - the UI is the benchmark console.
- **Open ambiguities:** A2, A5–A8 and A10–A12 remain; none exposes ground truth operationally.

## [contract 0.1.0]: canonical simulator contract (commit `2c75eaa`, merged in `d756482`)

Documentation and contract release. **Simulator behaviour is unchanged** (simulator version 1.0.0).
Introduces contract version 0.1.0 (draft).

### Added

- **Contract:** `docs/CANONICAL_SIMULATOR_CONTRACT.md`, the canonical simulator contract, covering:
  - the entity model and the semantic classes of state;
  - measurements, setpoints and manipulated variables;
  - equipment, utility, production, quality and maintenance state;
  - events and relationships;
  - the TEP boundary (all 15 couplings);
  - scenarios, ground truth, time, determinism and versioning.
- **Invariants:** `docs/CANONICAL_SIMULATOR_INVARIANTS.md`, 12 invariants that hold and are tested,
  plus 2 target invariants (no ground-truth exposure; no model-internal exposure) that are recorded
  as **violated today**.
- **Audit:** `docs/CANONICAL_CONTRACT_AUDIT.md`, covering coverage, ambiguities A1–A12, newly found
  ground-truth exposures G11 and G12, and the decisions required from the project owner.
- **Machine-readable contract:** `contract/canonical_contract.yaml`, documentation metadata only
  (not read by the simulator). It records:
  - semantic classes;
  - entity kinds and the semantics of every entity property;
  - record collections;
  - process-variable catalog references;
  - TEP boundary couplings;
  - event types with visibility;
  - the ground-truth classification of every API route.
- **Contract tests:** `tests/test_contract.py`, 9 tests that fail when the implementation and the
  contract drift apart (event types and visibility, boundary couplings, "only `tep_boundary`
  relations write into TEP", property coverage, metadata tags, collections, process-variable
  catalog, route classification, versions).
- `CHANGELOG.md` (this file).

### Changed

- **Documentation hierarchy:** README → master specification → canonical contract → subsystem pages
  → code. The README has a new "The Canonical Simulator Contract" section. `docs/README.md`, the
  master specification, the learning guide and the state, event, coupling, graph and architecture
  pages now refer to the contract for exact semantics instead of redefining it.
- **Developer guides:** adding equipment, utilities, couplings or events now includes the contract
  update step; the testing guide lists `tests/test_contract.py`.
- **Ground-truth exposures:** `docs/11_limitations/benchmark_limitations.md` lists two more:
  - G11: `/api/simulation/manifest` includes fault records;
  - G12: the scenario description in `/api/simulation` and `/api/ui/snapshot` names the fault.

  References to "G1–G10" now read "G1–G12".
- **Corrections in `docs/README.md` and `README.md`:** the learning guide has 17 levels, not ten, and
  17 boundary parameters are defined, of which 15 are driven.
- **Tooling:** `scripts/check_docs.py` also verifies references to `contract/` paths.
- **`.gitignore`:** now ignores virtual environments, `.env`, logs, and non-Windows builds of the TEP
  library. The Windows DLL stays tracked on purpose.

### Validation

- **Test suite:** `python -m pytest`, 92 passed (83 existing + 9 contract tests).
- **Behaviour unchanged:** the full 3 h SCN-COOL-001 run has the same event log (148 events), the same
  166 trend series, the same final TEP states and the same run id
  (`RUN-SCN-COOL-001-18472-4f2f4816dd`) as the previous commit.
- **Start-up:** `python run.py` starts, loads SCN-COOL-001 in state READY on the Fortran backend, and
  serves the UI.
- **Demo:** `python scripts/run_demo.py` completes the 3 h run with the documented chain: no
  shutdown, QS-00009 fails `G_MASS_PCT`, PL-0003 quarantined, repair complete at 02:51:13.
- **Documentation check:** `python scripts/check_docs.py` reports all links, anchors, paths, symbols
  and test references resolving.
- **Contract validation:** 9 of 9 contract tests pass. The property-coverage test was negatively
  tested: removing one entry made it fail.

### Known limitations

- **Ground-truth exposure:** ground truth is still exposed on operational API routes (G1–G12). The
  current API is a benchmark-operator and development interface, not a system-under-test interface.
- **Unresolved ambiguities A1–A12:**
  - utility status depends on model-internal availability;
  - the power-demand relation feeds TEP-relevant supply although it is labelled an observation;
  - property names are reused with different meanings;
  - there are placeholder and null fields;
  - correlation labelling is heuristic.
- **Draft status:** the contract is a draft (0.x) until those decisions are made. Event payloads have
  no per-type schema.
- **Reproducibility scope:** reproducibility holds per compiled TEP library; only Windows with
  Python 3.11 has been tested; the Dockerfile has not been built.
- **Not implemented:** UNS, historian, knowledge graph, i3X, MCP and agent layers.

## [1.0.0]: 2026-09-25, initial publication (commit `900cf00`)

### Added

- **Simulator:** the enterprise manufacturing simulator around the unmodified Tennessee Eastman
  Process Fortran model:
  - ISA-95 hierarchy;
  - equipment condition and maintenance;
  - utilities;
  - materials, inventory and warehouse;
  - production orders and scheduling;
  - quality;
  - alarms;
  - operator actions;
  - a benchmark-only fault engine;
  - declarative enterprise → TEP coupling;
  - REST API and web UI;
  - scenarios and exports.
- **Library:** compiled Windows TEP library (`simulator/tep/fortran/lib/tep_fortran.dll`).
- **Documentation:**
  - the numbered documentation tree under `docs/` and the master simulator specification;
  - the learning guide and the documentation audit;
  - generated reference tables and the variable graph.
- **Tests:** 83 tests (TEP equivalence, determinism, coupling, faults, scenarios, API).
