# Design Decisions & v2 Guideline Evaluation

Part of the [AI4SWENG Observability Stack](../README.md) documentation. See the
[documentation index](README.md) for the full set of guides.

## V2 Guideline Evaluation (2026-07-23)

A candidate engineer's proposed **v2 Observability Integration Guide** was reviewed
against this implementation. It is a candidate's proposal, not a finalized
contract — the decisions below are ours, made after comparing the two documents:

- **Log collection: kept our design (active OTLP push → VictoriaLogs), rejected
  v2's passive stdout-scrape → Loki model.** v2 assumes the collector can reach
  every KIO's container filesystem/stdout directly (e.g. via a mounted Docker log
  directory). That assumption breaks for a KIO running on a separate machine —
  exactly the [`remote-kio/`](../remote-kio/) scenario already implemented and tested in this repo
  (KIO5 over Tailscale). An OTLP-push model works uniformly regardless of where a
  KIO physically runs; a scrape-based model does not. Decision: OTLP push stays.
- **Langfuse: added**, per direct request (senior). Self-hosted stack (Postgres +
  ClickHouse + Redis + MinIO + langfuse-web/-worker) — see
  [Langfuse](langfuse.md). This is a materially heavier addition than everything
  else in this repo combined (6 extra containers vs. our previous 8 total), so
  it's worth being explicit about the trade-off for the report: it buys
  prompt/completion-level replay and LLM cost analytics that Tempo's generic
  spans don't provide. If the team ultimately doesn't need prompt-level
  debugging, this whole sub-stack (and its 4 backing services) can be removed
  without touching the OTel pipeline at all — it was deliberately kept as an
  isolated, independently-failing addition.
- **NATS JetStream / Session Manager / PostgreSQL lineage / Workflow API:
  implemented (2026-08), then disabled by default (it belongs to KIO1 — see
  [Orchestration Layer](orchestration.md); kept in `orchestrator/` as reference, its services
  commented out in `docker-compose.yml`).** Originally deferred as "a different team's
  concern" — reversed after an explicit decision to accelerate this. See
  [Orchestration Layer](orchestration.md) for the architecture and `orchestrator/` for the
  code. Scope is stated there too: the Planner is a static routing table, not a
  full LangGraph workflow graph. Tested at the logic level (fake pub/sub + SQLite,
  no Docker available in the dev sandbox this was built in) — kio3/kio4 are wired
  to it; kio2-sim stays on its internal timer so the real-Ollama demo isn't
  disrupted. A live `docker compose up` pass against the real NATS/Postgres is
  the remaining verification step — run `python scripts/verify_orchestration.py`
  after bringing the stack up (see [Orchestration verification](orchestration.md#orchestration-verification)); it drives
  the whole round trip (`POST /workflow/run` -> NATS -> kio3 -> `kio.results.kio3`
  -> Planner -> Postgres lineage) and prints exactly which hop failed if it doesn't.
- **Confirmed already-compliant, no change needed:** the 7 mandatory metrics
  (names/types/units), resource attributes, the low-cardinality rule (session IDs
  never used as metric labels — only in trace/log metadata, exactly as v2 also
  specifies), and the metrics+traces-over-OTLP/gRPC transport.
- **Adopted (2026-08):** v2's 15s metric export interval (was 5s — bumped across
  all 6 KIO simulators' `EXPORT_INTERVAL_MS` default in `docker-compose.yml`/
  `.env.example`/`kio_simulator.py`'s own fallback; no
  panel changes needed, existing `rate(...[15m])` windows comfortably contain
  multiple 15s samples) and a `$session_id` Grafana dashboard filter variable
  (textbox, regex, default `.*` = all — wired into the log-stream panel via
  VictoriaLogs LogsQL's `field:~"regex"` syntax and the Tempo trace table via
  TraceQL's `=~` operator; the LogsQL syntax is unverified against a live
  VictoriaLogs instance, first real `docker compose up` should confirm it).
  Metrics themselves still never carry `session_id` as a label (low-cardinality
  rule) — the new variable only filters the log/trace panels, matching v2's own
  metric-label rules.

See also [Architecture & Components](architecture.md#design-notes--decisions)
for the standing design notes (Prometheus, Tempo, Langfuse, Auth/TLS, remote
KIOs, Grafana access) these decisions feed into.
