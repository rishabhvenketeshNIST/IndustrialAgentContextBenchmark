# Causal semantics

## What an edge means, and does not mean

`A → B` means "the implementation contains a mechanism by which A can change B". It says nothing about:

* **sign or magnitude**, e.g. efficiency loss lowers capacity, and a higher supply temperature raises the
  valve opening;
* **activity**: many edges are gated (`× is_running`), saturate (`clip(..., 0, 1)`), or apply only in
  one regime (design margins mean small pump wear has **no** effect on VRNG(10));
* **delay**: immediate for relations; next walk knot for SZERO; analyzer dead time for composition;
  response times for events.

## Physical vs non-physical

| Truly physical (within the model) | Modelled mechanism but not physics | Pure bookkeeping or interpretation |
|---|---|---|
| process_response (TEP), capability_constraint, supply_dependency, tep_boundary_interface | control_* (control law), event_trigger, state_transition (organisational processes) | derivation (observations, metering, quality tests) |

## Runtime causal labels (not graph edges)

At runtime the **causal registry** labels entities with the event that explains their abnormal state.
Labels propagate along the *executed* coupling relations (`CouplingEngine._propagate_cause`):

1. A fault start registers its `cause_keys`, e.g. the pump id, or `XMEAS(n)` plus the equipment for a
   sensor fault.
2. After each relation evaluation: if the output deviates from its baseline by more than 0.5 % and any
   input key has a label, the output entity gets that label, unless it already has one. The first label
   wins.
3. When the output returns within tolerance, the label set by that relation is removed.
4. Boundary outputs pass the label to all measurements, manipulated variables and equipment listed for
   them under `process_influences`. This is **hand-curated**; TEP itself is opaque to labelling.
5. `utility_observation` relations never propagate labels.
6. Stopping a non-persistent fault, resetting a fault or remediating it clears all labels with its
   correlation id.

Publishers stamp the label onto events as `correlation_id` and `causation_id`
([event model](../05_state_and_events/event_model.md)).

## Limits of the labels

* **Overlapping faults:** only the first registered cause is kept per entity. A second fault affecting
  the same entity is not reflected.
* **Absorbed faults:** a deviation below 0.5 % labels nothing downstream, even if it has an effect.
* **TEP-internal propagation** is approximated by the curated list. A consequence TEP produces
  elsewhere (e.g. a separator level alarm during a reactor cooling fault) may carry **no** label, or,
  if an unrelated earlier label exists, the wrong one.
* **Labels are ground truth by construction.** They are derived from the benchmark's knowledge of the
  cause, not from anything observable.

Source:
- `simulator/coupling/__init__.py` — `CouplingEngine._propagate_cause`, `CouplingEngine._deviates`
- `simulator/state/__init__.py` — `CausalRegistry`
- `simulator/faults/engine.py` — `FaultEngine.start_fault`
