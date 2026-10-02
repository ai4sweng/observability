# Architecture & Components

Part of the [AI4SWENG Observability Stack](../README.md) documentation. See the
[documentation index](README.md) for the full set of guides.

## What's inside

| Component | Role | Port |
|-----------|------|------|
| `otel-collector` | Single OTLP ingestion gateway; fans metrics → VictoriaMetrics, logs → VictoriaLogs, traces → Tempo | 5317 (gRPC), 4318 (HTTP) |
| `victoriametrics` | Metrics store (Prometheus-compatible, no Prometheus needed) | 8428 |
| `victorialogs` | Store for unstructured / string telemetry | 9428 |
| `tempo` | Trace store (monolithic mode, local disk) — powers the trace waterfall view | 3200 |
| `grafana` | Dashboards (auto-provisioned) | 3000 |
| `metrics-api` | Parameterized read API over the metrics store — query telemetry without writing PromQL (see [Metrics Reference & Query API](metrics-reference.md)) | 8081 |
| `langfuse-web` / `langfuse-worker` | Self-hosted Langfuse — LLM-specific prompt/completion/cost tracing, a stream parallel to and independent of OTel | 3001 (UI+API), internal 3030 (worker) |
| `postgres` / `clickhouse` / `redis` / `minio` | Langfuse's own required backing stores (relational DB, trace analytics, queue, blob storage) — not something we chose, this is Langfuse's mandated self-host footprint. ClickHouse/MinIO retention: [Langfuse Data Retention](data-retention.md) | not published to the host (Docker network only), except minio on :9090 |
| `kio2-sim` / `kio7-sim` | The two KIO simulators pushing contract-compliant dummy telemetry, dual-written to OTel + Langfuse (the Langfuse stream sampled at 20% by default), each on its own internal timer (`kio2-sim` also drives the optional real-Ollama demo). The other simulator roles can be re-added — see [KIO Simulators](kio-simulators.md) | — |

The KIOs never run their own collector, never expose a scrape endpoint, and never
touch a database directly — exactly as the contract requires.

## Layout

```
observability/
├── docs/report/Observability_v2.3.docx              # normative Integration Guide — start here for a new KIO
├── docker-compose.yml
├── pytest.ini
├── otel-collector/config.yaml
├── tempo/tempo.yaml
├── grafana/
│   ├── provisioning/datasources/datasources.yml
│   ├── provisioning/dashboards/dashboards.yml
│   ├── provisioning/alerting/rules.yml       # Stale KIO alert (Contract §2.3)
│   └── dashboards/                       # overview, kio1..13 (generated), others
├── langfuse/                                 # ClickHouse/MinIO retention config (docs/data-retention.md)
├── kio-simulator/{kio_simulator.py,envelope.py,requirements.txt,Dockerfile}
├── metrics_api/                              # /api/metrics query API (read-only, 8081)
│   ├── {app.py,query.py,registry.py,stats.py,timefmt.py,victoriametrics.py}
│   └── registry_data.py                      # GENERATED — see scripts/
├── remote-kio/                              # connect a KIO from another machine
│   ├── {README.md,INTEGRATION.md,NETWORK.md}   # hub / own-module guide / networking
│   ├── with_script/{main.py,kio_otel.py,check_connectivity.py,requirements.txt,.env.example}
│   └── with_docker/{Dockerfile,docker-compose.yml,main.py,kio_otel.py,check_connectivity.py,…}
├── scripts/                                 # KPI table (d11_kpis.py), dashboard/registry generators, langfuse_retention.sh
├── tools/power_exporter.py                  # optional host GPU power/temperature exporter (real-LLM mode)
├── tests/{conftest.py,requirements-test.txt,kio_simulator_tests/,metrics_api_tests/}
└── docs/                                     # this documentation, plus the Integration Guide, technical report, KPI reference
```

## Design notes & decisions

- **Prometheus is intentionally absent** — VictoriaMetrics ingests via remote_write
  (push), which fits the contract's "KIOs never expose a scrape endpoint" rule.
- **Tempo** stores traces in monolithic mode with local-disk storage — sufficient for
  local dev; a production deployment would move to object storage (S3/GCS) and
  split Tempo's components.
- **Langfuse** is integrated (see [Langfuse](langfuse.md)) as a second stream
  parallel to metrics + logs + traces, evaluated
  against the v2 guide in [Design Decisions & v2 Guideline Evaluation](design-decisions.md).
- **Auth/TLS (§9.3): Bearer enforced, TLS not yet enabled.** The contract uses
  Bearer token + TLS on `:5317`/`:4318`. The collector's `bearertokenauth`
  extension (`otel-collector/config.yaml`) enforces the Bearer half on both
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
- **Grafana access:** anonymous visitors get the `Viewer` role, so dashboards
  are browsable without a login; editing, datasources, alerting and user
  management need the admin login. The admin login defaults to `admin` /
  `admin` (`GF_SECURITY_ADMIN_PASSWORD`) — set a real password in `.env`
  before any shared deployment.
