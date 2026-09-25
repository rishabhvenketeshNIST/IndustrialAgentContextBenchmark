# Site (SITE-TE)

**Represents:** the Tennessee Eastman Manufacturing Site. It is a single continuous-process site whose
primary production process is TEP, running in Downs & Vogel Mode 1 (50/50 G/H by mass). The mode is
recorded as a site attribute; nothing in the code switches modes.

**State:** no properties of its own. `EnterpriseView.site` adds the production summary
(`state.production`) to the API response.

**What happens at site level:**

* **Plant control mode.** `set_control_mode` (CLOSED_LOOP/MANUAL) publishes `CONTROL_MODE_CHANGED`
  targeted at `SITE-TE`.
* **Process shutdown.** `PROCESS_SHUTDOWN` and `SIMULATION_*` events target `SITE-TE`.
* **ESD alarm.** The alarm `ESD-TRIP` is attached to `SITE-TE`.

**Time:** the site clock is the simulation clock. `simulation_start` (a scenario field, default
2026-01-05T06:00:00Z) defines wall-clock timestamps. Technician shifts are evaluated in that clock's
hour of day, in UTC.

Source:
- `configs/site.yaml`
- `simulator/enterprise/__init__.py` — `EnterpriseView.site`
- `simulator/simulation/process_interface.py` — `ProcessInterface.set_control_mode`
- `simulator/alarms/__init__.py` — `AlarmModule.setup`
