# Test catalog

Status at the time of writing: **104 tests, 104 passed**, about 80 s on the development machine
(`python -m pytest`, Fortran backend). Tests marked "Fortran" are skipped when the library is not built.
The count includes parametrisations.

## tests/test_tep_adapter.py (15): TEP adapter

| Test | Proves | Does NOT prove |
|---|---|---|
| `test_initial_state_matches_downs_vogel_base_case` [fortran, python] | after TEINIT, all 41 XMEAS are within 0.1 % (or 0.001 abs) of the D&V base case; state/XMV shapes; no shutdown | behaviour after t = 0 |
| `test_boundary_parameters_equal_teinit` [fortran, python] | the adapter reads the 17 boundary parameters at their TEINIT values (so the ctypes layout of `/TEPROC/` and `/WLK/` is correct at those offsets) | the layout of fields never read |
| `test_closed_loop_holds_base_case_for_two_hours` (Fortran) | native control holds reactor T (±0.1), P (±25), levels near base over the 2nd hour; TEP TIME = 2 h | agreement with published trajectories or datasets |
| `test_fortran_is_deterministic_and_seed_dependent` (Fortran) | same seed → bit-identical XMEAS and states after 600 s (this test found the TCR initial-guess issue); different seed → different | reproducibility across builds or platforms |
| `test_python_backend_statistically_matches_fortran` (Fortran) | means of XMEAS 7, 8, 9, 12, 15, 17, 21 over minutes 10-30 agree within 1 % | bit-equality; agreement under faults |
| `test_native_control_scheme_transcription` | 19 loops, execution order, DELTAT, cascade structure, periods | that every gain equals `temain_mod.f` (checked by construction, not asserted per value) |
| `test_setpoint_measurement_and_output_are_distinct` [fortran, python] | the loop table separates PV, SP, output; cascade SP = master output; slave SP and owned XMV are write-protected | — |
| `test_manual_mode_freezes_controller_outputs` [fortran, python] | MANUAL runs no loop; manual XMV write; return to CLOSED_LOOP gives AUTO | bumpless transfer quality |
| `test_measurement_filter_does_not_touch_true_process` (Fortran) | the swap/restore of transmitted values around CONTRLn leaves the process bit-identical with an identity filter | — |
| `test_idv_disturbance_changes_trajectory` (Fortran) | the IDV flag reaches TEFUNC (IDV(1) raises A feed after 1 h) | IDV 2-20 individually |
| `test_shutdown_detected_with_native_limits` (Fortran) | total loss of reactor CW trips on a reactor limit; states freeze after the trip | every trip condition |

## tests/test_model.py (34): mappings, ISA-95, clock, events

| Test | Proves |
|---|---|
| `test_xmeas_mapping` ×11 | selected XMEAS map to the right equipment, property, area and unit |
| `test_every_variable_is_mapped_and_is_not_equipment` | all 41/12/20 are mapped; no equipment id is a variable; units match the catalog |
| `test_sampled_measurements_are_on_analyzers` | XMEAS 23-41 sit on analyzer control modules; sample periods |
| `test_xmv_mapping` ×6 | valve CMs, process equipment, names |
| `test_loops_map_to_control_modules_under_final_element_or_pv` | loop CM placement rule for XMV loops |
| `test_idv_mapping` ×8, `test_idv_catalog_matches_teprob` | IDV equipment mapping and catalog text |
| `test_hierarchy_is_valid` / `_rejects_invalid_containment` / `_rejects_duplicates_and_bad_levels` | ISA-95 validity and that invalid configs fail |
| `test_clock_is_deterministic` | timestamps from the start time, no negative advance, speed bounds |
| `test_event_bus_fields_ids_and_causation` | event fields, sequential ids, LC- ids, cause inheritance, visibility, query filter |

Not proven: the mapping is *correct* in a process sense beyond the spot checks (it is a modelling
choice).

## tests/test_enterprise.py (15): enterprise behaviour

