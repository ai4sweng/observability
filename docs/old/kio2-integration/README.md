# KIO2 (FocusTracer) — Real Module Integration Guide

This document is for the **FocusTracer** team (D1.1's KIO2 = "Bug Locate & Fix /
LLM Debugger"). Goal: hand the `kio2` identity over from the simulator that has
been generating data in its place so far (`kio2-sim`) to real FocusTracer runs,
connecting them to the central observability stack (OTel Collector →
VictoriaMetrics/VictoriaLogs/Tempo → Grafana, + Langfuse).

Reference documents:
- `docs/AI4SWENG_Observability_Teknik_Rapor_v1.5.docx` — overall architecture (Section 9.5-9.10: the Stale KIO alert, the NATS orchestration layer, D1.1 KPI status — now all of KIO2/KIO3/KIO4/KIO7, this handoff, dashboard adaptations, the pytest suite).
- `docs/AI4SWENG_KPI_Metrik_Referansi_v1.3.docx` — real project KPIs from D1.1 (v1.3: KIO7's (AI-SysDev) real D1.1 KPIs are now integrated too, in addition to kio2-sim/kio3/kio4 — all four KIOs D1.1 defines a KPI for are now covered).
- `kio-simulator/kio_simulator.py` — the **working reference implementation**. It already implements everything the contract requires (metrics/logs/traces/Langfuse pipeline setup, real Ollama calls, real GPU energy). Most of the code examples below can be lifted from here.

## 0) Decide first: where will it run?

Both scenarios are supported; which one you're in changes exactly one environment variable.

**A) A separate machine/network (FocusTracer runs in its own team's environment)**
The same pattern as `remote-kio/README.md` applies as-is: the architecture is
push-based, it doesn't matter where the KIO runs, as long as it can reach the
collector over the network.
- `OTEL_EXPORTER_OTLP_ENDPOINT` = the central machine's address, e.g. `http://<central-machine-ip>:4317`
- Verify before starting: `nc -zv <central-machine-ip> 4317`
- If you're not on the same network, a VPN like Tailscale/ZeroTier is the simplest fix.

**B) Joining our docker-compose network**
The service running FocusTracer is added to `observability/docker-compose.yml`
as a new service (like kio3/kio4), using
`OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317` — reachable by service
name since it's on the same Docker network.

Either way, there is **no instrumentation difference at all** in the code — the
only difference is this one endpoint variable.

**As of 2026-08, one more environment variable is mandatory (§9.3):** the
collector now requires a Bearer token (the `bearertokenauth` extension in
`otel-collector/config.yaml`), otherwise OTLP calls are silently rejected:
```bash
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <OTLP_BEARER_TOKEN value>
```
The token must match the `otel-collector` service's `OTLP_BEARER_TOKEN` in
`docker-compose.yml` exactly (or the override in the repo-root `.env`) — one
value shared across every KIO, not specific to your KIO. No code change is
needed; the OTel SDK reads this variable automatically (see
`kio_simulator.py`'s exporter setup, which never passes a `headers=`
parameter).

## 1) Mandatory identity attributes (Contract §1.2)

Every metric/log/trace must carry these attributes (via the OTel `Resource`
object, see `kio_simulator.py` around line ~255):

```python
resource = Resource.create({
    "service.name": "kio2",
    "service.version": "<FocusTracer version>",
    "kio.id": "kio2",                # FIXED — already safe from colliding with
                                      # kio2-sim, since the simulator was
                                      # renamed to "kio2-sim"; kio2 is now yours.
    "deployment.environment": "production",
    "llm": "<the model actually in use, e.g. qwen2.5:3b>",
    "task_type": "<based on the command, see below>",
})
```

Suggestion for `task_type`: make FocusTracer's CLI commands (`run`, `slice`,
`explain`, `reverse`, `replay`) the `task_type` value directly — the Contract
treats this field as free text, there's no fixed enum requirement.

## 2) FocusTracer-specific: what goes where

Unlike our simulator, FocusTracer is **not a continuously running service** —
every `focustracer <command>` call is a short-lived process (exception: the
`gui` command, which stays up via FastAPI/Uvicorn). So the most natural model
for telemetry here is "one request = one CLI command."

