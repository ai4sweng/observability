# Connecting a KIO — Data Contract & Setup Guide

Part of the [AI4SWENG Observability Stack](../README.md) documentation. See the
[documentation index](README.md) for the full set of guides.

> **⚠️ Current status: everything running in this repo today is a simulator.**
> `kio2-sim`, `kio3`, `kio4`, `kio7`, `kio8` and `kio13` (`kio-simulator/`) emit
> synthetic, randomly-generated telemetry that follows the exact same contract
> a real KIO must follow — they exist to prove the platform works and to give
> every dashboard panel something to show, not to represent real KIO output.
> (The one partial exception: `kio2-sim` can optionally drive a real local
> Ollama LLM for real tokens/sec and GPU energy — see
> [Real LLM Integration](real-llm-integration.md) — everything else, including
> every D1.1 KPI value, stays simulated.) See
> [The KIO Simulators](kio-simulators.md#real-vs-simulated-data-map) for the
> full real-vs-simulated breakdown. **This page is the guide for connecting a
> real KIO module**, whether that's your own code today or a real module
> replacing a simulator later.

## 1. The model: push-based, one direction, no code change to move it

```
   YOUR MACHINE                          CENTRAL PLATFORM
 ┌────────────────┐   OTLP/gRPC     ┌──────────────────────────────────────┐
 │  Your KIO      │  ── :4317 ───▶  │  OTel Collector  (ingestion gateway) │
 │  + kio_otel.py │  (you push)     │      │                              │
 └────────────────┘                  │      ├─▶ VictoriaMetrics  (metrics) │
                                     │      ├─▶ VictoriaLogs     (logs)    │
                                     │      └─▶ Tempo            (traces)  │
                                     │              │                      │
                                     │              ▼                      │
                                     │           Grafana  (dashboards)     │
                                     └──────────────────────────────────────┘
```

Your KIO **pushes** telemetry to the collector. The central platform never
opens a connection toward a KIO, never scrapes it, never reaches into its
filesystem. The only thing that ever changes between "I'm testing on my own
laptop" and "I'm a different team connecting from across the world" is **one
address** (`OTEL_EXPORTER_OTLP_ENDPOINT`) — never your code.

## 2. Which scenario are you in?

Pick one. Both use the exact same code and the exact same data contract (§3) —
only the endpoint value differs.

### Scenario A — The central platform runs on a remote server (this is how we run it)

The observability stack (`docker compose up`) runs on a server somewhere —
your own VPS, an office machine, a cloud instance — and **your KIO runs
somewhere else**: your laptop, another team's machine, anywhere that can reach
that server over the network.

```bash
# On the machine running YOUR KIO:
OTEL_EXPORTER_OTLP_ENDPOINT=http://<server-address>:4317
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <token from the platform team>
```

`<server-address>` is:
- the server's LAN IP, if you're on the same local network as it, or
- the server's Tailscale IP (`100.x.y.z`), if you're on a different network —
  **this is the recommended path** for anything that isn't the same LAN; see
  [Networking](#5-networking-firewall-static-ip-tailscale) below.

Full walkthrough: [`remote-kio/INTEGRATION.md`](../remote-kio/INTEGRATION.md).
Firewall / address / VPN details: [`remote-kio/NETWORK.md`](../remote-kio/NETWORK.md).

### Scenario B — Everything runs on your own machine (local development / testing)

You're running `docker compose up` **and** your KIO code on the same machine —
no other team, no network to configure. This is how you'd try the platform out
before deploying it anywhere, or how a KIO team develops before pointing at the
real central server.

```bash
# Plain Python process on the host:
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer local-dev-otlp-token

# Your KIO ALSO running in a container on the same machine — "localhost"
# inside a container means the container itself, not your host, so use:
OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4317
```

`local-dev-otlp-token` is the default `OTLP_BEARER_TOKEN` baked into
`.env.example` — change it (and match the value on both sides) before any
shared or real deployment.

Runnable examples for both of these are ready to copy: [`remote-kio/with_script/`](../remote-kio/with_script/)
(plain Python) and [`remote-kio/with_docker/`](../remote-kio/with_docker/)
(containerized) — each `.env.example` documents exactly which endpoint value
to use for which case.

## 3. What your KIO must send — the data contract

This is the exact, language- and runtime-agnostic contract. Follow it whether
you use the Python helper this repo ships or your own OTel setup in any other
language.

### 3.1 Identity (resource attributes, set once)

Set via `OTEL_RESOURCE_ATTRIBUTES=key=value,key=value`. The collector promotes
these to metric labels (dots become underscores).

| Attribute | Required | Example | Becomes label |
|---|---|---|---|
| `service.name` | ✅ | `kio5` | `service_name` |
| `service.version` | ✅ | `1.0.0` | `service_version` |
| `kio.id` | ✅ | `kio5` | `kio_id` |
| `deployment.environment` | ✅ | `production` | `deployment_environment` |
| `llm` | optional | `claude-sonnet` | `llm` |
| `task_type` | optional | `code-analysis` | `task_type` |

`kio.id` **must be unique** across every running KIO and is assigned by the
central platform team — a collision silently merges two KIOs' data in Grafana.

### 3.2 The 7 mandatory metrics — names, types, units are fixed

Every metric also carries `kio.id` via the resource attribute above.

| Metric (OTLP name) | Type | Unit | Extra labels | Meaning |
|---|---|---|---|---|
| `kio.request.count` | Counter | `1` | `status` = `ok` \| `error` | Requests processed |
| `kio.request.duration_ms` | Histogram | `ms` | — | End-to-end latency distribution |
| `kio.request.error_count` | Counter | `1` | `error_type` = bounded enum¹ | Errors by category |
| `kio.llm.token_count` | Counter | `tokens` | `direction` = `input` \| `output` | LLM token usage |
| `kio.llm.cost_usd` | Counter | `USD` | — | Estimated total LLM cost |
| `kio.session.active_count` | UpDownCounter | `1` | — | In-flight sessions (goes up and down) |
| `kio.heartbeat` | Counter | `1` | — | Liveness, incremented every 60s |

¹ `error_type` must be a **small, bounded set** of strings you define (e.g.
`timeout`, `internal`, `rate_limit`) — never an unbounded value like an ID or
message (rule G3 below).

> **The heartbeat is critical.** A KIO silent for more than **120 seconds** is
> flagged **stale** — a real Grafana alert rule and an Overview panel show
> this automatically, not just a manual check.

In Prometheus/PromQL form (how they appear once ingested): dots become
underscores and there is no `_total` suffix — `kio.request.count` →
`kio_request_count`. See [Metrics Reference & Query API](metrics-reference.md)
for the full query-side detail (histograms expand into `_bucket`/`_sum`/`_count`,
example PromQL, the `/api/metrics` HTTP query API).

### 3.3 Naming & governance rules (G1–G7)

- **G1** Names follow `kio.<domain>.<metric>` and are **permanent** once released.
- **G2** Every metric/trace carries `kio.id` (traces also `session.id`).
- **G3** **No high-cardinality labels** — no unique IDs / URLs / emails as label
  values; use bounded enums (`session.id` is trace/log metadata, **not** a metric label).
- **G4** **No secrets / PII** in labels or traces.
- **G5** Bump `service.version` on every release.
- **G6** The mandatory metrics above are governed by the central team — fixed, not negotiable.
- **G7** **Anything beyond the mandatory 7 is self-service** — define your own
  metric as long as it obeys G1–G5.

### 3.4 What else your KIO can send (optional, self-service under G7)

Beyond the mandatory 7, everything below is **additive, not required** — a KIO
sends it only if it applies to that KIO's role:

- **Free-text logs** (LLM output snippets, analysis notes) as OTLP logs, stored
  in VictoriaLogs — see [Unstructured / string telemetry](metrics-reference.md#unstructured--string-telemetry).
- **Rich traces** (child spans for a request's own steps, e.g.
  `prepare_prompt` → `llm_call` → `postprocess`) — one root span per request is
  enough to satisfy the contract; a full waterfall is a nice-to-have.
- **Domain-specific metrics** this repo already defines under G7 as precedent —
  `kio.llm.tokens_per_second`, `kio.llm.energy_joules`,
  `kio.request.accuracy`, `kio.repo.line_count` (code-analysis KIOs only) — see
  the full optional set in [Metrics Reference](metrics-reference.md#metrics-integration-guide-21--optional-extras).
- **D1.1 project KPIs** — the 16 official KPIs (1.1–9.2) from the project's
  Management Handbook, e.g. bug-fix time, code quality, developer productivity,
  adoption rate. **Not every KIO reports every KPI** — each KPI has one or more
  owning KIOs per D1.1's assignment table, and a KIO only emits the KPIs it
  owns (set via `KIO_REAL_KPI_ROLE`). Full catalog, per-KIO breakdown, and
  metric names: [Real Project KPIs (D1.1)](kpis.md).
- **Langfuse traces** (prompt/completion/cost) — a second, fully optional
  stream, independent of OTel. See [Langfuse](langfuse.md).
- **NATS-driven dispatch** — only relevant if a KIO wants to be triggerable by
  the central `POST /workflow/run` call; a completely separate concern from
  telemetry. See [Orchestration Layer](orchestration.md).

## 4. Runnable examples & step-by-step setup

**[`remote-kio/`](../remote-kio/)** is the runnable starter kit implementing
everything in §3 — no Docker required, and no need to write OTel setup code
from scratch:

| Document / folder | Purpose |
|---|---|
| **[`remote-kio/README.md`](../remote-kio/README.md)** | Hub page — "pick a path" table |
| **[`remote-kio/INTEGRATION.md`](../remote-kio/INTEGRATION.md)** | Full guide: concept tour, step-by-step setup, deployment without Docker, troubleshooting — **start here for a from-scratch integration** |
| **[`remote-kio/NETWORK.md`](../remote-kio/NETWORK.md)** | Networking deep dive (§5 below) |
| **[`remote-kio/with_script/`](../remote-kio/with_script/)** | Copyable minimal kit, plain Python: `cp .env.example .env` → edit → `pip install -r requirements.txt` → `python main.py` |
| **[`remote-kio/with_docker/`](../remote-kio/with_docker/)** | Same demo, containerized: `cp .env.example .env` → edit → `docker compose up --build` |

Both examples push the seven mandatory metrics + a 60-second heartbeat + log
lines, driven entirely by `.env` — pick the endpoint value from §2 above for
your scenario. The two files that matter once you move past the demo:
- **`main.py`** — the demo loop. Replace `do_one_request()` with your real work.
- **`kio_otel.py`** — the instrumentation helper (mandatory metrics, heartbeat,
  trace and log streams). Copy this one file into your real project; you don't
  edit it.

**Verify before writing any real integration code** — most "no data in
Grafana" problems are network problems, not code problems:

```bash
cd remote-kio/with_script
pip install -r requirements.txt
OTEL_EXPORTER_OTLP_ENDPOINT=http://<platform-host>:4317 python check_connectivity.py
```

A `PASS` means the network path and auth are good; watch your `KIO_ID` appear
in Grafana → **KIO Detail** within ~30–60 seconds. A `FAIL` is almost always
the firewall or the wrong address — see §5.

**Not using Python, or writing a real integration by hand?** The contract in
§3 is language-agnostic — OpenTelemetry SDKs exist for Go, Java, JS/TS, .NET,
Rust and more. Configure an OTLP exporter at `OTEL_EXPORTER_OTLP_ENDPOINT`,
load the resource attributes, create the 7 mandatory instruments with the
exact names/types/units in §3.2, and tick the heartbeat every 60s. The
normative reference implementation (appendix, `MeterProvider` setup) is in
**[`docs/Observability_v2.2.docx`](Observability_v2.2.docx)**.

## 5. Networking (firewall, static IP, Tailscale)

Documented once, in depth, in **[`remote-kio/NETWORK.md`](../remote-kio/NETWORK.md)**:
opening the OTLP ports on the central machine's firewall (Windows/Linux),
finding the central machine's address, choosing between same-LAN /
static-public-IP-with-port-forwarding / Tailscale-VPN (recommended for
different networks), and adding TLS + Bearer auth for untrusted networks.

## 6. Onboarding checklist (§1 of the normative guide)

1. **Get assigned**: an OTLP Bearer token + a unique `kio.id`, from the central
   platform team (plus Langfuse project keys if you'll use that stream).
2. **Set the environment variables** in §2/§3.1 for your scenario.
3. **Send the 7 mandatory metrics** (§3.2) plus the heartbeat.
4. **Verify the acceptance gate**: `kio.heartbeat` visible in Grafana → **KIO
   Detail**, and (if using Langfuse) at least one trace visible there. A KIO
   isn't considered onboarded until both are visible.
5. *(Optional)* register with the Workflow API/Planner (§3.4, last bullet) —
   only if the KIO should be triggerable centrally.

This is a completely separate concern from the Planner/NATS orchestration
layer — sending contract-compliant telemetry never requires registering
there. See [Orchestration Layer](orchestration.md).
