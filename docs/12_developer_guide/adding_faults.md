# Adding a fault type

A fault type writes **causes only**. Decide first where the cause enters, then make sure a
consequence path exists.

## 1. Choose the channel

Use an existing channel in `simulator/faults/effects.py → COMBINE` (e.g. `damage`, `efficiency_loss`,
`capacity_loss`, `value`, `blocked`), or add a new one there with its combination rule.

## 2. Implement the type

In `simulator/faults/types.py`:

```python
@register
class HeatExchangerFouling(FaultType):
    name = "heat_exchanger_fouling"
    persistent = True                      # stays after stop until repair/reset
    description = "Fouling reduces the cooling tower's thermal performance."
    default_expected_effects = ["CW supply temperature rises", "valves open further"]
    default_observability = {"direct_indication": False, "observable_via": ["XMEAS(21)", "XMV(10)"]}

    def targets(self, engine):             # validated on create_fault
        return ["WU-CT-101"]

    def apply(self, engine, fault, intensity):   # called every second while ACTIVE
        engine.state.fault_effects.set(fault.target, "thermal_degradation", fault.id,
                                       fault.severity * intensity)
```

* Do not write entity properties, XMEAS, XMV or boundaries in `apply`. The only exceptions are the
  IDV types (TEP-native) and sensor types (instrumentation), which already exist.
* Override `remove(engine, fault, reset)` only if the default (`clear_fault`) is not enough.
* Override `cause_keys` if the causal registry should label other keys (sensor types label `XMEAS(n)`).
* Set `supports_gradual = False` for binary causes.

## 3. Make sure something reads the channel

Either a relation input `fault.<TARGET>.<channel>` in `configs/coupling.yaml`, or module code that
calls `state.fault_effects.value(target, channel)`. Otherwise the fault has no effect. The engine does
not warn about this.

## 4. Persistent faults and repair

If `persistent = True` and the target is an asset, make sure the equipment module clears your channel on
repair. `EquipmentModule._on_maintenance_completed` clears `damage`, `efficiency_loss` and `failed`.
Add your channel there if repair should remediate it.

## 5. Test

* Add the fault to a scenario (e.g. SCN-FAULT-LIBRARY) so `test_every_library_fault_can_start_and_stop`
  covers its lifecycle.
* Add a behaviour test asserting the consequence chain: cause channel → module or relation →
  boundary → process.
* Regenerate the docs tables and the graph.

Source:
- `simulator/faults/types.py` — `FaultType`, `register`, `REGISTRY`
- `simulator/faults/effects.py` — `COMBINE`, `FaultEffects.set`
- `simulator/equipment/__init__.py` — `EquipmentModule._on_maintenance_completed`
