# Metrics Reference & Query API

Part of the [AI4SWENG Observability Stack](../README.md) documentation. See the
[documentation index](README.md) for the full set of guides.

## Metrics (Integration Guide §2.1 + optional extras)

Mandatory set, all carrying `kio_id`:

- `kio_request_count` (counter; labels `kio_id`, `status`)
- `kio_request_duration_ms` (histogram → `_bucket` / `_sum` / `_count`)
- `kio_request_error_count` (counter; `error_type`)
- `kio_llm_token_count` (counter; `direction` = input/output)
- `kio_llm_cost_usd` (counter)
- `kio_session_active_count` (up/down counter)
- `kio_heartbeat` (counter; ticks every 60s — a KIO silent >120s is "stale", enforced
  by a real Grafana alert rule, see `grafana/provisioning/alerting/rules.yml`, plus a
  visual "Stale KIO Check" table on the Overview dashboard — not just a manual check
  anymore)

Optional self-service metrics (contract rule G7, meeting requirements):

- `kio_llm_tokens_per_second` (histogram)
- `kio_llm_energy_joules` (counter — GPU energy during token generation)
- `kio_request_accuracy` (histogram)
- `kio_repo_line_count` / `kio_repo_directory_count` / `kio_repo_file_count` (gauges, code-analysis KIO only)

Labels `llm` and `task_type` are attached to every metric so dashboards can slice by
model and by KIO. (Metric-name suffixing is disabled in the collector, so names stay
clean — no `_total` suffix.)

For the full data & naming contract (transport, resource attributes, the G1–G7
rules) see [`remote-kio/INTEGRATION.md`](../remote-kio/INTEGRATION.md) §3, or
[Connecting a Remote KIO](remote-connectivity.md).

## Querying metrics over HTTP (`/api/metrics`)

The `metrics-api` service (port **8081**) gives external callers parameterized
read access to the operational telemetry already in VictoriaMetrics, so nobody
has to write PromQL by hand. Without it, asking for one KIO's average request
latency means knowing three things that are nowhere documented: that
`kio.request.duration_ms` is stored as `kio_request_duration_ms`, that its
average is `rate(_sum) / rate(_count)` with a divide-by-zero guard, and that
`kio.request.count` carries a `status` label that must be summed over or the
answer is double-counted.

```bash
# Average request latency for one KIO, right now
curl "http://localhost:8081/api/metrics?metric=request_duration_ms&kio_id=kio2-sim"

# True p95 from the histogram buckets
curl "http://localhost:8081/api/metrics?metric=request_duration_ms&aggregate=p95"

# Statistics over the last 24h, real-Ollama data only
curl "http://localhost:8081/api/metrics?metric=llm_tokens_per_second&kio_id=kio2-sim&period=24h&source=real&format=summary"

# Time series for charting
curl "http://localhost:8081/api/metrics?metric=request_count&period=6h&step=15m&format=series"

# Error rate broken out by outcome instead of summed
curl "http://localhost:8081/api/metrics?metric=request_count&group_by=status"

# What can I query, and with which filters?
curl "http://localhost:8081/api/metrics/catalog"
```

Metric names are accepted in any spelling — `kio.request.duration_ms`,
`kio_request_duration_ms`, `request.duration_ms` or `request_duration_ms` all
resolve to the same metric, and an ambiguous short form is an error rather than
a guess.

**Parameters:** `metric` (required, comma-separated), `aggregate`, `format`
(`auto`/`snapshot`/`summary`/`series`), `group_by`, and any valid label as a
filter (`kio_id`, `llm`, `task_type`, `source`, `status`, `direction`,
`error_type`). Plus six time controls:

