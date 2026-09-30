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

Two simulators run: **kio2-sim** and **kio7-sim**. Every simulator's `kio_id`
ends in `-sim`, so it can never be confused with a real module reporting under the
plain `kioN` id (`kio2` and `kio7` stay reserved for those). Both run on their own
internal timer.

| KIO | LLM | Task type | Notes |
|-----|-----|-----------|-------|
| kio2-sim | `qwen2.5:3b` | code-analysis | also reports **real** repo line/dir/file counts; **real Ollama tok/s + real GPU energy/temperature** (`KIO2_REAL_LLM_ENABLED=true`, see [Real LLM Integration](real-llm-integration.md)); D1.1 KPI 1.2 + 6.1 + 6.2 (simulated, `KIO_REAL_KPI_ROLE=bugfix`). Shown on the KIO2 dashboard |
| kio7-sim | `claude-sonnet` | ai-sysdev (D1.1's KIO7) | random dummy telemetry; D1.1 KPI 4.1 + 5.1 + 7.1 + 9.1 + 9.2 (simulated, `KIO_REAL_KPI_ROLE=ai-sysdev`). Shown on the KIO7 dashboard |

### Why only two

The stack used to run six simulators. It runs two to keep the container count
and Langfuse's disk growth down
(see [Langfuse Data Retention](data-retention.md)). kio7-sim was kept alongside
kio2-sim because D1.1 Table 3 ties KIO7 to 13 of the 16 KPIs, the most of any
KIO, and its `ai-sysdev` role emits the most KPIs of any single role.

The other four roles still exist in `kio_simulator.py` and can be brought back
by copying the `kio7-sim` block in `docker-compose.yml` with a different
`KIO_ID` (keep the `-sim` suffix), `KIO_TASK_TYPE` and `KIO_REAL_KPI_ROLE`:

| Role (`KIO_REAL_KPI_ROLE`) | D1.1 KIO | D1.1 KPIs emitted |
|---|---|---|
| `nlp-requirements` | KIO3 | 1.1 + 3.1 |
| `architecture-to-code` | KIO4 | 1.1 + 3.1 + 3.2 |
| `green-deploy` | KIO8 | 2.1 + 2.2 + 8.3 |
| `adoption` | KIO13 | 8.1 + 8.2 |

A re-added `kioN-sim` also needs `N: ["kioN-sim", "kioN"]` in `KIO_LABELS` in
`scripts/generate_kio_dashboards.py` (then re-run it) so the KIO's dashboard
selects it, and an entry in the `ai4sweng-others.json` exclusion regex.

With only these two running, the KPIs of the dropped roles (1.1, 2.1, 2.2, 3.1,
3.2, 8.1, 8.2, 8.3) show "—" on their dashboards — expected, since no simulator runs those
roles. Adjust LLMs, task types and rates in
`docker-compose.yml`, or edit `kio-simulator/kio_simulator.py`.

## Real vs. Simulated Data Map

Which panel/metric is real vs. simulated (dummy), and when — all distinguishable
in Grafana via the `source=real|simulated` label too (green=real, orange=simulated,
see the "Data source" panel):

| Metric / Panel | kio2-sim (`KIO2_REAL_LLM_ENABLED=false`) | kio2-sim (`=true`) | kio7-sim |
|---|---|---|---|
| tok/s, `kio.llm.tokens_per_second` | simulated | **real** (Ollama `eval_count/eval_duration`) | always simulated |
| Energy (W/J), `kio.llm.energy_joules` | simulated (fixed per-model coefficient) | **real** (NVML / `tools/power_exporter.py`) | always simulated |
| GPU temperature, `kio.llm.gpu_temperature_celsius` | no data ("No data") | **real** (if NVML/power-exporter is reachable) | no data (no GPUs) |
| Error rate, `kio.request.error_count` | simulated (~7% random) | **real** (actual Ollama success/failure) | always simulated (~7% random) |
| Repo line/directory/file counts | **real** (scans its own source) | **real** | not applicable (not code-analysis) |
| Accuracy / fix@1, `kio.request.accuracy` | always simulated | always simulated (even with real Ollama) | always simulated |
| D1.1 KPIs (bug-fix time, issue resolution, slicing success, customer-reported) | always simulated (`KIO_REAL_KPI_ROLE=bugfix`) | always simulated | not applicable (different role — see [Real Project KPIs](kpis.md)) |
| D1.1 KPIs (dev productivity, time-to-market, cost saving, refactoring/tech-debt reduction) | not applicable | not applicable | always simulated (`KIO_REAL_KPI_ROLE=ai-sysdev`) |
| D1.1 KPIs of the dropped roles (codegen speed, code quality, review score, energy, cross-arch build, adoption) | not emitted — no simulator currently runs those roles (see above) | not emitted | not emitted |
| Cumulative CO2e (estimated) | estimate derived from simulated energy | estimate derived from real energy (still an estimate, not a real carbon measurement) | estimate derived from simulated energy |

fix@1 and every D1.1 project-level KPI never becomes "real" on any KIO, because
their real source is the actual real module in question — none of the KIOs has one
connected yet. Until a real module connects under its own
`kio.id` (see [Connecting a KIO](remote-connectivity.md)), everything produced
here stays deliberately simulated — "clearly labeled dummy data" was chosen
over "empty panel while waiting."

Each simulated request also emits a **trace**: a root `kio.request` span with
sequential child spans — `prepare_prompt` → `llm_call` → `postprocess` (code-analysis
KIOs add a leading `repo_scan` span). Timestamps are set explicitly to mirror the
request's real latency breakdown, so the waterfall in Grafana reflects the actual
timing split, not a fixed mock. See [Metrics Reference & Query API](metrics-reference.md#traces)
for how to view them.
