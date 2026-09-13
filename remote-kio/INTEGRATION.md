# Connecting Your KIO Module — Complete Integration Guide

This guide is for a developer who has written their **own KIO module** (their own
codebase, most likely running on a different machine) and needs to connect it to
the AI4SWENG central observability platform. It assumes **no prior knowledge** of
this system, OpenTelemetry, or Grafana.

You do **not** need Docker, and you do **not** have to use Python — this guide
covers both the Python helper we ship and the raw data contract for any language
or runtime. Read top to bottom and your module will show up live in the central
Grafana. Estimated time: **15–30 minutes** if the network is ready.

> This document is self-contained. For deep networking (firewall, static IP,
> Tailscale) see [`NETWORK.md`](NETWORK.md); for the normative rules see the
> [Observability Guide v2.2](../docs/Observability_v2.2.docx). You do
> not need to leave this page to follow the flow.

## Quick start — run a ready-made example first

The fastest way to see this working is to run one of the two ready examples in
this folder, watch it appear in Grafana, then swap in your own code. Pick the one
that matches how you run things:

- **[`with_script/`](with_script/)** — a plain Python program (no Docker):
  `cp .env.example .env` → edit `KIO_ID` + the endpoint/token →
  `pip install -r requirements.txt` → `python main.py`.
- **[`with_docker/`](with_docker/)** — the same program in a container:
  `cp .env.example .env` → edit it → `docker compose up --build`.

Both push the seven mandatory metrics + heartbeat + a log line, driven entirely
by `.env`. Once your `KIO_ID` shows up in Grafana's **KIO Detail** dashboard, copy
`kio_otel.py` into your real module and wrap your handler with
`with kio.request(...)` — the rest of this guide explains that path in full.

---

