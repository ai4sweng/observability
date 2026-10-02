# Design Decisions & v2 Guideline Evaluation

Part of the [AI4SWENG Observability Stack](../README.md) documentation. See the
[documentation index](README.md) for the full set of guides.

## V2 Guideline Evaluation

The **v2 Observability Integration Guide** (now v2.3, normative) was evaluated
against this implementation. The decisions below record where the stack
follows it, where it deviates, and why:

- **Log collection: kept our design (active OTLP push → VictoriaLogs), rejected
  v2's passive stdout-scrape → Loki model.** v2 assumes the collector can reach
  every KIO's container filesystem/stdout directly (e.g. via a mounted Docker log
  directory). That assumption breaks for a KIO running on a separate machine —
  exactly the [`remote-kio/`](../remote-kio/) scenario already implemented and tested in this repo
  (KIO5 over Tailscale). An OTLP-push model works uniformly regardless of where a
  KIO physically runs; a scrape-based model does not. Decision: OTLP push stays.
- **Langfuse: added.** Self-hosted stack (Postgres +
  ClickHouse + Redis + MinIO + langfuse-web/-worker) — see
  [Langfuse](langfuse.md). This is a materially heavier addition than everything
  else in this repo combined (6 of the stack's 14 containers), so the
  trade-off is worth stating: it buys
  prompt/completion-level replay and LLM cost analytics that Tempo's generic
  spans don't provide. If the team ultimately doesn't need prompt-level
  debugging, this whole sub-stack (and its 4 backing services) can be removed
  without touching the OTel pipeline at all — it was deliberately kept as an
  isolated, independently-failing addition.
- **Confirmed already-compliant, no change needed:** the 7 mandatory metrics
  (names/types/units), resource attributes, the low-cardinality rule (session IDs
  never used as metric labels — only in trace/log metadata, exactly as v2 also
  specifies), and the metrics+traces-over-OTLP/gRPC transport.
- **Adopted:** v2's 15s metric export interval (was 5s — the
  `EXPORT_INTERVAL_MS` default for every KIO simulator in `docker-compose.yml`/
  `.env.example`/`kio_simulator.py`'s own fallback; no
  panel changes needed, existing `rate(...[15m])` windows comfortably contain
  multiple 15s samples) and a `$session_id` Grafana dashboard filter variable
  (textbox, regex, default `.*` = all — wired into the log-stream panel via
  VictoriaLogs LogsQL's `field:~"regex"` syntax and the Tempo trace table via
  TraceQL's `=~` operator).
  Metrics themselves still never carry `session_id` as a label (low-cardinality
  rule) — the new variable only filters the log/trace panels, matching v2's own
  metric-label rules.

See also [Architecture & Components](architecture.md#design-notes--decisions)
for the standing design notes (Prometheus, Tempo, Langfuse, Auth/TLS, remote
KIOs, Grafana access) these decisions feed into.