| Test | Proves | Does NOT prove |
|---|---|---|
| `test_fault_lifecycle_and_validation` | CREATED→SCHEDULED→ACTIVE→STOPPED→RESET with the right events; cause channel applied and removed; five invalid specs rejected | every type's validation |
| `test_tep_native_fault_only_toggles_idv` | IDV4 sets exactly one flag and leaves all boundaries unchanged; cleared at stop | — |
| `test_sensor_bias_changes_transmitted_not_true_value` | transmitted − true = bias; the equipment property uses the transmitted value; closed loop moves the true process ≥ 1.5 °C; removal restores | drift/dropout behaviour |
| `test_fault_injection_is_deterministic` | gradual + event-triggered + intermittent faults give an identical event trace and state across two runs | — |
| `test_equipment_degradation_propagates_through_coupling_to_tep` | health → efficiency → capacity fraction → VRNG(10) = 1000 × fraction → XMV(10) opens; reactor T still controlled; correlation id reaches XMEAS(9) and the utility event; maintenance restores capacity with the standby | quantitative correctness of the temperature response |
| `test_coupling_baseline_is_native_and_graph_is_acyclic` | healthy boundary = TEINIT; relation order respects dependencies; graph API | — |
| `test_power_loss_cascades_to_pumps_and_compressor` | power loss reduces process supply, MCC supply and CPFLMX | effect on the process |
| `test_failure_changeover_and_maintenance_recovery` | FAILED → capacity 0 → auto-changeover in 30-60 s → P1 WO → part issued → repair to STANDBY, health 0.98 → fault remediated; all events present | — |
| `test_spare_part_shortage_blocks_work_order` | WAITING_PARTS while blocked; SCHEDULED after stop | — |
| `test_production_order_state_machine` | PLANNED→RUNNING, illegal transition rejected, pause/resume, completion; event sequence | BLOCKED paths (see next) |
| `test_order_blocked_by_material_shortage` | 100 % stock loss → supply availability 0 → VRNG(1) = 0 → order BLOCKED; MATERIAL_SHORTAGE | process effect of losing D feed |
| `test_quality_calculation_from_tep_composition` | G_MASS_PCT equals the formula on the transmitted XMEAS; baseline PASS; lot assigned | — |
| `test_quality_failure_fault_rejects_lot` | offset → all FAIL → lot REJECTED → rejected kg, QA-OFFSPEC alarm | QUARANTINE threshold behaviour |
| `test_inventory_consumption_matches_metered_feed` | stock decrease = ∫ true E feed (to 0.01 kg over 30 min); FIFO | kscmh conversions (A, A/C) |
| `test_replenishment_orders_and_receives_material` | reorder → delivery → incoming inspection → usable | supply-disruption delay |

## tests/test_reproducibility.py (11): reproducibility, fidelity, scenarios

| Test | Proves | Does NOT prove |
|---|---|---|
| `test_same_scenario_same_seed_identical_results` | 2 h of SCN-COOL-001 twice: identical full event log (JSON), all 166 trend series, run id, config hash | 3 h (only 2 h are compared); determinism across processes, machines or builds |
| `test_different_seed_gives_different_trajectory` | seed changes the trajectory and the configuration hash | — |
| `test_healthy_enterprise_reproduces_native_tep_exactly` (Fortran) | enterprise simulator without faults = bare native adapter with the same TEP seed, XMEAS bit-identical every second for 1 h, states identical | the same with faults (by design they differ) |
| `test_reset_returns_to_initial_state` | reset snapshot equals a fresh snapshot; trend buffer cleared; only SIMULATION_RESET in the log | — |
| `test_manifest_contents` | manifest keys; Fortran source and library hashes present | — |
| `test_demo_scenario_causal_chain` | the demo's ordered chain (fault → degraded → constrained → saturation alarm → TAH-09), correlation F-COOL-001 on each, T > 130 °C, XMV(10) ≥ 99.9 %, no trip, recovery state, WO completed, health > 0.9, a FAIL sample, a non-released lot | exact times or values |
| `test_every_library_scenario_loads_and_runs` ×3 | each scenario file validates and runs 120 s | their long-horizon behaviour |
| `test_every_library_fault_can_start_and_stop` | every manual fault in SCN-FAULT-LIBRARY starts, runs 20 min together, and stops | the consequences of each fault |
| `test_python_development_backend_runs_full_engine` | the full engine runs on the Python backend for 600 s near the base case | — |

## tests/test_api_ui.py (8): API and UI

| Test | Proves | Does NOT prove |
|---|---|---|
| `test_ui_is_served` | index and the five static assets are served; key labels are present | that the UI renders or works in a browser (checked manually only) |
| `test_snapshot_reflects_engine_state` | the UI snapshot, equipment view, property API and history equal engine state | — |
| `test_required_operations_exist` | the required GET routes answer 200 | response schemas |
| `test_simulation_controls` | speed bounds, run_until, reset with a new duration | real-time pacing |
| `test_operator_actions_and_validation` | setpoint change, cascade protection (400), unknown action (400), 404, event filtering | — |
| `test_fault_injection_is_benchmark_only` | fault ops only under /api/benchmark; FAULT_* absent from /api/events; ground truth under /api/benchmark; dropout visible as BAD | the rest of the boundary (see test_operational_boundary.py) |
| `test_export` | JSON keys and CSV files; measurements header | content correctness |
| `test_scenario_save_duplicate` | save, duplicate, no silent overwrite | — |

