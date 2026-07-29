# AI4SWENG Observability Stack


**Notion Documentation: [AI4SWENG Observability - Grafana Metrik Sistemi](https://app.notion.com/p/dawn-squash-710/Observability-Grafana-Metrik-Sistemi-3a619cd5a4d88093a1fdebd6ada75f5a)**

Local implementation of the **AI4SWENG Observability Integration Contract v1.0**.
KIO modules push telemetry over OTLP; the central platform stores it and Grafana
visualizes it. Everything runs locally via Docker Compose.




<div align="center">

  <img src="assets/architecture.png" width="600" alt="Architecture diagram" />

</div>


```
                 OTLP/gRPC (:4317)
  KIO2 ┐                              ┌─ metrics ─► VictoriaMetrics (:8428) ─┐
  KIO3 ├──►  OpenTelemetry Collector ─┤─ logs ────► VictoriaLogs    (:9428) ─┼─► Grafana (:3000)
  KIO4 ┘                              └─ traces ───► Tempo           (:3200) ─┘
       │
       └──────────────────────────────► Langfuse (:3001, HTTPS, parallel stream)
       (dummy telemetry)
```

## What's inside

| Component | Role | Port |
|-----------|------|------|
| `otel-collector` | Single OTLP ingestion gateway; fans metrics → VictoriaMetrics, logs → VictoriaLogs, traces → Tempo | 4317 (gRPC), 4318 (HTTP) |
| `victoriametrics` | Metrics store (Prometheus-compatible, no Prometheus needed) | 8428 |
| `victorialogs` | Store for unstructured / string telemetry | 9428 |
| `tempo` | Trace store (monolithic mode, local disk) — powers the "işlem sırası" waterfall view | 3200 |
| `grafana` | Dashboards (auto-provisioned) | 3000 |
| `langfuse-web` / `langfuse-worker` | Self-hosted Langfuse — LLM-specific prompt/completion/cost tracing, a stream parallel to and independent of OTel | 3001 (UI+API), internal 3030 (worker) |
| `postgres` / `clickhouse` / `redis` / `minio` | Langfuse's own required backing stores (relational DB, trace analytics, queue, blob storage) — not something we chose, this is Langfuse's mandated self-host footprint | internal only (127.0.0.1-bound except minio :9090) |
| `kio2` / `kio3` / `kio4` | KIO simulators pushing contract-compliant dummy telemetry, dual-written to OTel + Langfuse | — |

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
- **AI4SWENG — KIO Detail**: pick a KIO from the `KIO` dropdown; per-KIO metrics, a
  live panel of that KIO's unstructured string logs, and a **traces** section — a
  table of recent traces plus a waterfall view of the selected one (copy a Trace ID
  from the table into the `trace_id` box above it).

Give it ~30–60 seconds after startup for the first metrics and traces to land.

Separately, **http://localhost:3001** opens the Langfuse UI (login
`admin@ai4sweng.local` / `ai4sweng-admin`, auto-created on first boot — see
Headless Initialization below). Langfuse takes noticeably longer to become ready
than the rest of the stack (~2–3 minutes: it's booting Postgres + ClickHouse +
Redis + MinIO underneath it) — the KIOs will log harmless connection-refused
retries against it until it's up, then start landing traces automatically.

Tear down (and wipe data): `docker compose down -v`

## The three KIO simulators

| KIO | LLM | Task type | Notes |
|-----|-----|-----------|-------|
| kio2 | `qwen2.5:3b` | code-analysis | also reports **real** repo line / directory / file counts of its own source; its trace has an extra `repo_scan` span |
| kio3 | `llama3.1:8b` | test-generation | random dummy telemetry |
| kio4 | `gpt-4o-mini` | debug | random dummy telemetry (non-zero cost) |

All values are randomly generated — no real LLM is invoked. Adjust LLMs, task types,
and rates in `docker-compose.yml`, or edit `kio-simulator/kio_simulator.py`.

