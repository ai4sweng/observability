# AI4SWENG Observability Stack

<div align="center">

[![AI4SWENG Observability — demo video](assets/screenshot_1.png)](https://www.youtube.com/watch?v=2chrEqgPCFQ)

</div>

Local implementation of the **[AI4SWENG Observability Integration Guide v2.2](https://ai4seceu.sharepoint.com/:f:/s/AI4SwEng134/IgCtKA3X92K5T7XYr7gvzTIKAXhV17nbktjaGPy9_BZ26rM?e=WKBQQB)**.
KIO modules push telemetry over
OTLP; the central platform stores it and Grafana visualizes it. Stack: 


- VictoriaMetrics for metrics,
- VictoriaLogs for unstructured string logs, 
- Tempo for traces,
- langfuse for LLM prompt/completion/cost tracing,
- Grafana for the overall visualization 

is the **generic central platform every KIO connects to**; it doesn't run or own any KIO's real logic. The push-based architecture means a KIO can connect from a completely different machine
with no code change, only one address — see
**[Connecting a KIO — Data Contract & Setup Guide](docs/remote-connectivity.md)**.

> **⚠️ The KIOs bundled in this repo (`kio-simulator/`) are simulators.** They
> emit synthetic telemetry that follows the same contract a real KIO must
> follow, so every dashboard has something to show — they are not real KIO
> module output. See [The KIO Simulators](docs/kio-simulators.md) for exactly
> what's real vs. simulated.

<div align="center">

  <img src="assets/architecture_v2.2.png" width="600" alt="Architecture diagram" />

</div>

## Documentation

This README covers local quick start only. Everything else architecture,
metrics reference, the remote-KIO integration path, KPIs, testing, and design decisions lives under **[`docs/`](docs/README.md)**:

| Guide | Covers |
|---|---|
| **[Connecting a KIO — Data Contract & Setup Guide](docs/remote-connectivity.md)** | The full data contract (mandatory metrics, types, units, optional D1.1 extras) plus setup for both a remote-server deployment and a fully-local one, start here if you're connecting a KIO |
| [Architecture & Components](docs/architecture.md) | What each service does, the repo layout, and standing design decisions |
| [The KIO Simulators](docs/kio-simulators.md) | Which simulators run by default and the real-vs-simulated data map |
| [Metrics Reference & Query API](docs/metrics-reference.md) | The 7 mandatory metrics, the `/api/metrics` HTTP query API, logs, and traces |
| [Real Project KPIs (D1.1)](docs/kpis.md) | The 16 D1.1 KPIs and which KIO emits which |
| [Real LLM Integration (KIO2)](docs/real-llm-integration.md) | Running a real Ollama LLM + real GPU energy behind `kio2-sim` |
| [Langfuse (LLM Tracing)](docs/langfuse.md) | The self-hosted Langfuse stack for prompt/completion/cost tracing |
| [Tests (pytest)](docs/testing.md) | Running the test suite |
| [Design Decisions & v2 Guideline Evaluation](docs/design-decisions.md) | Why this stack made the choices it did |

See **[`docs/README.md`](docs/README.md)** for the full index, including the
normative reference documents (`.docx`).

## Quick start

Works unmodified on both Windows (Docker Desktop) and Linux (native Docker
Engine, e.g. Ubuntu), everything runs in containers with relative bind
mounts and standard Linux images; the only host-OS-sensitive bit
(`host.docker.internal` for the optional real-Ollama/GPU path) is handled via
`extra_hosts` in `docker-compose.yml`, see
[Real LLM Integration](docs/real-llm-integration.md).

All ports and passwords/keys are centralized: every one is overridable from a
single repo-root `.env` file (`cp .env.example .env`, then uncomment what you
need), `docker-compose.yml` itself never needs editing. No `.env`? Every
default in `.env.example` is already baked in, so `docker compose up` works
as-is.

```bash
cd observability
docker compose up -d --build
```

Then open **http://localhost:3000**, anonymous browsing is on (Viewer role, no
login needed to look at dashboards); editing/deleting/datasources/alerting/user
management need the admin login (`admin` / `GF_SECURITY_ADMIN_PASSWORD` from
`.env.example`, rotate before any real/shared deployment). Eleven dashboards
appear under the **AI4SWENG** folder:

- **AI4SWENG — Overview (All KIOs)**: aggregate stats across every KIO,
  request & error rates, latency p95, token throughput, tokens/sec, GPU
  energy, cost, accuracy — plus a **D1.1 KPI section**: a bar gauge of current
  attainment per KPI (100% = target met, green/orange/red), the raw measured
  value in D1.1's own unit, and a **trend panel with a red threshold line at
  100%** so you can see at a glance whether a KPI is consistently meeting its
  GA-acceptable commitment over time, not just right now. These aggregate
  panels have **no per-KIO filter** — any `kio.id` that sends the matching
  metrics is automatically folded in, including one nobody registered.
- **AI4SWENG — KIO*N* Detail** (one dashboard per KIO — KIO1, KIO2, KIO3,
  KIO4, KIO7, KIO8, KIO9, KIO10, KIO11, KIO13): per-KIO metrics, a live panel
  of that KIO's unstructured string logs, and a **traces** section, a table of
  recent traces plus a waterfall view of the selected one (copy a Trace ID
  from the table into the `trace_id` box above it). Further down: a **gauge
  view** (colored green/orange/red bands, mirroring an external dashboard
  reference the team liked) for error rate, tokens/sec, and accuracy; a
  **power/efficiency/carbon** row (Watts, tokens-per-Watt, and an estimated
  kg-CO2e derived from energy, all pure PromQL, no new instrumentation); a
  **real GPU temperature** gauge (only populated when
  `KIO2_REAL_LLM_ENABLED=true` and NVML/power-exporter is reachable. "No data"
  otherwise is expected, not a bug); and a **simulated vs. real** before/after
  bar-chart row (energy, tokens/sec, latency) split by the `source` label.
  **Each of these dashboards is wired to one specific, pre-registered
  `kio.id`** (set as a fixed dropdown option in that dashboard's JSON, not
  discovered dynamically) — a KIO sending an ID nobody created a dashboard for
  shows up only in the Overview aggregate, never gets its own detail page. See
  [Connecting a KIO](docs/remote-connectivity.md) for what `kio.id` to use.

Give it ~30–60 seconds after startup for the first metrics and traces to land.

Separately, **http://localhost:3001** opens the Langfuse UI, see
[Langfuse](docs/langfuse.md) for login details and remote-deployment notes.

Tear down (and wipe data): `docker compose down -v`

Then run `uv run pytest` to exercise the test suite, see [Tests](docs/testing.md).

## Layout

```
observability/
├── docs/                                     # full documentation set — see docs/README.md
├── docker-compose.yml
├── otel-collector/ tempo/ grafana/            # ingestion gateway, trace store, dashboards
├── kio-simulator/                             # dummy KIO telemetry generators
├── metrics_api/                               # /api/metrics query API (read-only, 8081)
├── remote-kio/                                # connect a KIO from another machine — see docs/remote-connectivity.md
└── tests/                                     # pytest suite
```

Full breakdown in [Architecture & Components](docs/architecture.md#layout).
