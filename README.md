# AI4SWENG Observability Stack

Local implementation of the **AI4SWENG Observability Integration Contract v1.0**.
KIO modules push telemetry over OTLP; the central platform stores it and Grafana
visualizes it. Everything runs locally via Docker Compose.


<div align="center">

  <img src="assets/architecture.png" width="600" alt="Architecture diagram" />

</div>

```
                 OTLP/gRPC (:4317)
  KIO2 ┐                              ┌─ metrics ─► VictoriaMetrics (:8428) ─┐
  KIO3 ├──►  OpenTelemetry Collector ─┤                                      ├─► Grafana (:3000)
  KIO4 ┘                              └─ logs ────► VictoriaLogs    (:9428) ─┘
       (dummy telemetry)                 (traces pipeline wired but deferred — see below)
```

## What's inside

| Component | Role | Port |
|-----------|------|------|
| `otel-collector` | Single OTLP ingestion gateway; fans metrics → VictoriaMetrics, logs → VictoriaLogs | 4317 (gRPC), 4318 (HTTP) |
| `victoriametrics` | Metrics store (Prometheus-compatible, no Prometheus needed) | 8428 |
| `victorialogs` | Store for unstructured / string telemetry | 9428 |
| `grafana` | Dashboards (auto-provisioned) | 3000 |
| `kio2` / `kio3` / `kio4` | KIO simulators pushing contract-compliant dummy telemetry | — |

The KIOs never run their own collector, never expose a scrape endpoint, and never
touch a database directly — exactly as the contract requires.

## Quick start

```bash
cd observability
docker compose up -d --build
```

Then open **http://localhost:3000** (login `admin` / `admin`, anonymous access is
also on). Two dashboards appear under the **AI4SWENG** folder:

- **AI4SWENG — Overview (All KIOs)**: aggregate stats across every KIO — request &
  error rates, latency p95, token throughput, tokens/sec, GPU energy, cost, accuracy.
- **AI4SWENG — KIO Detail**: pick a KIO from the `KIO` dropdown; per-KIO metrics plus
  a live panel of that KIO's unstructured string logs.

Give it ~30–60 seconds after startup for the first metrics to land.

Tear down (and wipe data): `docker compose down -v`

## The three KIO simulators

| KIO | LLM | Task type | Notes |
|-----|-----|-----------|-------|
| kio2 | `qwen2.5:3b` | code-analysis | also reports **real** repo line / directory / file counts of its own source |
| kio3 | `llama3.1:8b` | test-generation | random dummy telemetry |
| kio4 | `gpt-4o-mini` | debug | random dummy telemetry (non-zero cost) |

All values are randomly generated — no real LLM is invoked. Adjust LLMs, task types,
and rates in `docker-compose.yml`, or edit `kio-simulator/kio_simulator.py`.

## Metrics (Integration Contract §2.1 + optional extras)

Mandatory set, all carrying `kio_id`:

- `kio_request_count` (counter; labels `kio_id`, `status`)
- `kio_request_duration_ms` (histogram → `_bucket` / `_sum` / `_count`)
- `kio_request_error_count` (counter; `error_type`)
- `kio_llm_token_count` (counter; `direction` = input/output)
- `kio_llm_cost_usd` (counter)
- `kio_session_active_count` (up/down counter)
- `kio_heartbeat` (counter; ticks every 60s — a KIO silent >120s is "stale")

Optional self-service metrics (contract rule G7, meeting requirements):

- `kio_llm_tokens_per_second` (histogram)
- `kio_llm_energy_joules` (counter — GPU energy during token generation)
- `kio_request_accuracy` (histogram)
- `kio_repo_line_count` / `kio_repo_directory_count` / `kio_repo_file_count` (gauges, code-analysis KIO only)

Labels `llm` and `task_type` are attached to every metric so dashboards can slice by
model and by KIO. (Metric-name suffixing is disabled in the collector, so names stay
clean — no `_total` suffix.)

## Unstructured / string telemetry

Free-form strings (LLM output snippets, analysis notes, failure summaries) are sent as
**OTLP logs** and stored in **VictoriaLogs**, since VictoriaMetrics can only hold
numeric series. They show up in the log panel on the KIO Detail dashboard. Query them
directly with LogsQL, e.g. `kio.id:kio2` or `_msg:~"coverage"`.

## Design notes & decisions

- **Prometheus is intentionally absent** — VictoriaMetrics ingests via remote_write
  (push), which fits the contract's "KIOs never expose a scrape endpoint" rule.
- **Tempo/Jaeger (traces) deferred to phase 2.** The collector already has a traces
  pipeline wired (currently debug-only). With dummy data there are no real sub-span
  timings to show; when needed, add a Tempo service and point the traces pipeline at it.
- **Langfuse** (the second stream in the contract) is out of scope here — a separate,
  small integration owned separately. This stack covers metrics + logs.
- **Auth/TLS**: the contract uses Bearer token + TLS on `:4317`. For local dev the
  collector listens insecure. To exercise the real path, add a `bearertokenauth`
  extension to the collector and set `OTEL_EXPORTER_OTLP_HEADERS` on the KIOs.
- **Remote KIOs**: when KIOs run on other machines later, expose the collector's
  `:4317` (with TLS + Bearer) and point their `OTEL_EXPORTER_OTLP_ENDPOINT` at it —
  no other change needed.

## Layout

```
observability/
├── docker-compose.yml
├── otel-collector/config.yaml
├── grafana/
│   ├── provisioning/datasources/datasources.yml
│   ├── provisioning/dashboards/dashboards.yml
│   └── dashboards/{ai4sweng-overview,ai4sweng-kio}.json
└── kio-simulator/{kio_simulator.py,requirements.txt,Dockerfile}
```