Each simulated request also emits a **trace**: a root `kio.request` span with
sequential child spans — `prepare_prompt` → `llm_call` → `postprocess` (code-analysis
KIOs add a leading `repo_scan` span). Timestamps are set explicitly to mirror the
request's real latency breakdown, so the waterfall in Grafana reflects the actual
timing split, not a fixed mock.

## Running a KIO on a different machine

See **[`remote-kio/README.md`](remote-kio/README.md)** for a copy-paste-ready package
that runs a single KIO on a separate machine while the rest of the stack (collector,
databases, Grafana) keeps running wherever it already is. The architecture is push-based
by design, so this needs no code change — only pointing `OTEL_EXPORTER_OTLP_ENDPOINT`
at the central machine.

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

## Traces ("işlem sırası")

Each request's `kio.request` trace (with its `prepare_prompt` / `repo_scan` /
`llm_call` / `postprocess` children) is exported to **Tempo**. The KIO Detail
dashboard exposes it two ways: a TraceQL-backed table of recent traces for the
selected KIO, and a waterfall panel that renders the full span sequence once you
paste a Trace ID from that table into the `trace_id` variable. If the waterfall
panel ever comes up empty, the same Trace ID can always be opened via
**Explore → Tempo** in Grafana as a fallback.

## Gerçek proje KPI'ları (D1.1)

`docs/AI4SWENG_KPI_Metrik_Referansi.docx` — projenin resmi Proje Yönetim El Kitabı'ndan (D1.1)
alınan tüm KPI'ların (1.1–9.2) ve iş-paketi/görev seviyesi metriklerin tam kataloğu, ve
hangi KIO'ya hangi KPI'nın bağlı olduğunun haritası.

