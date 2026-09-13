# Architecture & Components

Part of the [AI4SWENG Observability Stack](../README.md) documentation. See the
[documentation index](README.md) for the full set of guides.

## What's inside

| Component | Role | Port |
|-----------|------|------|
| `otel-collector` | Single OTLP ingestion gateway; fans metrics → VictoriaMetrics, logs → VictoriaLogs, traces → Tempo | 4317 (gRPC), 4318 (HTTP) |
| `victoriametrics` | Metrics store (Prometheus-compatible, no Prometheus needed) | 8428 |
| `victorialogs` | Store for unstructured / string telemetry | 9428 |
| `tempo` | Trace store (monolithic mode, local disk) — powers the trace waterfall view | 3200 |
| `grafana` | Dashboards (auto-provisioned) | 3000 |
| `metrics-api` | Parameterized read API over the metrics store — query telemetry without writing PromQL (see [Metrics Reference & Query API](metrics-reference.md)) | 8081 |
| `langfuse-web` / `langfuse-worker` | Self-hosted Langfuse — LLM-specific prompt/completion/cost tracing, a stream parallel to and independent of OTel | 3001 (UI+API), internal 3030 (worker) |
| `postgres` / `clickhouse` / `redis` / `minio` | Langfuse's own required backing stores (relational DB, trace analytics, queue, blob storage) — not something we chose, this is Langfuse's mandated self-host footprint | internal only (127.0.0.1-bound except minio :9090) |
| ~~`nats` / `orchestrator-postgres` / `workflow-api` / `planner`~~ | **Disabled — commented out in `docker-compose.yml`.** These are the orchestration layer (task dispatch), which architecturally belongs to **KIO1**, not this observability platform — kept as reference only (see [Orchestration Layer](orchestration.md)) | off |
| `kio2-sim` / `kio3` | KIO simulators pushing contract-compliant dummy telemetry, dual-written to OTel + Langfuse, each on its own internal timer (`kio2-sim` also drives the optional real-Ollama demo). More sims (`kio4`/`kio7`/`kio8`/`kio13`) are present but commented out — uncomment in `docker-compose.yml` to light up their D1.1 KPI panels (see [KIO Simulators](kio-simulators.md)) | — |

The KIOs never run their own collector, never expose a scrape endpoint, and never
touch a database directly — exactly as the contract requires.

## Layout

```
observability/
├── docs/Observability_v2.2.docx              # normative guide (v2.2) — start here for a new KIO
├── docker-compose.yml
├── pytest.ini
├── otel-collector/config.yaml
├── tempo/tempo.yaml
├── grafana/
│   ├── provisioning/datasources/datasources.yml
│   ├── provisioning/dashboards/dashboards.yml
│   ├── provisioning/alerting/rules.yml       # Stale KIO alert (Contract §2.3)
│   └── dashboards/{ai4sweng-overview,ai4sweng-kio}.json
├── kio-simulator/{kio_simulator.py,requirements.txt,Dockerfile}
├── metrics_api/                              # /api/metrics query API (read-only, 8081)
│   ├── {app.py,query.py,registry.py,stats.py,timefmt.py,victoriametrics.py}
│   └── registry_data.py                      # GENERATED — see scripts/
├── orchestrator/{planner.py,session_manager.py,workflow_api.py,envelope.py}  # reference — disabled in compose (KIO1's)
├── remote-kio/                              # connect a KIO from another machine
│   ├── {README.md,INTEGRATION.md,NETWORK.md}   # hub / own-module guide / networking
│   ├── with_script/{main.py,kio_otel.py,check_connectivity.py,requirements.txt,.env.example}
│   └── with_docker/{Dockerfile,docker-compose.yml,main.py,kio_otel.py,check_connectivity.py,…}
├── tests/{conftest.py,requirements-test.txt,kio_simulator_tests/,orchestrator_tests/,metrics_api_tests/}
└── docs/                                     # this documentation, plus the v2.2 guide, technical reports, KPI reference
```

## Design notes & decisions

- **Prometheus is intentionally absent** — VictoriaMetrics ingests via remote_write
  (push), which fits the contract's "KIOs never expose a scrape endpoint" rule.
- **Tempo** stores traces in monolithic mode with local-disk storage — sufficient for
  local dev; a production deployment would move to object storage (S3/GCS) and
  split Tempo's components.
- **Langfuse** is integrated (see [Langfuse](langfuse.md)) as a second stream
  parallel to metrics + logs + traces, added per direct request and evaluated
  against the v2 proposal in [Design Decisions & v2 Guideline Evaluation](design-decisions.md).
- **Auth/TLS (2026-08, §9.3): Bearer done, TLS still deferred.** The contract uses
  Bearer token + TLS on `:4317`/`:4318`. The collector's `bearertokenauth`
  extension (`otel-collector/config.yaml`) now enforces the Bearer half on both
  gRPC and HTTP — every KIO in `docker-compose.yml` sends
  `OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer $OTLP_BEARER_TOKEN"` (shared
  value, set via `.env`'s `OTLP_BEARER_TOKEN`, default `local-dev-otlp-token`; no
  `kio_simulator.py` code change needed — the OTel SDK reads this env var on its
  own). TLS itself is still not enabled — the connection is authenticated but not
  encrypted, fine for a trusted LAN/VPN, not for the open internet. Real TLS
  (certs on the collector, `insecure=False` on every client) is left for an
  actual remote/production deployment, not this docker-compose.
- **Remote KIOs**: fully supported today via the push architecture — see
  [Connecting a Remote KIO](remote-connectivity.md).
- **Grafana access (2026-08):** anonymous viewers, not anonymous admins. Until
  now `GF_AUTH_ANONYMOUS_ORG_ROLE=Admin` meant anyone who could reach `:3000` —
  no login at all — got full edit/delete/datasource/alerting/user-management
  rights. Harmless on localhost-only, a real problem the moment this port is
  reachable over Tailscale/LAN/internet (which is exactly the direction this
  project is heading with remote KIOs). Now: anonymous role is `Viewer`
  (dashboards stay fully browsable with zero login, matching the "let anyone
  look" intent), and the default admin password was rotated off `admin` to a
  random placeholder in `.env.example` — still rotate it yourself before any
  real/shared deployment, this is still a shared local-dev default.
