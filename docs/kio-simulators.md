# The KIO Simulators

Part of the [AI4SWENG Observability Stack](../README.md) documentation. See the
[documentation index](README.md) for the full set of guides.

> **⚠️ All of these are simulators, not real KIO modules.** Every KIO below
> emits telemetry that follows the real Integration Contract (see
> [Connecting a KIO](remote-connectivity.md)), but the values themselves are
> synthetic/randomly generated — they exist to exercise the platform and
> populate dashboards, not to represent a real team's real output. The only
> partial exception is `kio2-sim`'s optional real-Ollama LLM path (real
> tokens/sec, real GPU energy — see [Real LLM Integration](real-llm-integration.md));
> everything else, including every D1.1 KPI value, stays simulated until a
> real module connects under its own `kio.id` and takes over.

Only **kio2-sim** and **kio3** run by default. The other four (**kio4**, **kio7**,
**kio8**, **kio13**) are fully defined in `docker-compose.yml` but **commented out** —
uncomment any of them to enable it (e.g. to populate its D1.1 KPI panels). Every
simulator runs on its own **internal timer**; the NATS-driven trigger path is
disabled (it belongs to KIO1 — see [Orchestration Layer](orchestration.md)).

| KIO | Default | LLM | Task type | Notes |
|-----|---------|-----|-----------|-------|
| kio2-sim | **on** | `qwen2.5:3b` | code-analysis | also reports **real** repo line/dir/file counts; **real Ollama tok/s + real GPU energy/temperature** (`KIO2_REAL_LLM_ENABLED=true`, see [Real LLM Integration](real-llm-integration.md)). `kio2` itself is reserved for a real code-analysis module once one connects |
| kio3 | **on** | `llama3.1:8b` | nlp-requirements (D1.1's KIO3) | random dummy telemetry; D1.1 KPI 1.1 + 3.1 (simulated, `KIO_REAL_KPI_ROLE=nlp-requirements`) |
| kio4 | off (commented) | `gpt-4o-mini` | architecture-to-code (D1.1's KIO4) | random dummy telemetry (non-zero cost); D1.1 KPI 1.1 + 3.1 + 3.2 (simulated, `KIO_REAL_KPI_ROLE=architecture-to-code`) |
| kio7 | off (commented) | `claude-sonnet` | ai-sysdev (D1.1's KIO7) | random dummy telemetry; D1.1 KPI 4.1 + 5.1 + 7.1 + 9.1 + 9.2 (simulated, `KIO_REAL_KPI_ROLE=ai-sysdev`) — only the KPIs where KIO7 is a clear primary owner, not the full "most KPIs" D1.1 assigns it |
| kio8 | off (commented) | `gemini-1.5-pro` | green-deploy (D1.1's KIO8) | random dummy telemetry; D1.1 KPI 2.1 + 2.2 + 8.3 (simulated, `KIO_REAL_KPI_ROLE=green-deploy`) |
| kio13 | off (commented) | `gpt-4o-mini` | adoption (D1.1's KIO13) | random dummy telemetry; D1.1 KPI 8.1 + 8.2 (simulated, `KIO_REAL_KPI_ROLE=adoption`) — the only two D1.1 KPIs mapped to a single KIO with no co-owner |

`task_type` for kio3/kio4 was renamed 2026-08 from the arbitrary
`test-generation`/`debug` to match D1.1's actual KIO3/KIO4 identities, once
it became clear the D1.1 KPI traceability matrix's assignments (KPI 1.1, 3.1,
3.2) are keyed to those real roles, not to whatever this simulator happened
to call them first. kio7, kio8, kio13 were added the same way directly
against D1.1's KIO7/KIO8/KIO13 identities — kio8/kio13 close out D1.1's last
two uncovered KPIs (2.1/2.2/8.3 and 8.1/8.2). kio8/kio13 skip the NATS
trigger path since they have no real dispatch target behind them yet (see
the orchestrator's task_type routing table) — pure KPI simulators, timer-only.

kio3/kio4/kio7/kio8/kio13 are otherwise still random dummy data — no real LLM
is invoked for them. (A NATS-driven dispatch path via the Workflow API/Planner
also exists in the code, but it is **disabled by default** — that orchestration
layer belongs to KIO1, see [Orchestration Layer](orchestration.md).) Every simulator keeps
producing baseline demo data on its own internal timer regardless. Adjust LLMs,
task types, and rates in `docker-compose.yml`, or edit `kio-simulator/kio_simulator.py`.

## Real vs. Simulated Data Map

Which panel/metric is real vs. simulated (dummy), and when — all distinguishable
in Grafana via the `source=real|simulated` label too (green=real, orange=simulated,
see the "Data source" panel):

| Metric / Panel | kio2-sim (`KIO2_REAL_LLM_ENABLED=false`) | kio2-sim (`=true`) | kio3 / kio4 |
|---|---|---|---|
| tok/s, `kio.llm.tokens_per_second` | simulated | **real** (Ollama `eval_count/eval_duration`) | always simulated |
| Energy (W/J), `kio.llm.energy_joules` | simulated (fixed per-model coefficient) | **real** (NVML / `tools/power_exporter.py`) | always simulated |
| GPU temperature, `kio.llm.gpu_temperature_celsius` | no data ("No data") | **real** (if NVML/power-exporter is reachable) | no data (no GPUs) |
| Error rate, `kio.request.error_count` | simulated (~7% random) | **real** (actual Ollama success/failure) | always simulated (~7% random) |
| Repo line/directory/file counts | **real** (scans its own source) | **real** | not applicable (not code-analysis) |
| Accuracy / fix@1, `kio.request.accuracy` | always simulated | always simulated (even with real Ollama) | always simulated |
| D1.1 KPIs (bug-fix time, issue resolution, slicing success, customer-reported) | always simulated (kio2-sim only, `KIO_REAL_KPI_ROLE=bugfix`) | always simulated | not applicable (kio3/kio4 have a different role — see [Real Project KPIs](kpis.md)) |
| D1.1 KPIs (codegen duration, code quality, review score) | not applicable (these KPIs are kio3/kio4-specific) | not applicable | always simulated (`KIO_REAL_KPI_ROLE=nlp-requirements`/`architecture-to-code`) |
| D1.1 KPIs (dev productivity, time-to-market, cost saving, refactoring/tech-debt reduction) | not applicable (these KPIs are kio7-specific) | not applicable | not applicable — **kio7** only, always simulated (`KIO_REAL_KPI_ROLE=ai-sysdev`) |
| D1.1 KPIs (lifecycle energy, deploy energy efficiency, cross-arch build success) | not applicable (these KPIs are kio8-specific) | not applicable | not applicable — **kio8** only, always simulated (`KIO_REAL_KPI_ROLE=green-deploy`) |
| D1.1 KPIs (adoption rate, active usage, satisfaction/MOS) | not applicable (these KPIs are kio13-specific) | not applicable | not applicable — **kio13** only, always simulated (`KIO_REAL_KPI_ROLE=adoption`) |
| Cumulative CO2e (estimated) | estimate derived from simulated energy | estimate derived from real energy (still an estimate, not a real carbon measurement) | estimate derived from simulated energy |

fix@1 and every D1.1 project-level KPI never becomes "real" on any KIO, because
their real source is the actual real module in question — none of KIO2/KIO3/
KIO4 has one connected yet. Until a real module connects under its own
`kio.id` (see [Connecting a KIO](remote-connectivity.md)), everything produced
here stays deliberately simulated — "clearly labeled dummy data" was chosen
over "empty panel while waiting."

Each simulated request also emits a **trace**: a root `kio.request` span with
sequential child spans — `prepare_prompt` → `llm_call` → `postprocess` (code-analysis
KIOs add a leading `repo_scan` span). Timestamps are set explicitly to mirror the
request's real latency breakdown, so the waterfall in Grafana reflects the actual
timing split, not a fixed mock. See [Metrics Reference & Query API](metrics-reference.md#traces)
for how to view them.