**Contents**
1. [What this system is (5-minute concept tour)](#1-what-this-system-is-5-minute-concept-tour)
2. [What you change vs. never touch](#2-what-you-change-vs-never-touch)
3. [The data & naming contract](#3-the-data--naming-contract-read-this-for-non-python--non-docker) ← for non-Python / non-Docker
4. [Prerequisites](#4-prerequisites)
5. [Step-by-step setup](#5-step-by-step-setup)
6. [Deployment without Docker](#6-deployment-without-docker)
7. [Integration by code shape](#7-integration-by-code-shape)
8. [Not using Python?](#8-not-using-python)
9. [Optional streams](#9-optional-streams)
10. [FAQ](#10-faq)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. What this system is (5-minute concept tour)

**Observability** means collecting the numbers a program produces while running
(how many requests it handled, how long they took, whether they errored, how many
tokens it spent…) into one central place and viewing them as charts. The pieces:

```
   YOUR MACHINE                          CENTRAL MACHINE (B)
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

Three things to know:

1. **It is push-based.** *You* push data to the center. The center never pulls
   from you, never scrapes you, never connects to you. The only network permission
   needed: an **outbound** connection from you to the center's `:4317`.
2. **Location is irrelevant.** Your KIO can be in the same room or another city —
   as long as it can reach the collector, it works. So moving from "local test →
   LAN → remote network" is **not a code change**, only a change of one address
   (`OTEL_EXPORTER_OTLP_ENDPOINT`).
3. **It uses a standard tool.** Telemetry is produced by **OpenTelemetry (OTel)**,
   the industry-standard SDK. You do not invent a wire format; you configure the
   SDK and emit 7 standard measurements. This repo hands you that in one file:
   [`with_script/kio_otel.py`](with_script/kio_otel.py).

**Telemetry has three kinds** (all travel over the same `:4317`):
- **Metrics** — numeric time series (request count, duration, tokens…). *Required.*
- **Traces** — the step-by-step timeline of one request. *Recommended, easy.*
- **Logs** — free-text lines. *Optional.*

This guide gets the required metrics + heartbeat + a simple trace flowing; the
rest is optional (§9).

---

## 2. What you change vs. never touch

The most common worry for a newcomer is "how much will I have to customize?" The
answer: **very little**. The change surface is deliberately small and bounded.

| Thing | Owner | Do you change it? |
|---|---|---|
| **Your own module code** (your LLM call, analysis, work) | You | ✅ It's yours already |
| **Environment variables** (endpoint, `kio.id`, version…) | You | ✅ Yes — 3–4 lines for your setup |
| **Integration point**: wrapping your handler in `with kio.request()` | You | ✅ Yes — usually one place, a few lines |
| [`with_script/kio_otel.py`](with_script/kio_otel.py) | Reference kit | ⛔ Usually no — copy it as-is |
| **The 7 mandatory metric names/types/units** | Contract (§3) | ⛔ No — fixed; dashboards depend on them |
| **Central collector / databases / Grafana** | Central team | ⛔ No — don't touch; already running |
| **Docker itself** | — | ⛔ Not required at all — see §6 |

> **Two ready examples:** [`with_script/`](with_script/) (plain Python) and
> [`with_docker/`](with_docker/) (containerized) each run a small demo KIO that
> pushes the mandatory telemetry from `.env`. Run one to see it work, then copy
> `kio_otel.py` into your own module — that's the path this guide describes.

---

## 3. The data & naming contract (read this for non-Python / non-Docker)

This is the exact, language- and runtime-agnostic contract of **what goes on the
wire and under what names**. `kio_otel.py` produces all of this for you; you need
this section only if you implement it yourself (another language, or you want to
know precisely what you are emitting).

### 3.1 Transport

- Protocol: **OTLP** (OpenTelemetry Protocol).
- Default: **gRPC on port 4317** — this is what `kio_otel.py` and every KIO in this
  repo use. Point your exporter's endpoint at `http://<central>:4317`.
- Alternative: **OTLP/HTTP on port 4318** — the collector also accepts it, at path
  `/v1/metrics` (metrics), `/v1/traces`, `/v1/logs`. Use this only if your language's
  SDK prefers HTTP. **Do not hand-roll OTLP by hand** — use a real OTel SDK for your
  language; it is far less error-prone.
- Security: a plain `http://` endpoint connects **insecure** (fine on a trusted LAN
  or Tailscale/VPN, and is the default). An `https://` endpoint uses TLS; see §9 /
  [`NETWORK.md`](NETWORK.md) §4.

### 3.2 Resource attributes (identity — attached to every signal)

Set once, via the `OTEL_RESOURCE_ATTRIBUTES` environment variable as
`key=value,key=value`. These identify who is sending, and the collector promotes
them to metric **labels** (dots become underscores — see 3.4).

| Attribute key | Required | Example | Becomes label |
|---|---|---|---|
| `service.name` | ✅ | `kio1` | `service_name` |
| `service.version` | ✅ | `1.0.0` | `service_version` |
| `kio.id` | ✅ | `kio1` | `kio_id` |
| `deployment.environment` | ✅ | `production` | `deployment_environment` |
| `llm` | optional | `claude-sonnet` | `llm` |
| `task_type` | optional | `code-analysis` | `task_type` |

`kio.id` **must be unique** across all running KIOs and should be assigned by the
central team (Contract §1.1).

### 3.3 The 7 mandatory metrics (Contract §2.1) — names/types/units are fixed

Every metric also carries `kio.id` (via the resource above). Metric-level labels
listed below are the ones that vary per data point.

| Metric name (OTLP) | Type | Unit | Extra labels (allowed values) | Meaning |
|---|---|---|---|---|
| `kio.request.count` | Counter | `1` | `status` = `ok` \| `error` | Requests processed |
| `kio.request.duration_ms` | Histogram | `ms` | — | End-to-end latency distribution |
| `kio.request.error_count` | Counter | `1` | `error_type` = bounded enum¹ | Errors by category |
| `kio.llm.token_count` | Counter | `tokens` | `direction` = `input` \| `output` | LLM token usage |
| `kio.llm.cost_usd` | Counter | `USD` | — | Estimated total LLM cost |
| `kio.session.active_count` | UpDownCounter | `1` | — | In-flight sessions (goes up and down) |
| `kio.heartbeat` | Counter | `1` | — | Liveness, incremented every 60s |

¹ `error_type` must be a **small, bounded set** of strings you define (e.g.
`timeout`, `internal`, `rate_limit`). Never put unbounded values (IDs, messages)
there — see rule G3.

> **The heartbeat is critical.** A KIO that does not emit `kio.heartbeat` for more
> than **120 seconds** is flagged **stale** in the central registry — a Grafana
> alert rule and an Overview panel show this automatically. `kio_otel.py` handles
> it via `start_heartbeat()`.

### 3.4 How names appear in Grafana / PromQL

VictoriaMetrics is Prometheus-compatible, so when you query in Grafana:
- Dots in metric and label names become **underscores**:
  `kio.request.count` → `kio_request_count`, `kio.id` → `kio_id`.
- There is **no `_total` suffix** (the collector is configured with
  `add_metric_suffixes: false`).
- Histograms expand into three series: `kio_request_duration_ms_bucket`,
  `kio_request_duration_ms_sum`, `kio_request_duration_ms_count`.

Example PromQL a dashboard uses: `sum(rate(kio_request_count{kio_id="kio1"}[5m]))`.

### 3.5 Rules (Contract Appendix G1–G7) — summary

- **G1** Names follow `kio.<domain>.<metric>` and are **permanent** once released.
- **G2** Every metric/trace carries `kio.id` (traces also `session.id`).
- **G3** **No high-cardinality labels**: no unique IDs / URLs / emails as label
  values; use bounded enums. (`session.id` is trace/log metadata, **not** a metric label.)
- **G4** **No secrets / PII** in labels or traces.
- **G5** Bump `service.version` on every release.
- **G6** Mandatory metrics are governed by the central team.
- **G7** **Anything beyond the mandatory 7 is self-service** — define your own metric
  as long as it obeys G1–G5.

---

## 4. Prerequisites

1. **A working KIO module** — your own code that calls an LLM / analyzes code / does
   some job. (This guide assumes Python; for other languages see §8.)
2. **Python 3.9+** and `pip` (for the Python helper). **No Docker required** (§6).
3. **The central machine's address** — from the central team (e.g. `192.168.1.50`
   or a Tailscale IP). You'll append `:4317`.
4. **An assigned `kio.id`** — from the central team. Must be **unique** across all
   running KIOs; don't make one up (collisions merge two KIOs' series in Grafana).
5. *(Optional)* an **OTLP Bearer token** if the setup is secured/remote; **Langfuse
   keys** if you'll also use that stream — both from the central team.

---

## 5. Step-by-step setup

### Step 1 — BEFORE writing code: verify you can reach the center

Most "No data" problems are network problems, fixable without touching code. Prove
reachability first:

```bash
cd with_script
pip install -r requirements.txt
OTEL_EXPORTER_OTLP_ENDPOINT=http://<central-address>:4317 python check_connectivity.py
```

It checks (1) TCP reachability to the port, and (2) that a real `kio.heartbeat`
export is accepted by the collector. **Do not continue until you see `RESULT: PASS`** —
if it's a network/firewall issue, no code change will fix it. On `FAIL`, it's almost
always that **port 4317 is closed by the firewall** on the central machine, or you're
**not on the same network**. Fixes in [`NETWORK.md`](NETWORK.md).

### Step 2 — Install dependencies and take `kio_otel.py`

```bash
pip install opentelemetry-api==1.44.0 opentelemetry-sdk==1.44.0 opentelemetry-exporter-otlp-proto-grpc==1.44.0
cp with_script/kio_otel.py <your-project>/
```

You do **not** edit `kio_otel.py` — use it as-is.

### Step 3 — Set environment variables

```bash
# REQUIRED: central collector address (gRPC, port 4317)
export OTEL_EXPORTER_OTLP_ENDPOINT="http://<central-address>:4317"

# REQUIRED: your identity (the unique id assigned by the central team)
export OTEL_RESOURCE_ATTRIBUTES="service.name=kio1,service.version=1.0.0,kio.id=kio1,deployment.environment=production"
```

Full list in §3.2 and the [`.env.example`](with_script/.env.example). On Windows
PowerShell use `$env:NAME="value"`; in a container, pass these as normal env vars.

### Step 4 — Wire it into your code (the actual work — a few lines)

Three additions: **(a)** an instance + heartbeat at startup, **(b)** wrap each unit
of work in `with kio.request()`, **(c)** flush on exit.

```python
from kio_otel import KIOTelemetry

# (a) At application STARTUP, exactly ONCE:
kio = KIOTelemetry()        # reads OTEL_* / KIO_* env vars itself
kio.start_heartbeat()       # 60s liveness signal (required, §3)

# (b) Around each "request" / unit of work:
with kio.request(session_id=incoming_id) as req:
    output = my_real_work(...)                     # ← YOUR code, unchanged
    req.record_tokens(input=in_tok, output=out_tok)   # your real token counts
    # req.record_cost_usd(0.0012)                  # if your LLM has a $ cost

# (c) Before the process exits, MANDATORY:
kio.shutdown()              # flushes the last buffered data; skip it and you lose it
```

The `with` block automatically handles the active-session counter, request duration,
ok/error counting, and — if an exception escapes the block — records it as a real
error (`kio.request.error_count`, with a bounded `error_type`) and re-raises it, so
telemetry never changes your control flow. All you do inside is report your real
token counts. Not using an LLM? Drop those lines — metrics still flow. Runnable
example: [`with_script/main.py`](with_script/main.py).

### Step 5 — Run and verify (the acceptance gate)

Start your module. Within ~30–60s: central Grafana → **KIO Detail** dashboard → the
`KIO` dropdown should show **your `kio.id`**, the heartbeat should tick, and your
panels should start filling. Until it appears, the KIO is **not "onboarded"**
(Contract §1.5). If it doesn't show, see §11.

---

## 6. Deployment without Docker

**Docker is not required anywhere in this integration.** `kio_otel.py` is plain
Python; your KIO runs however you already run it. Options:

**A) Bare process.** Set the env vars, then just run it:
```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="http://192.168.1.50:4317"
export OTEL_RESOURCE_ATTRIBUTES="service.name=kio1,service.version=1.0.0,kio.id=kio1,deployment.environment=production"
python your_kio.py
```

**B) venv + systemd service (Linux) — recommended for a long-running KIO.**
Create `/etc/systemd/system/kio1.service`:
```ini
[Unit]
Description=KIO1 telemetry module
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/kio1
Environment=OTEL_EXPORTER_OTLP_ENDPOINT=http://192.168.1.50:4317
Environment=OTEL_RESOURCE_ATTRIBUTES=service.name=kio1,service.version=1.0.0,kio.id=kio1,deployment.environment=production
ExecStart=/opt/kio1/venv/bin/python /opt/kio1/your_kio.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
Then `sudo systemctl enable --now kio1`. (`Restart=always` gives you the same
"stays up / recovers" behavior Docker's `restart: unless-stopped` would.)

**C) Windows.** Run it in a terminal, as a Scheduled Task (at logon / startup), or
as a service via NSSM. Set env vars with `$env:NAME="value"` or in the task/service
definition.

**D) Process managers.** supervisor, pm2, runit, etc. all work — they just need to
run your Python entrypoint with the env vars set.

**E) Still want a container?** You may use Docker/Podman/nerdctl if you prefer — the
integration doesn't care. There's just no requirement to.

Whatever you choose, the only hard requirements are: the 3 pip packages installed,
the env vars set, and outbound reachability to `:4317`.

---

## 7. Integration by code shape

The three parts (set up / wrap / flush) are identical everywhere; only *where* you
put them changes.

**A) Web service (FastAPI / Flask / …)** — construct once at startup, wrap each
endpoint, flush on shutdown:
```python
kio = KIOTelemetry()          # once, when the module loads
kio.start_heartbeat()

@app.post("/solve")
async def solve(body):
    with kio.request(session_id=body.session_id) as req:
        result = await my_llm_work(body)
        req.record_tokens(input=result.in_tok, output=result.out_tok)
        return result

@app.on_event("shutdown")     # Flask: atexit.register(kio.shutdown)
def _flush():
    kio.shutdown()
```

**B) CLI / one-shot process (each call is a new process)** — set up and flush in the
same `main()`. `shutdown()` is critical here; lower `EXPORT_INTERVAL_MS` (e.g. 1000)
for short commands:
```python
def main():
    kio = KIOTelemetry()
    kio.start_heartbeat()
    try:
        with kio.request() as req:
            out = do_the_job()
            req.record_tokens(input=..., output=...)
    finally:
        kio.shutdown()        # flush — mandatory in a short-lived process
```
**C) Continuous worker / queue consumer** — each job is one `request()`:
```python
kio = KIOTelemetry(); kio.start_heartbeat()
try:
    for task in queue:
        with kio.request(session_id=task.session_id) as req:
            out = handle(task)
            req.record_tokens(input=..., output=...)
finally:
    kio.shutdown()
```

> **Common rule:** `KIOTelemetry()` installs global OTel providers — create **one
> instance per process** and call `start_heartbeat()` once.

---

## 8. Not using Python?

`kio_otel.py` is a convenience, not a requirement. The contract is language-agnostic
and OpenTelemetry exists for Go, Java, JS/TS, .NET, Rust, and more. In any language:

1. Configure an OTLP metric exporter (gRPC → `:4317`, or HTTP → `:4318`, §3.1) at
   `OTEL_EXPORTER_OTLP_ENDPOINT`.
2. Load `Resource` attributes from `OTEL_RESOURCE_ATTRIBUTES` (§3.2).
3. Create the **7 mandatory instruments with the exact names/types/units** in §3.3.
4. Run a background task that increments `kio.heartbeat` every 60s.
5. Per request: active count +1/−1, duration, `status=ok|error` count, `error_count`
   with a bounded `error_type` on failure.

A working Python reference (the manual-setup pattern) is in the
[Observability Guide v2.2](../docs/Observability_v2.2.docx) (Appendix —
Reference Implementation); `kio_otel.py` follows the same pattern and is a good model
to port.

---

## 9. Optional streams (skip unless you need them)

`kio_otel.py` deliberately implements only the required OTel stream (metrics + a
simple trace + heartbeat). These are all optional:

- **Free-text logs → VictoriaLogs.** LLM output summaries, analysis notes, etc.,
  sent as OTLP logs. Setup pattern: the logger block in `../kio-simulator/kio_simulator.py`.
- **Langfuse (LLM prompt/completion/cost).** A separate, parallel stream; only if
  you want prompt-level replay/cost analysis. Shared project keys come from the
  platform team (treat the secret as a credential — keep it in `.env`, don't post
  it publicly). The runnable examples (`with_script`/`with_docker`) already include
  an off-by-default Langfuse path: enable it by uncommenting the `LANGFUSE_*` keys
  in `.env` and `langfuse` in `requirements.txt`. The host is derived from your OTLP
  IP automatically (same host, port 3001).
- **Rich traces (child spans / step waterfall).** `kio_otel.py` emits one root span
  per request; for a `prepare_prompt → llm_call → postprocess` waterfall, see
  `kio_simulator.py` `emit_trace()`.
- **NATS-driven dispatch (orchestration layer).** Completely separate from telemetry;
  only if your KIO should be triggered by the central `POST /workflow/run`. See the
  main [`../README.md`](../README.md) "Orchestration layer".
- **TLS + Bearer auth** (for untrusted networks / production): `https://` endpoint +
  `OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <token>`. The SDK reads both env
  vars automatically — no code change. See [`NETWORK.md`](NETWORK.md) §4.

---

## 10. FAQ

**Will I have to change my module a lot?** No — your code stays; you add 3 parts (§5):
one object at startup, a `with` around your handler, one `shutdown()` at exit. You
don't touch metric names, the collector, or docker-compose.

**Do I have to use Docker?** No — §6. Bare process, systemd, Windows service, any
process manager. Only the 3 pip packages + env vars + reachability to `:4317`.

**My module isn't Python.** Fine — §8. The contract is language-agnostic.

**I don't call an LLM / have no token counts.** Drop the `req.record_tokens(...)`
lines. Request/duration/error/heartbeat metrics still flow; LLM metrics stay at zero.

**What exactly do I send, and under what names?** §3 — that's the full data & naming
contract (metric names, label keys, allowed values, how they appear in Grafana).

**Port 4317 or 4318?** The Python client uses **gRPC → 4317**. 4318 is OTLP/HTTP, for
SDKs that prefer HTTP. Pointing a gRPC client at 4318 gives a silent "No data".

**Do I set up Grafana?** No. Grafana, the collector, and the databases already run on
the central machine. You only send data; the dashboards are ready.

**My process is short-lived; heartbeat never reaches 60s.** Fine — in the CLI model
each call is one "request"; what matters is `shutdown()` to flush (§7-B). A
long-running service heartbeats normally.

---

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `check_connectivity.py` **[1/2] FAIL** | Wrong IP / firewall closed / different network | [`NETWORK.md`](NETWORK.md) §2 (firewall), §2c (address), §3 (network/VPN) |
| **[1/2] OK but [2/2] FAIL** | Port open, collector rejects export (e.g. TLS/auth) | [`NETWORK.md`](NETWORK.md) §4; check `http` vs `https` scheme |
| Test PASS but "No data" in Grafana | Endpoint set to `:4318` (HTTP), client is gRPC | Use port **4317** |
| KIO shows up but series look mixed | Same `kio.id` sent from two sources | Get a unique `kio.id` (§4) |
| Runs briefly, then stops reporting | `shutdown()` not called in a short-lived process | §7-B: `finally: kio.shutdown()` |
| KIO flagged "stale" | Heartbeat gap >120s (process stopped / network dropped) | Verify the process is up and exporting |

One-stop for all network/firewall/port issues → [`NETWORK.md`](NETWORK.md) §6.

---

## Related documents

| Document | For |
|---|---|
| [`with_script/`](with_script/) | Copyable minimal kit: `kio_otel.py`, example, preflight |
| [`NETWORK.md`](NETWORK.md) | Networking / firewall / static IP / Tailscale (deep dive) |
| [`README.md`](README.md) | Running **our simulator** on a remote machine (different scenario) |
| [Observability Guide v2.2](../docs/Observability_v2.2.docx) | Normative contract (7 metrics, rules, reference code) |