Bu sürümde yalnızca **KIO2** (D1.1'de "Bug Locate & Fix / LLM Debugger" — FocusTracer'ın
yaptığı işin ta kendisi) gerçek KPI'larla entegre edildi. `docker-compose.yml`'de kio2'ye
`KIO_REAL_KPI_ROLE=bugfix` env değişkeni set edilince, `kio_simulator.py` şu dört ek metriği
yayınlıyor (isim/birim doğrudan D1.1'den, değerler henüz simüle):

- `kio_bugfix_duration_hours` — KPI 6.1 (Bug-fix time)
- `kio_issue_resolution_hours` — KPI 1.2 (Issue resolution speed)
- `kio_slicing_success_rate` — WP3 görev metriği (Dynamic slicing success rate, hedef ≥%85)
- `kio_issue_customer_reported_count` — KPI 6.2 (Customer-reported issues)

KIO Detail dashboard'unda yeni bir "D1.1 Gerçek Proje KPI'ları" bölümü bu dört metriği
gösterir (yalnızca kio2 seçiliyken veri dolu gelir). Diğer KIO'lar (KIO3, KIO4, KIO7...)
için aynı yöntem — gerçek isim/birim, KIO'ya özel env-var ile etkinleştirme — ileride
sırayla uygulanacak; bkz. referans dokümanının "Diğer KIO'lar" bölümü.

## Gerçek LLM entegrasyonu (KIO2, opsiyonel — NVIDIA GPU gerekir)

KIO2'nin gerçek modülü (FocusTracer) henüz hazır olmadığı için, o hazırlanana kadar en
azından **gerçek bir LLM'i gerçekten çalıştırıp** tokens/sec ve GPU enerjisini gerçek
ölçmek istersen bu yol kapalı-varsayılan (`KIO2_REAL_LLM_ENABLED: "false"`) olarak
hazır. Açtığında değişen şey:

- **Gerçek olan:** tokens/sec, input/output token sayısı ve latency (Ollama'nın kendi
  `eval_count`/`eval_duration`'ından), GPU enerjisi (gerçek NVML güç okumasının çağrı
  süresi boyunca integrali), **ve artık error rate de** — Ollama çağrısı gerçekten
  başarısız olursa (unreachable/timeout/HTTP hatası) bu, rastgele bir zar değil,
  `kio.request.error_count`'a gerçek `error_type` ile yazılan gerçek bir hata olarak
  sayılıyor. Eskiden gerçek yol açıkken bile hata oranı hâlâ `%7` rastgele zardan
  geliyordu ve gerçek bir Ollama kopması, arkasından sanki hiçbir şey olmamış gibi
  taze bir sahte "başarılı" istekle örtbas ediliyordu — bu düzeltildi
  (`_call_ollama_real()` artık başarı/başarısızlığı açıkça ayırt eden bir sözlük
  döndürüyor, `simulate_request()`'te üç yollu dallanma: gerçek-başarı / gerçek-hata /
  yol-tamamen-kapalı).
- **Hâlâ simüle olan:** fix@1 (`kio.fix.attempt_count`, `outcome=success|failure`) —
  çünkü "doğru düzeltme" için gerçek bir hata + gerçek bir test çalıştırması gerekiyor,
  bu da FocusTracer'ın işi. FocusTracer hazır olunca `kio_simulator.py`'daki
  `_evaluate_fix_success()` fonksiyonunun içini değiştirmek yeterli olacak — metrik adı/şekli aynı kalır.
  Ayrıca `kio.request.accuracy` (genel Contract metriği, D1.1/fix@1'den bağımsız) da aynı
  sebeple simüle kalıyor — bir LLM cevabının "doğruluğunu" otomatik ölçecek bir referans/test
  seti yok.

Kurulum:
1. Bu bilgisayarda (container içinde değil) Ollama kurulu olsun ve model çekilmiş olsun: `ollama pull qwen2.5:3b`
2. (Opsiyonel, gerçek enerji için) `pip install nvidia-ml-py` sonra `python tools/power_exporter.py` çalıştır — Windows host'ta NVML'den okuyup `http://localhost:9400/power` üzerinden JSON servis eder (`{"watts": 87.3}`). Bunu çalıştırmazsan enerji eski tahmini formüle döner, sistem yine de çalışır.
3. `docker-compose.yml`'de kio2'nin `KIO2_REAL_LLM_ENABLED` değerini `"true"` yap, `docker compose up -d --build kio2` ile yeniden başlat.

Neden bu yol (container'a GPU passthrough değil, host'ta native Ollama + ayrı bir
power-exporter script'i)? Çünkü Linux container'ların Windows host'un GPU'suna
görünürlüğü yok (passthrough ayrıca kurulmadıkça); Ollama zaten native Windows'ta GPU'yu
doğrudan kullanabiliyor, bu yüzden en az sürtünmeli yol bu. `_read_gpu_power_watts()`
önce `GPU_POWER_EXPORTER_URL`'i, sonra (varsa) container'ın kendi NVML'ini dener, ikisi
de yoksa sessizce eski tahmini değere düşer — hiçbir durumda simülatör çökmez.

## Langfuse (LLM-specific tracing)

A second, parallel telemetry stream — independent of the OTel pipeline — dedicated
to prompt/completion/cost tracking. Each KIO wraps its simulated LLM call in a
Langfuse span+generation (same `session_id` as the OTel trace, so a human can
correlate the two), while metrics/logs/traces above keep flowing through OTel
exactly as before. If Langfuse is unreachable or misconfigured, the KIO logs a
warning and keeps running unaffected — this stream can never take down the rest
of the stack (see `kio_simulator.py`'s `emit_langfuse_trace()`).

Self-hosted via `LANGFUSE_INIT_*` "headless initialization" env vars on
`langfuse-web`, so the org/project/API-keys exist automatically on first boot —
no manual UI setup step, no copy-pasting keys before the KIOs can connect.

## V2 Guideline Değerlendirmesi (2026-07-23)

A candidate engineer's proposed **v2 Observability Integration Guide** was reviewed
against this implementation. It is a candidate's proposal, not a finalized
contract — the decisions below are ours, made after comparing the two documents:

- **Log collection: kept our design (active OTLP push → VictoriaLogs), rejected
  v2's passive stdout-scrape → Loki model.** v2 assumes the collector can reach
  every KIO's container filesystem/stdout directly (e.g. via a mounted Docker log
  directory). That assumption breaks for a KIO running on a separate machine —
  exactly the `remote-kio/` scenario already implemented and tested in this repo
  (KIO5 over Tailscale). An OTLP-push model works uniformly regardless of where a
  KIO physically runs; a scrape-based model does not. Decision: OTLP push stays.
- **Langfuse: added**, per direct request (senior). Self-hosted stack (Postgres +
  ClickHouse + Redis + MinIO + langfuse-web/-worker) — see "Langfuse" section
  above. This is a materially heavier addition than everything else in this repo
  combined (6 extra containers vs. our previous 8 total), so it's worth being
  explicit about the trade-off for the report: it buys prompt/completion-level
  replay and LLM cost analytics that Tempo's generic spans don't provide. If the
  team ultimately doesn't need prompt-level debugging, this whole sub-stack (and
  its 4 backing services) can be removed without touching the OTel pipeline at
  all — it was deliberately kept as an isolated, independently-failing addition.
- **NATS JetStream / Session Manager / PostgreSQL lineage / Workflow API:
  deferred, not implemented.** These describe how KIOs get *triggered* (an
  orchestration layer), not how they're *observed* — arguably a different team's
  concern, out of scope for this observability workstream. Open question worth
  discussing further: does the team need this orchestration layer modeled here at
  all, or is it assumed to exist elsewhere and out of scope by design? Revisit
  once that's clarified.
- **Confirmed already-compliant, no change needed:** the 7 mandatory metrics
  (names/types/units), resource attributes, the low-cardinality rule (session IDs
  never used as metric labels — only in trace/log metadata, exactly as v2 also
  specifies), and the metrics+traces-over-OTLP/gRPC transport.
- **Minor, cheap-to-adopt items not yet applied:** v2's 15s metric export interval
  (we currently use 5s — fine for this KIO count, worth revisiting at higher
  scale) and a `session_id` Grafana dashboard filter variable (we currently only
  filter by `kio_id`).

## Design notes & decisions

- **Prometheus is intentionally absent** — VictoriaMetrics ingests via remote_write
  (push), which fits the contract's "KIOs never expose a scrape endpoint" rule.
- **Tempo** stores traces in monolithic mode with local-disk storage — sufficient for
  local dev; a production deployment would move to object storage (S3/GCS) and
  split Tempo's components.
- **Langfuse** is now integrated (see "Langfuse" section above) as a second stream
  parallel to metrics + logs + traces, added per direct request and evaluated
  against v2 in the section above.
- **Auth/TLS**: the contract uses Bearer token + TLS on `:4317`. For local dev the
  collector listens insecure. To exercise the real path, add a `bearertokenauth`
  extension to the collector and set `OTEL_EXPORTER_OTLP_HEADERS` on the KIOs (the
  OTel SDK picks this env var up automatically — no code change needed).
- **Remote KIOs**: fully supported today via the push architecture — see
  `remote-kio/README.md`. No code change is required, only environment variables.

## Layout

```
observability/
├── docker-compose.yml
├── otel-collector/config.yaml
├── tempo/tempo.yaml
├── grafana/
│   ├── provisioning/datasources/datasources.yml
│   ├── provisioning/dashboards/dashboards.yml
│   └── dashboards/{ai4sweng-overview,ai4sweng-kio}.json
├── kio-simulator/{kio_simulator.py,requirements.txt,Dockerfile}
├── remote-kio/{docker-compose.yml,.env.example,README.md}
└── docs/AI4SWENG_Observability_Teknik_Rapor.docx
```