### `cli.py` → `main()`
Set up the OTel pipeline once at process start (the metrics + logs + traces
exporters — the setup block in `kio_simulator.py`, around lines ~266-380, can
be copied almost as-is). Before the process exits (before `main()` returns),
remember to call `force_flush()`/`shutdown()` on the
`MeterProvider`/`LoggerProvider`/`TracerProvider` — in a short-lived process,
exiting before the `PeriodicExportingMetricReader`'s export interval elapses
loses your last batch of data (consider a shorter interval than the default
15000ms `EXPORT_INTERVAL_MS` for short commands, combined with
`force_flush()`).

Every command invocation should get a root span (`focustracer.command`,
attributes: `command`, `target_script`) plus
`kio.request.count`/`kio.request.duration_ms`, and on failure
`kio.request.error_count` (with a real exception/traceback, not random — see
the "real error ≠ simulated error" distinction we recently fixed on our side,
the pattern in `kio_simulator.py`'s `_call_ollama_real()`).

### `explain_cmd()` (`cli.py`) / `explain_slice()` (`core/explain.py`) — **real LLM metrics come from here**

This is the one place a real Ollama call actually happens
(`agent.analyze_trace()` → `OllamaClient.generate()` → `POST /api/generate`).
This is exactly where your request to "capture metrics directly from the LLM
side" is answered:

**There's currently a data loss here:** `OllamaClient.generate()`
(`agent/ollama_client.py:30-72`) reads Ollama's returned `eval_count`,
`eval_duration`, and `prompt_eval_count` fields and discards them, returning
only the `response` text. These three fields are the source of real tokens/sec
— exactly what our own `kio_simulator.py`'s `_call_ollama_real()` does. The
suggested small change (without breaking the interface, without changing
`BaseAIAgent.generate()`'s signature):

```python
# ollama_client.py — in addition to current behavior, once generate() succeeds:
self.last_usage = {
    "input_tokens": data.get("prompt_eval_count", 0),
    "output_tokens": data.get("eval_count", 0),
    "duration_s": eval_duration_s,          # data["eval_duration"] / 1e9
    "tokens_per_second": eval_count / eval_duration_s if eval_duration_s else 0.0,
}
```

Right after the `explain_cmd()` call, reading `agent.last_usage` lets you
publish these metrics with real values (names identical to ours, so dashboards
work directly):

- `kio.llm.token_count` (counter; `direction=input|output`)
- `kio.llm.tokens_per_second` (histogram)
- `kio.llm.cost_usd` (probably 0 for local Ollama — no API cost, your call)
- `kio.llm.energy_joules` — for real GPU power, you can copy
  `kio_simulator.py`'s `_read_gpu_power_watts()` + `_measure_energy_during()`
  functions as-is (they read via NVML or `tools/power_exporter.py`, and fall
  back silently to the estimated formula if neither is available — never crash).
- `source` label: can stay fixed at `real` as it does on our side (you're
  always connecting to a real Ollama, there's no simulation path) — but if the
  Ollama call genuinely fails (`requests.exceptions.*`), record it on
  `kio.request.error_count` with the **real** `error_type`, don't manufacture a
  fake "success" result.

For `OpenCodeClient` (the other agent), the same token/timing information is
probably not available (CLI-based, a different interface) — in that case, only
wall-clock duration (`kio.request.duration_ms`) is real; leaving a `None`/
omitted field for tokens/sec is better than making up a random number.

### `slice_trace_cmd()` (`cli.py`) — **the WP3 "Dynamic slicing success rate" can become real here**

This KPI is currently entirely simulated on our side
(`random.uniform(0.75, 0.97)`). But `slice_trace()` returns a real result —
after `model, result = slice_trace(...)`, you can derive a **real**
success/failure signal from whether the `nodes` list produced by
`slice_result_to_dicts(model, result)` is empty, or from the existing check at
`cli.py:1220` (the "no reads" error):

```python
success = bool(nodes)  # or a more meaningful "was a slice found" criterion for your project
slicing_success_hist.record(1.0 if success else 0.0, labels)  # kio.slicing.success_rate
```

This would be the **first time** D1.1's WP3 task metric (target ≥85%) is filled
with real data — we'd recommend prioritizing it.

### An important boundary on fix@1 (KPI/"accuracy") — your own CLAUDE.md confirms this too

Your project's own `CLAUDE.md` states plainly: *"LLM-based fix generation is
out of KIO2 scope (it belongs to KIO7); the `explain` command stays as a
standalone convenience, and in the KIO2 integration the LLM step is a hand-off
to KIO7."*

In other words: FocusTracer produces an **explanation** (`explain`); it does
**not** apply and test a fix. fix@1's definition ("did the first proposed
patch actually pass a test") can therefore never come from FocusTracer itself
as real data — that's KIO7's job. For now `kio.fix.attempt_count` will remain
a placeholder on our side; we do **not** expect this metric from FocusTracer as
part of the KIO2 integration. We'd suggest clarifying this within your team to
avoid confusion.

A similar boundary applies to `kio.bugfix.duration_hours` (KPI 6.1) and
`kio.issue.resolution_hours` (KPI 1.2): these likely measure the **end-to-end**
resolution time of an issue/ticket (a project-management-level KPI in D1.1), a
different concept from the duration of a single FocusTracer CLI command. What
FocusTracer can realistically provide is "technical analysis time" (`run` +
`slice` + `explain` combined wall-clock) — a component that feeds into the KPI,
not the KPI itself. We'd suggest clarifying this within your team too before
publishing it under the same name — we don't want to give the appearance of a
mislabeled "real KPI."

### Traces
Root span `focustracer.command`, with child spans that vary by command — e.g.
for `explain`: `slice` → `llm_call` (attributes: `llm.model`,
`llm.tokens.input/output`) → `format_output`. `kio_simulator.py`'s
`emit_trace()` (around line ~418) shows how to construct spans with real
timestamps (`start_time`/`end_time`, no extra `sleep`) — the same pattern
applies.

### Logs
Free text (e.g. the explanation `explain` produces, `slice`'s criterion
summary) goes to VictoriaLogs via OTLP logs — the logger setup in
`kio_simulator.py` (around lines ~357-367) can be copied directly.

### Langfuse (optional but recommended)
Since `explain` is a real LLM call, it makes sense to also write it to
Langfuse — `emit_langfuse_trace()` (`kio_simulator.py` around line ~472) gives
the same `session_id` to both the OTel trace and Langfuse, making the two
correlatable by eye. `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_BASE_URL`
— same values used by the kio3/kio4 services in the main
`docker-compose.yml` (Langfuse is a single project, every KIO shares the same
key).

## 3) Testing / verification

1. `focustracer check-agent --agent ollama --model qwen2.5:3b` — your LLM
   connection should already be working, this step doesn't change.
2. Run a few `focustracer run` / `slice` / `explain` commands.
3. Grafana → **KIO Detail** → `kio2` should appear in the `KIO` dropdown
   (within 30-60 seconds, depending on your export interval). `kio2-sim` is
   still there too — you can compare them side by side.
4. The "LLM" panel should show your actual model name, "Avg tokens/sec" a real
   number, and "Error rate %" only real errors.
5. Follow `docker compose logs -f kio2` (or your own log output) for the
   "online" line and error/success logs.

## 4) What happens to kio2-sim?

The simulator (`kio2-sim`, in `docker-compose.yml`) keeps running in parallel
until FocusTracer is verified — for comparison/baseline purposes. Once the real
module is stable, removing it is a one-line `docker compose stop kio2-sim` (or
deleting the service from `docker-compose.yml`).

## 5) Note: the NATS orchestration layer (optional, doesn't affect you)

A NATS JetStream-based orchestration layer (Workflow API + Session Manager +
Planner) has been set up under `observability/orchestrator/` — `kio3`/`kio4`
are now triggered through it (see the technical report's Section 9.6). The
Planner's static routing table currently has no task type defined for
`kio2`/FocusTracer — your CLI-based execution model (short-lived process,
`focustracer <command>`) doesn't map one-to-one onto this trigger model. If you
want, adding a task type to `DEFAULT_ROUTING_TABLE` in
`orchestrator/planner.py` later, so your `focustracer` commands could be
triggered via the Workflow API, is something we could evaluate together — for
now it's not required, and your existing CLI flow can stay unchanged.
