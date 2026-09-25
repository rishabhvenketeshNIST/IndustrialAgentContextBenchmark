# Adding an event type

1. Add a member to `EventType` in `simulator/events/__init__.py`. If it is benchmark ground truth, add
   it to `BENCHMARK_EVENT_TYPES` so that `/api/events` excludes it. If it depends on wall-clock user
   interaction, add it to `LIFECYCLE_EVENT_TYPES` (separate `LC-` ids).
2. Publish it from the owning module:

   ```python
   ref = self.ctx.state.causal.get(entity_id)          # carry ground-truth correlation, if any
   kw = {"correlation_id": ref.correlation_id, "causation_id": ref.event_id} if ref else {}
   self.ctx.bus.publish(EventType.MY_EVENT, self.name, entity_id, {"key": value}, severity="warning", **kw)
   ```

   When reacting to another event, pass `cause=that_event` instead.
3. Subscribe elsewhere with `ctx.bus.subscribe(handler, [EventType.MY_EVENT])` in `setup`. Handlers run
   synchronously; keep them deterministic (sorted iteration, no wall clock).
4. Payloads must be JSON-serialisable (see `to_jsonable`).
5. Regenerate [event_catalog.md](../05_state_and_events/event_catalog.md) with
   `scripts/generate_docs_tables.py`.
6. Add the type to `events.types` in `contract/canonical_contract.yaml` with its visibility and id
   sequence (a MINOR contract change; `tests/test_contract.py` fails otherwise).

Source:
- `simulator/events/__init__.py` — `EventType`, `EventBus.publish`, `EventBus.subscribe`
- `simulator/common/__init__.py` — `to_jsonable`
