# Adding a scenario

1. Copy a file in `scenarios/` (or use the UI Scenario tab: duplicate, or save the current run).
2. Set a unique `id` (letters, digits, `_ . -`) and a `seed`.
3. Add faults, production orders, planned maintenance and scripted operator actions
   ([scenario model](../06_scenarios/scenario_model.md)). Scripted actions are the only reproducible
   way to include operator behaviour.
4. For parameter variations, use `config_overrides` instead of editing `configs/`:

   ```yaml
   config_overrides:
     maintenance: {response_time_s: {2: 900}}
     alarms: {defaults: {on_delay_s: 5}}
   ```

5. Validate by loading it: `python scripts/run_demo.py --scenario <id>`. Any `ConfigError` names the
   problem.
6. It is automatically covered by `test_every_library_scenario_loads_and_runs` (120 s smoke run).
   Add a behaviour test if the scenario is meant to demonstrate a chain.

**Tip for fault scenarios:** check the outcome's sensitivity. Run several severities and seeds.
Thresholds such as the reactor CW trip cliff are sharp ([scenario validation](../09_validation/scenario_validation.md)).

Source:
- `simulator/scenarios/__init__.py` — `validate_scenario`, `ScenarioStore.save`
- `scripts/run_demo.py`
