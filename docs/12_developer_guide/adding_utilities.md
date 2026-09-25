# Adding a utility service

1. **Declare the service** in `configs/utilities.yaml → services`. Give it `name`, `utility_type`,
   `supplied_by` (an existing ISA-95 element), `serves`, `capacity`, `unit`, `nominal: {...}` (exposed
   as `nominal_*` properties) and `units`. `UtilitiesModule` registers it as an entity of kind
   `utility` and classifies its status every step.
2. **Compute its quantities** with relations in `configs/coupling.yaml`. At least
   `available_capacity` and `utilization` are needed for the status; add `capacity_fraction` to make it
   the availability. Use the categories `utility_supply` and `utility_observation`.
3. **Couple it to TEP only through a boundary parameter** (category `tep_boundary`). If no existing
   parameter fits, see [adding couplings](adding_couplings.md).
4. **Alarms** (`configs/alarms.yaml → utilities`), optionally with `maintenance: inspection` and
   `maintenance_target`.
5. **Faults:** `utility_capacity_loss` accepts any `utility` entity automatically. Its channel
   `fault.<ID>.capacity_loss` must be read by a relation to have an effect.
6. The trend buffer records `available_capacity, utilization, flow, temperature, pressure, availability,
   capacity_fraction` for every service automatically (`TrendBuffer.build_default`).

Source:
- `configs/utilities.yaml`
- `simulator/utilities/__init__.py` — `UtilitiesModule.setup`, `UtilitiesModule._classify`
- `simulator/simulation/history.py` — `TrendBuffer.build_default`
