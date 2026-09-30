# Historian query API

A read-only HTTP service over one Historian SQLite file ([HISTORIAN.md](HISTORIAN.md)). It is a thin
layer over `historian.reader.HistorianReader` ([historian/api.py](../historian/api.py)):

- every query semantic is the reader's: scopes, access policy, half-open ranges, limits, ordering,
  continuation, and no interpolation;
- the service maps HTTP parameters to reader calls, encodes continuation keys as cursors, and returns
  stable JSON errors.

It only answers `GET` requests. It has no write endpoints, never serves the database file, and runs no
caller-supplied SQL.

## Starting it

```bash
python scripts/record_history.py --scenario SCN-COOL-001 --db exports/SCN-COOL-001.sqlite   # record first
python scripts/run_historian_api.py --database exports/SCN-COOL-001.sqlite                 # then serve
#   -> http://127.0.0.1:8060/status
```

| Option | Default | Meaning |
|---|---|---|
| `--database PATH` | (required) | an existing Historian file |
| `--host HOST` | `127.0.0.1` | bind address: localhost only by default |
| `--port PORT` | `8060` | (the simulator uses 8000, the UNS inspector 8050, MQTT 1883) |
| `--access current\|evaluator` | `current` | access policy, below |

The service exits with code 2 and a message if the file does not exist or is not an
`acme-historian/1` file. It is a local benchmark service: there is no authentication, and it should not
be bound to a network interface.

## Access policy

The policy is chosen when the service starts. **No request parameter can change it.**

| `--access` | For | Readable scopes |
|---|---|---|
| `current` (default) | agents and other operational consumers | only the current scope: the one most recently begun in the file |
| `evaluator` | the benchmark evaluator **only** | every recorded scope |

Every query names exactly one scope, and there are no cross-scope queries. Under `current` access, a
query for any other recorded scope is refused with `403 scope_not_permitted`. `/scopes` and `/status`
report only the scopes the policy allows. Nothing identifies a scope except its opaque
`operational_scope_id`: no run id, scenario, seed or configuration hash exists in the file or in any
response.

## Time, limits and pagination

- **Time.** All times are simulation seconds (`t`) or the simulated calendar (`simulation_start`).
  There is no wall-clock time.
- **Ranges** are half-open, `[start, end)`. A range may span at most **86 400** simulated seconds,
  and `end` must be greater than `start`.
- **Limits.** `limit` defaults to **1 000** and may be at most **10 000**.
- **Cursors.** A result that hit its limit has `"truncated": true` and a `next_cursor`. Pass it back as
  `cursor` with **the same parameters** to get the next page. Pages follow the history order: `t` for
  samples, and `(t, seq)` for events and state changes. Following the cursors returns every row
  exactly once. Cursors are opaque and bound to the query that produced them: a cursor used with other
  parameters is rejected (`invalid_cursor`). The last page has `next_cursor: null`.
- **No interpolation.** Values are returned as recorded.

## Endpoints

All responses are JSON. Row shapes are the reader's (see
[HISTORIAN_DATA_MODEL.md](HISTORIAN_DATA_MODEL.md) for the meaning of each field).

| Endpoint | Parameters | Returns |
|---|---|---|
| `GET /status` | — | `api` (`acme-historian-api/1`), `schema` (`acme-historian/1`), `access`, `current_scope`, `scopes_visible`, `coverage` and `covered_range` of the current scope, `limits` |
| `GET /scopes` | — | `scopes`: `operational_scope_id`, `simulation_start`, `duration_seconds`, `sample_period_s`, `schema`, `coverage`, `covered_range` (only permitted scopes) |
| `GET /entities` | `scope`; optional `type`, `parent`, `limit` | `rows`: `entity_id` (canonical), `entity_type`, `parent_id`, `isa95_path`, `isa95_mapping_id`, `name` (entities; null for records), `first_t`; `truncated` |
| `GET /series` | `scope`, `entity`; optional `variable` | `rows`: `series_id`, `entity_id`, `variable`, `kind`, `unit`, `semantics`, `sample_period_s`, `dead_time_s`, `source` |
| `GET /samples` | `scope`, `entity`, `variable`, `start`, `end`; optional `limit`, `cursor` | `series`, `rows` (`t`, `value`, `quality`), `truncated`, `next_cursor` |
| `GET /value_at` | `scope`, `entity`, `variable`, `t` | `value`, `quality`, `sample_time`, `age`, `semantics`, `unit`, `dead_time_s`, `represents_time`, `covered`, `continuous_since_sample`, and the `series` |
| `GET /events` | `scope`, `start`, `end`; optional `entity`, `type`, `limit`, `cursor` | `rows`: `event_id` (OE-/LC-), `t`, `seq`, `event_type`, `entity_id`, `source`, `severity`, `payload`, `causation_id`, `correlation_id`; `truncated`, `next_cursor` |
| `GET /states` | `scope`, `entity`, `start`, `end`; optional `property`, `limit`, `cursor` | `rows`: `entity_id`, `property`, `t`, `seq`, `value`, `unit`, `origin` (`baseline`/`change`); `truncated`, `next_cursor` |
| `GET /state_at` | `scope`, `entity`, `t` | `state`: per property `value`, `unit`, `since`, `origin`; `covered` |

- **`/value_at`** returns the last sample at or before `t`, or nulls if there is none.
  `continuous_since_sample: false` means a recording gap lies between that sample and `t`. For an
  analyzer, `represents_time = sample_time − dead_time_s`.
- **`/state_at`** returns, for every property, its last recorded value at or before `t`.

Example:

```text
GET /value_at?scope=OS-…&entity=equipment_module:EM-REACTOR&variable=measurement:XMEAS(9)&t=4833
{"value": 120.845…, "sample_time": 4833, "age": 0, "semantics": "step_state", "unit": "degC",
 "covered": true, "continuous_since_sample": true, "series": {…}, …}
```

## Errors

Errors carry an HTTP status and a stable body: `{"error": {"code": "...", "message": "..."}}`.
Messages never contain database paths, SQL or stack traces.

| Status | `code` | When |
|---|---|---|
| 400 | `missing_parameter`, `invalid_parameter` | a required parameter is missing, or a parameter is malformed (e.g. `start=x`) |
| 400 | `missing_scope` | `scope` is empty |
| 400 | `invalid_range`, `span_exceeded` | `end <= start`, or the span exceeds 86 400 s |
| 400 | `invalid_limit` | `limit` is outside 1..10 000 |
| 400 | `invalid_cursor` | a malformed cursor, or the cursor of another query |
| 403 | `scope_not_permitted` | a scope other than the current one, under `current` access |
| 404 | `unknown_scope`, `unknown_entity`, `unknown_series` | not recorded in the file (or scope) |
| 404 | `not_found` | no such endpoint |
| 405 | `method_not_allowed` | any method other than `GET` (the API is read-only) |
| 503 | `database_unavailable`, `incompatible_schema` | the database cannot be read |

Source:
- `historian/api.py`, `historian/reader.py`
- `scripts/run_historian_api.py`
- `tests/test_historian_api.py`
