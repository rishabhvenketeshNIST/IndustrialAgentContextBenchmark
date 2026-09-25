# Running

## Interactive (one command)

```bash
python run.py [--port 8000] [--host 127.0.0.1] [--scenario SCN-COOL-001] [--backend auto|fortran|python] [--no-browser]
```

It checks dependencies, builds the Fortran library if it is missing, loads the scenario (status READY,
t = 0) and serves the UI at `http://127.0.0.1:8000/` and the OpenAPI docs at `/docs`. Stop it with
Ctrl+C.

## The UI

The UI is plain HTML/CSS/JS in `ui/` with no build step. It polls `/api/ui/snapshot` every 1 s, renders
trends every 2 s and refreshes the active tab every 3 s.

| Area | Content |
|---|---|
| **Top bar** | scenario and duration selection, Load, Start, Pause, Resume, Step (1 s, 1 min, 10 min, 1 h), Reset, speed (0.1× … 2000×), clock and progress, run/process/alarm badges, JSON/CSV export, theme |
| **KPI strip** | production state and rate, produced/accepted/rejected, OEE, reactor T and P, utility availability |
| **Left** | ISA-95 tree: level badge, `ent` marker for enterprise-layer elements, roll-up status dot, active alarm count |
| **Centre tabs** | Process schematic (live values, valves, unit status, alarmed values, shutdown banner); Loops & Variables; Utilities; Maintenance; Inventory & Warehouse; Quality; Production; Alarms & Events; Causal Model; Scenario; **Fault Injection (benchmark)**, visually marked red |
| **Right** | selected element: path, level, origin, status, TEP variables, loops with separate PV/SP/output/mode and operator controls, asset commands, properties (hides `unobservable` ones), alarms |
| **Bottom** | trend charts from the in-memory buffer, one chart per measure, optional setpoint and true-value overlays, synchronised crosshair |

Deep links: `/#tab=faults&select=WU-CWP-101A&theme=dark`.

```mermaid
flowchart LR
  subgraph Browser
    TOP["top bar"] & TREE["tree"] & TABS["tabs"] & DET["details"] & TR["trends"]
  end
  TOP -->|POST /api/simulation/*| API
  TREE & DET -->|GET /api/entities/{id}| API
  DET -->|POST /api/operator/*| API
  TABS -->|GET module routes, /api/benchmark/* for faults| API
  TR -->|GET /api/history| API
  Browser -->|GET /api/ui/snapshot every 1 s| API["FastAPI"]
```

## Headless

```bash
python scripts/run_demo.py [--scenario SCN-COOL-001] [--backend ...] [--duration s] [--out exports]
```

It runs the scenario as fast as possible (about 6.5 s for the 3 h demo on the development machine),
prints the event timeline with correlation ids, and writes `<run_id>.json` and `<run_id>.zip`.

## Documentation tooling

```bash
python scripts/generate_docs_tables.py      # regenerate reference tables (runs the demo)
python scripts/build_variable_graph.py      # regenerate the variable graph + viewer
python scripts/check_docs.py                # verify doc links and source references
```

Source:
- `run.py` — `main`
- `scripts/run_demo.py` — `main`
- `ui/js/app.js`
- `ui/index.html`