| Parameter | Default | Controls |
|---|---|---|
| `period` | `5m` | Relative window — `15m`, `1h`, `24h`, `7d`, `30d` |
| `start` / `end` | — | Absolute ISO-8601 or Unix seconds; mutually exclusive with `period` |
| `step` | `auto` | Sampling resolution for `series`/`summary`. `auto` = `period/100`, floored at the 15s export interval (a finer step only produces gaps) |
| `window` | `auto` | Rate lookback *per point*. `auto` = `step` + one 15s export interval, floored at 60s (Grafana's `$__rate_interval` formula) |
| `tz` | `UTC` | IANA timezone deciding where bucket boundaries fall, e.g. `Europe/Istanbul` |
| `align` | `auto` | Snap range points to calendar boundaries. `auto` = yes for steps of an hour or more, no below that; `calendar` = always; `none` = never |

`window` and `period` are different things, and conflating them is a real
trap. For an instant query they coincide — "the rate over the last 5m" is one
number covering one window. For a range query they must not: evaluating
`rate(x[24h])` at every step of a 24h range returns a 24h-smoothed value at
*each* point, so the series flattens into nearly a straight line and its
`min`/`max` collapse toward the mean. Sizing the window off the step instead
makes each point summarise its own neighbourhood. Note the window tracks the
step *additively*, not as a multiple of it — a 4x rule would give a `1d` step a
four-day lookback, smoothing each point across four neighbouring buckets. The
effective `window` is echoed in every response.

`align` matters for reporting. VictoriaMetrics places range points at `start`,
`start + step`, `start + 2*step`, ... so a `period=7d&step=1d` request made at
14:37 would otherwise return points at 14:37 on each of the last seven days —
day-sized buckets that are not *calendar* days, so two reports run an hour
apart disagree about the same "day". Aligning `start` down to a boundary in
`tz` fixes it, since every later point inherits the alignment. Daily and
weekly steps snap to local midnight; a multi-day step is anchored so
successive requests describe the same bucket.

```bash
# Daily trend in Istanbul local days, aligned to local midnight
curl "http://localhost:8081/api/metrics?metric=request_count&period=7d&step=1d&format=series&tz=Europe/Istanbul"
```

Series points carry both timestamp forms — ISO-8601 for a human reading the
response, raw Unix seconds beside it for a machine:

```json
{ "timestamp": "2026-09-09T00:00:00Z", "unix": 1788912000.0, "value": 0.317 }
```

**Query shape follows the instrument type**, which is where the hand-written
PromQL usually goes wrong. Counters default to a rate (averaging a monotonic
counter is meaningless, and asking for it returns a 400 rather than a number);
histograms default to the guarded `_sum`/`_count` ratio; gauges are read
directly. Note that `aggregate=p95` on a histogram is the true 95th percentile
of the observation distribution via `histogram_quantile`, whereas the `p95`
inside a `format=summary` block is the 95th percentile of the period's samples
of the average — "how bad did the average get", not "the slowest request".
Different questions, so they are reached by different parameters.

**Nothing fails silently.** An unknown metric, a filter the metric does not
carry (`source` on `kio.heartbeat`, say — five metrics genuinely lack it), a
misspelled parameter, or an impossible aggregation all return **400** with a
message naming what was wrong and what is valid. An empty result returns **200**
with a `no_data_reason` explaining whether the absence is expected (D1.1 KPIs
are only emitted by KIOs with a matching `KIO_REAL_KPI_ROLE`; the `kio.repo.*`
gauges only by `task_type=code-analysis`) or a possible fault. If
VictoriaMetrics itself is unreachable that is a **502**, never an empty success
— the distinction between "the value is zero" and "the pipeline is broken" is
the whole reason the endpoint exists.

> **Unauthenticated, by deliberate decision.** Anyone who can reach `:8081`
> can read all KIO telemetry. That is accepted here: this stack is a testbed
> and the telemetry is simulated KIO output with nothing sensitive in it. It
> also adds no exposure that did not already exist — the VictoriaMetrics
> (`:8428`), VictoriaLogs (`:9428`) and Tempo (`:3200`) read paths are open
> too, and `:8428` offers full PromQL, so this endpoint is strictly less
> capable than what sits beside it. Only OTLP *ingest* is Bearer-protected.
> If real data ever flows through this stack, add auth (reusing
> `OTLP_BEARER_TOKEN` or a separate read token) and bind the read ports to
> localhost — every port already comes from a `.env` variable, so
> `METRICS_API_PORT=127.0.0.1:8081` is enough, with no compose edit.

The registry that powers all of this is **generated** from
`kio-simulator/kio_simulator.py` — instrument type, unit, label set and
conditional-emission gates are extracted by a static AST parse (an import would
only ever see the instruments the current env vars enable, and none of the
~20 D1.1 KPI instruments exist unless a role is set):

```bash
python scripts/generate_metric_registry.py     # -> metrics_api/registry_data.py
```

It is committed, because the API's Docker image does not have the simulator in
its build context. `tests/metrics_api_tests/test_registry_drift.py` fails if the
two ever disagree, so adding an instrument to the simulator without
regenerating is caught by the suite rather than discovered in production.

D1.1 project KPIs are a separate endpoint (`/api/kpis`) with baseline/target
evaluation — not yet implemented; the KPI family is already in the registry and
visible via `/api/metrics/catalog?family=kpi`. See also the
[roadmap for the KPI query API](ROADMAP_KPI_API.md).

## Unstructured / string telemetry

Free-form strings (LLM output snippets, analysis notes, failure summaries) are sent as
**OTLP logs** and stored in **VictoriaLogs**, since VictoriaMetrics can only hold
numeric series. They show up in the log panel on the KIO Detail dashboard. Query them
directly with LogsQL, e.g. `kio.id:kio2` or `_msg:~"coverage"`.

## Traces

Each request's `kio.request` trace (with its `prepare_prompt` / `repo_scan` /
`llm_call` / `postprocess` children) is exported to **Tempo**. The KIO Detail
dashboard exposes it two ways: a TraceQL-backed table of recent traces for the
selected KIO, and a waterfall panel that renders the full span sequence once you
paste a Trace ID from that table into the `trace_id` variable. If the waterfall
panel ever comes up empty, the same Trace ID can always be opened via
**Explore → Tempo** in Grafana as a fallback.
