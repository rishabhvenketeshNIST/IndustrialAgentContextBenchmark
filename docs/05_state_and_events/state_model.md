# State model

How state is structured and how it changes. The per-variable reference is
[variable_reference.md](variable_reference.md), generated from an instrumented run.

## Kinds of variables

| Kind | Meaning | Examples |
|---|---|---|
| **measured** | a TEP measurement or an observable indicator | XMEAS copies on equipment, `vibration`, loop `process_value` |
| **commanded** | set by a controller or operator | XMV, `setpoint`, `mode`, `desired_state` |
| **derived** | computed each step from other variables | coupling outputs, utility `status` and `availability`, storage `level_pct` |
| **simulated** | evolves by module logic over time | `health`, `run_hours`, stock, lots, orders, work orders |
| **constant** | configuration | `rated_*`, `nominal_*`, `capacity` |

## Kinds of truth

| Label | Meaning | Examples |
|---|---|---|
| process truth | what TEP computed | `xmeas_true`, XMV, SETPT |
| simulation truth (model-internal) | a real property of the simulated plant that no plant instrument measures | `health`, `efficiency`, `available_flow`, lot composition deviations, boundary values |
| observation | what instruments report | transmitted XMEAS, `vibration`, measurement quality |
| enterprise interpretation | a judgement computed from observations | utility `status`, equipment `status`, alarm state, quality PASS/FAIL, lot disposition |
| benchmark ground truth | causes | faults, cause channels, causal registry, correlation ids |

## When state changes

Everything changes on the 1-second step ([architecture: step lifecycle](../01_overview/architecture.md#3-simulation-step-lifecycle)).
Changes happen at three kinds of moment:

* **Continuously (every step):** process image, coupling outputs, health (wear), inventory quantities,
  utility status, alarm evaluation, production rate.
* **At scheduled times:** fault triggers, operator scripts, planned maintenance, deliveries, dispatch,
  lot closing, QC decisions, work-order starts and completions.
* **On events (synchronous handlers):** maintenance start/complete → equipment; lot state → warehouse
  and production; order release/close → inventory reservations; alarm/failure → work order; repair →
  fault remediation.

Handlers run inside the publishing call, so their effects are visible to the next module in the same
step.

Per-domain pages:
[equipment](equipment_state.md) · [utilities](utility_state.md) · [production](production_state.md) ·
[quality](quality_state.md) · [maintenance](maintenance_state.md).

Source:
- `simulator/state/__init__.py` — `CanonicalState`
- `simulator/events/__init__.py` — `EventBus.publish`
