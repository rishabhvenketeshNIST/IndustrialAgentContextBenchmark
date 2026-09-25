# Changelog

Notable changes to this repository. The simulator version is `simulator.__version__`; the canonical
contract version is `contract.version` in `contract/canonical_contract.yaml`
(see [contract §23](docs/CANONICAL_SIMULATOR_CONTRACT.md#23-versioning)).

## [Unreleased]: canonical simulator contract

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