## tests/test_contract.py (10): canonical contract vs implementation

| Test | Proves | Does NOT prove |
|---|---|---|
| `test_contract_version_describes_current_simulator` | the contract names the current simulator version | — |
| `test_event_types_and_visibility_match_contract` | every event type, visibility and id sequence is in the contract, and vice versa | payload schemas |
| `test_tep_boundary_couplings_match_contract` | the 15 enterprise → TEP couplings and the 2 uncoupled parameters match the contract | the physical accuracy of the couplings |
| `test_only_tep_boundary_relations_write_into_tep` | no relation writes a process variable; only `tep_boundary` relations write boundary parameters | Fortran writes outside relations (see the coupling contract) |
| `test_every_entity_property_has_contract_semantics` | every property produced in the demo has a semantic class | properties that appear only in other scenarios |
| `test_metadata_tags_agree_with_contract_observability` | properties are tagged model-internal exactly when the contract says so | — |
| `test_operational_boundary_rules_match_contract` | withheld event types, payload redactions and the status mapping match the contract | — |
| `test_collections_match_contract` | record collections match the contract; faults are benchmark | — |
| `test_process_variable_catalog_matches_contract` | XMEAS/XMV/IDV counts, loop count and periods | per-variable semantics |
| `test_every_api_route_is_classified` | every `/api` route is classified; only `/api/benchmark/*` routes are benchmark | — |

## tests/test_operational_boundary.py (11): operational information boundary

| Test | Proves | Does NOT prove |
|---|---|---|
| `test_operational_routes_do_not_reveal_fault_or_scenario_identity` | a crawl of every operational route at 01:15 and at the end of the demo contains no fault id or type, scenario id or name, run id, config hash, truth-bearing key, or DEGRADED/CONSTRAINED value | inference from legitimate instrument readings (by design) |
| `test_model_internal_properties_are_hidden_and_indistinguishable_from_missing` | hidden properties are absent and answer exactly like missing ones | — |
| `test_hidden_degradation_is_not_reported_as_status` | internally DEGRADED asset and utility; operationally RUNNING and no utility status; no DEGRADED events | — |
| `test_operational_event_stream_is_self_contained_and_gap_free` | contiguous `OE-` ids; correlation and causation stay inside the stream; redacted payloads; alarm → work order link kept | — |
| `test_operational_stream_matches_fault_free_run_until_the_first_symptom` | before 01:20:33 the operational stream equals that of the scenario without the fault | behaviour after the first symptom (it legitimately differs) |
| `test_manifest_and_simulation_state_carry_no_scenario_identity` | operational manifest fields; no scenario block | — |
| `test_history_and_process_image_exclude_truth` | no `TRUE:` or hidden series; no boundary, true XMEAS or IDV | — |
| `test_truth_routes_are_not_operational` | the moved truth routes no longer exist outside `/api/benchmark` | authentication (there is none) |
| `test_evaluator_routes_retain_the_true_cause` | evaluator routes carry the fault, true correlation, `operational_id`, health and boundary | — |
| `test_cooling_water_utilization_equals_valve_position` | CW utilization equals XMV(10)/100 of the previous step, which is why it is operational | — |
| `test_alarm_sources_are_operational` | no alarm is defined on a hidden property | — |

## Not covered by any test

See [documentation gaps](../11_limitations/documentation_gaps.md#code-paths-not-covered-by-tests). The
main ones:

* **Fault paths:** SZERO, XST, VRNG(9) and VRNG(2-4) couplings end to end; cooling-tower and steam
  faults; `raw_material_quality_deviation` effects; sensor drift and dropout effect on control.
* **Workflows:** inspection findings; planned maintenance; technician shifts; the QUARANTINE decision
  threshold; warehouse dispatch.
* **Runtime:** the real-time runner; the UI in a browser; the event-cursor behaviour of
  `/api/benchmark/ui/snapshot`.

Source:
- `tests/conftest.py` — `requires_fortran`, `make_engine`, `demo_run`
