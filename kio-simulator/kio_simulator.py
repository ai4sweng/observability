#!/usr/bin/env python3
"""
AI4SWENG KIO telemetry simulator.

Emits contract-compliant dummy telemetry over OTLP/gRPC for a single KIO:
  * the mandatory metric set (Integration Contract §2.1),
  * optional self-service metrics (tokens/sec, GPU energy, accuracy, repo stats),
  * a 60s heartbeat (§2.3),
  * an unstructured/string log stream (extension beyond the contract minimum),
  * a trace per request: a root "kio.request" span with nested child spans
    (prepare_prompt -> llm_call -> postprocess; code-analysis KIOs additionally
    emit a leading repo_scan span) so Grafana/Tempo can show the step-by-step
    sequence of an operation (the "Trace Waterfalls" panel).

Real LLMs are NOT invoked by default; values are randomly generated. One KIO
of type "code-analysis" additionally reports real line/directory counts of a
scanned repo path so at least one KIO carries genuine structured extra data.

Optionally (KIO2_REAL_LLM_ENABLED=true), a KIO can call a real Ollama server
for its llm_call step instead of faking token counts — see
_call_ollama_real(). Tokens/sec then comes from Ollama's own eval_count /
eval_duration. GPU energy is read from a real source if one is reachable
(local NVML, or an external power-exporter HTTP endpoint); otherwise it
silently falls back to the old estimated formula — see _read_gpu_power_watts().

Config comes from environment variables (see docker-compose.yml).
"""
import asyncio
import os
import random
import signal
import threading
import time
import uuid

from envelope import KIOEnvelope, KIOResult, result_subject, task_subject

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.resources import Resource

from opentelemetry import _logs
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
import logging

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import Status, StatusCode

# Langfuse — second, parallel telemetry stream (LLM prompts/completions/cost),
# independent of the OTel pipeline above. Reads LANGFUSE_PUBLIC_KEY /
# LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL from the environment automatically.
# The SDK swallows connection errors internally (never raises into caller
# code), so this is safe even before the Langfuse stack finishes booting.
from langfuse import get_client, propagate_attributes

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
KIO_ID = os.environ.get("KIO_ID", "kioN")
KIO_LLM = os.environ.get("KIO_LLM", "qwen2.5:3b")
TASK_TYPE = os.environ.get("KIO_TASK_TYPE", "generic")
SERVICE_VERSION = os.environ.get("SERVICE_VERSION", "1.0.0")
ENVIRONMENT = os.environ.get("DEPLOYMENT_ENVIRONMENT", "production")
ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
EXPORT_INTERVAL_MS = int(os.environ.get("EXPORT_INTERVAL_MS", "15000"))
HEARTBEAT_INTERVAL_S = int(os.environ.get("HEARTBEAT_INTERVAL_S", "60"))
REQUEST_INTERVAL_S = float(os.environ.get("REQUEST_INTERVAL_S", "3"))

# --- Optional NATS-driven trigger mode (orchestration layer) ---
# Off by default: this KIO fires requests on its own internal timer, exactly
# as before. When on, tasks instead arrive as KIOEnvelope messages on
# kio.tasks.<KIO_ID> (published by orchestrator/planner.py), and this KIO
# publishes a KIOResult back on kio.results.<KIO_ID> for lineage registration.
# The two modes are mutually exclusive but touch nothing else — simulate_request()
# itself doesn't know or care which one is driving it.
NATS_ENABLED = os.environ.get("NATS_ENABLED", "false").lower() == "true"
NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")
REPO_SCAN_PATH = os.environ.get("REPO_SCAN_PATH", "/app")

# Real-project KPI role (D1.1 Project Management Handbook). Empty by default.
# Valid values, each mapped to a D1.1 KIO identity (not to this simulator's
# arbitrary task_type/LLM choice):
#   "bugfix"               -> KIO2 (Bug Locate & Fix / LLM Debugger)
#   "nlp-requirements"      -> KIO3 (NLP -> Formal Requirements)
#   "architecture-to-code"  -> KIO4 (Architecture-to-Code Planner)
#   "ai-sysdev"             -> KIO7 (AI-SysDev)
#   "green-deploy"          -> KIO8 (Cross-Architecture / Energy-Efficient Deploy)
#   "adoption"              -> KIO13 (Adoption & Usage Tracking)
# Others can be added the same way later — see
# docs/AI4SWENG_KPI_Metrik_Referansi_v1.3.docx.
KIO_REAL_KPI_ROLE = os.environ.get("KIO_REAL_KPI_ROLE", "")

# --- Optional real-LLM path (KIO2 today) ---
# Off by default so the base demo never requires Ollama to be running.
KIO2_REAL_LLM_ENABLED = os.environ.get("KIO2_REAL_LLM_ENABLED", "false").lower() == "true"
OLLAMA_ENDPOINT = os.environ.get("OLLAMA_ENDPOINT", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", KIO_LLM)
OLLAMA_TIMEOUT_S = float(os.environ.get("OLLAMA_TIMEOUT_S", "30"))
# Real GPU power, read from whichever source is available (in this order):
# 1) an external "power exporter" HTTP endpoint (see tools/power_exporter.py,
#    meant to run natively on the Windows host next to Ollama, since NVML
#    inside a Linux container can't see a GPU it hasn't been passed through);
# 2) local NVML, if the container *does* have GPU passthrough configured.
# If neither works, energy silently falls back to the old estimated formula.
GPU_POWER_EXPORTER_URL = os.environ.get("GPU_POWER_EXPORTER_URL", "")

# LLM pricing per 1K tokens (dummy) so cost varies believably by model.
LLM_COST_PER_1K = {
    "qwen2.5:3b": 0.0,
    "llama3.1:8b": 0.0,
    "gpt-4o-mini": 0.00060,
    "gpt-4o": 0.01000,
    "claude-sonnet": 0.00300,
}

# Approx. GPU joules per output token (dummy), bigger models cost more energy.
# Used as the fallback whenever a real power reading isn't available.
LLM_JOULES_PER_TOKEN = {
    "qwen2.5:3b": 0.8,
    "llama3.1:8b": 2.1,
    "gpt-4o-mini": 3.5,
    "gpt-4o": 9.0,
    "claude-sonnet": 6.0,
}

# --------------------------------------------------------------------------- #
# Optional real-LLM path (KIO2_REAL_LLM_ENABLED=true) — real Ollama call +
# real GPU power sampling. Every function here is defensive: any failure
# (Ollama unreachable, no power source, pynvml missing) falls back cleanly
# rather than crashing the simulator — this path is additive, never required.
# --------------------------------------------------------------------------- #
_nvml_handle = None
_nvml_ready = False


def _nvml_init_once():
    """Best-effort NVML init; only meaningful if this process's container
    actually has GPU passthrough. Safe to call repeatedly."""
    global _nvml_handle, _nvml_ready
    if _nvml_ready or _nvml_handle is not None:
        return
    try:
        import pynvml
        pynvml.nvmlInit()
        _nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        _nvml_ready = True
        logging.getLogger(KIO_ID).info("NVML initialised — local GPU power reading available.")
    except Exception:
        _nvml_ready = False


def _read_gpu_power_watts():
    """Real GPU power draw in watts, or None if no real source is reachable.
    Tries the external power-exporter HTTP endpoint first (works when Ollama
    runs natively on the Windows host, which is the common case — a Linux
    container has no visibility into that GPU otherwise), then local NVML."""
    if GPU_POWER_EXPORTER_URL:
        try:
            import requests
            resp = requests.get(GPU_POWER_EXPORTER_URL, timeout=1.5)
            resp.raise_for_status()
            watts = float(resp.json()["watts"])
            return watts
        except Exception:
            pass  # fall through to NVML / caller's fallback
    _nvml_init_once()
    if _nvml_ready:
        try:
            import pynvml
            return pynvml.nvmlDeviceGetPowerUsage(_nvml_handle) / 1000.0  # mW -> W
        except Exception:
            return None
    return None


def _read_gpu_temperature_celsius():
    """Real GPU die temperature in °C, or None if no real source is reachable.
    Mirrors _read_gpu_power_watts()'s two-tier lookup (host power-exporter
    first, then local NVML) — see tools/power_exporter.py, which now also
    serves a "temperature_c" field alongside "watts"."""
    if GPU_POWER_EXPORTER_URL:
        try:
            import requests
            resp = requests.get(GPU_POWER_EXPORTER_URL, timeout=1.5)
            resp.raise_for_status()
            temp_c = resp.json().get("temperature_c")
            if temp_c is not None:
                return float(temp_c)
        except Exception:
            pass  # fall through to NVML
    _nvml_init_once()
    if _nvml_ready:
        try:
            import pynvml
            return float(pynvml.nvmlDeviceGetTemperature(_nvml_handle, pynvml.NVML_TEMPERATURE_GPU))
        except Exception:
            return None
    return None


def _measure_energy_during(call_fn):
    """Runs call_fn() while sampling real GPU power ~5x/second in a background
    thread, integrating power*dt to get real joules. Returns (result, joules)
    where joules is None if no real power source was ever readable — caller
    should fall back to the estimated formula in that case."""
    samples = []
    stop = threading.Event()

    def _sampler():
        last_t = time.monotonic()
        while not stop.is_set():
            w = _read_gpu_power_watts()
            now = time.monotonic()
            if w is not None:
                samples.append((now - last_t, w))
            last_t = now
            stop.wait(0.2)

    t = threading.Thread(target=_sampler, daemon=True)
    t.start()
    try:
        result = call_fn()
    finally:
        stop.set()
        t.join(timeout=1.0)
    if not samples:
        return result, None
    joules = sum(dt * w for dt, w in samples)
    return result, joules


def _call_ollama_real(prompt, model=None):
    """Calls a real Ollama server's /api/generate. Always returns a dict —
    never None — so the caller can tell a REAL SUCCESS apart from a REAL
    FAILURE (as opposed to the real path being disabled entirely):
      success: {"ok": True, "text", "input_tokens", "output_tokens",
                "duration_s", "tokens_per_second"} — every field measured.
      failure: {"ok": False, "error_type", "duration_s"} — the call was
                genuinely attempted and genuinely failed (Ollama down, wrong
                model, network error, timeout). The caller must record this
                as a real kio.request.error_count, not silently swap in a
                fresh simulated "successful" request — doing that would hide
                real Ollama unavailability from the error-rate metric."""
    import requests
    t0 = time.monotonic()
    try:
        resp = requests.post(
            f"{OLLAMA_ENDPOINT}/api/generate",
            json={"model": model or OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=OLLAMA_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        wall_s = time.monotonic() - t0
        eval_count = data.get("eval_count", 0)
        eval_duration_s = data.get("eval_duration", 0) / 1e9  # ns -> s
        prompt_eval_count = data.get("prompt_eval_count", 0)
        tps = eval_count / eval_duration_s if eval_duration_s > 0 else (eval_count / wall_s if wall_s > 0 else 0.0)
        return {
            "ok": True,
            "text": data.get("response", ""),
            "input_tokens": prompt_eval_count,
            "output_tokens": eval_count,
            "duration_s": wall_s,
            "tokens_per_second": tps,
        }
    except requests.exceptions.Timeout:
        error_type = "ollama_timeout"
    except requests.exceptions.ConnectionError:
        error_type = "ollama_unreachable"
    except requests.exceptions.HTTPError:
        error_type = "ollama_http_error"
    except Exception:
        error_type = "ollama_call_failed"
    wall_s = time.monotonic() - t0
    logging.getLogger(KIO_ID).warning(
        f"Real Ollama call failed ({OLLAMA_ENDPOINT}, model={model or OLLAMA_MODEL}, "
        f"reason={error_type}) — recording this as a REAL request error.",
    )
    return {"ok": False, "error_type": error_type, "duration_s": wall_s}


def _build_debug_prompt():
    """A small, real prompt for KIO2's real-LLM path: ask the model about a
    short snippet of KIO2's own scanned source. Not a real bug corpus (that's
    FocusTracer's job, not ready yet) — just enough to make the real Ollama
    call meaningful rather than an empty no-op prompt."""
    snippet = "def divide(a, b):\n    return a / b  # ZeroDivisionError if b == 0"
    return (
        "You are a bug-fix assistant. In one short sentence, identify the "
        f"most likely runtime bug in this function and suggest a one-line fix:\n\n{snippet}"
    )


def _evaluate_fix_success():
    """Placeholder outcome for the fix@1 metric (KIO2's 'accuracy' KPI, method
    left to us per the meeting). Real success requires a real bug + a real
    test run — that's FocusTracer's job once it's ready. Until then this
    returns a plausible pass rate; swap this function's body only, once
    FocusTracer can report whether its first suggested patch actually passed."""
    return random.random() < 0.72


# --------------------------------------------------------------------------- #
# Resource — mandatory correlation attributes (Contract §1.2)
# --------------------------------------------------------------------------- #
resource = Resource.create({
    "service.name": KIO_ID,
    "service.version": SERVICE_VERSION,
    "kio.id": KIO_ID,
    "deployment.environment": ENVIRONMENT,
    # Optional bounded-enum labels (G3-compliant) promoted to metric labels.
    "llm": KIO_LLM,
    "task_type": TASK_TYPE,
})
_INSECURE = ENDPOINT.startswith("http://")

# --------------------------------------------------------------------------- #
# Metrics pipeline
# --------------------------------------------------------------------------- #
metric_exporter = OTLPMetricExporter(endpoint=ENDPOINT, insecure=_INSECURE)
reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=EXPORT_INTERVAL_MS)
metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
meter = metrics.get_meter("kio.instrumentation")

# --- Mandatory metric set (Contract §2.1) ---
request_counter = meter.create_counter("kio.request.count", unit="1")
request_duration = meter.create_histogram("kio.request.duration_ms", unit="ms")
error_counter = meter.create_counter("kio.request.error_count", unit="1")
llm_token_counter = meter.create_counter("kio.llm.token_count", unit="tokens")
llm_cost_counter = meter.create_counter("kio.llm.cost_usd", unit="USD")
active_sessions = meter.create_up_down_counter("kio.session.active_count", unit="1")
heartbeat_counter = meter.create_counter("kio.heartbeat", unit="1")

# --- Optional self-service metrics (G7, matching G1-G5) ---
tokens_per_second = meter.create_histogram("kio.llm.tokens_per_second", unit="tokens/s")
energy_counter = meter.create_counter("kio.llm.energy_joules", unit="J")
accuracy_hist = meter.create_histogram("kio.request.accuracy", unit="1")

# Real GPU die temperature (°C) — only ever recorded when KIO2_REAL_LLM_ENABLED
# and a real power-exporter/NVML source is reachable (source=real). Modelled
# as a histogram (like tokens_per_second) rather than a native OTel gauge, to
# stay consistent with the rest of this file's instrumentation choices and
# with how the OTLP->Prometheus exporter here already handles _sum/_count.
gpu_temp_hist = meter.create_histogram("kio.llm.gpu_temperature_celsius", unit="Cel")

# Repo stats reported only by code-analysis KIOs, via observable gauges.
_repo_stats = {"lines": 0, "directories": 0, "files": 0}


def _scan_repo():
    """Best-effort real line/dir/file count of REPO_SCAN_PATH."""
    lines = dirs = files = 0
    for root, dirnames, filenames in os.walk(REPO_SCAN_PATH):
        if any(part.startswith(".") for part in root.split(os.sep)):
            continue
        dirs += len(dirnames)
        for fn in filenames:
            if fn.endswith((".py", ".js", ".ts", ".go", ".java", ".txt", ".md", ".yaml", ".yml")):
                files += 1
                try:
                    with open(os.path.join(root, fn), "r", errors="ignore") as fh:
                        lines += sum(1 for _ in fh)
                except OSError:
                    pass
    return {"lines": lines, "directories": dirs, "files": files}


def _obs_lines(options):
    from opentelemetry.metrics import Observation
    return [Observation(_repo_stats["lines"], {"kio.id": KIO_ID, "llm": KIO_LLM, "task_type": TASK_TYPE})]


def _obs_dirs(options):
    from opentelemetry.metrics import Observation
    return [Observation(_repo_stats["directories"], {"kio.id": KIO_ID, "llm": KIO_LLM, "task_type": TASK_TYPE})]


def _obs_files(options):
    from opentelemetry.metrics import Observation
    return [Observation(_repo_stats["files"], {"kio.id": KIO_ID, "llm": KIO_LLM, "task_type": TASK_TYPE})]


if TASK_TYPE == "code-analysis":
    _repo_stats = _scan_repo()
    meter.create_observable_gauge("kio.repo.line_count", callbacks=[_obs_lines], unit="1")
    meter.create_observable_gauge("kio.repo.directory_count", callbacks=[_obs_dirs], unit="1")
    meter.create_observable_gauge("kio.repo.file_count", callbacks=[_obs_files], unit="1")

# --- Real project KPIs (D1.1) — only for KIOs mapped to a "bugfix" role ---
# KIO2 in D1.1 = "Bug Locate & Fix / LLM Debugger" (T3.2 Reverse Execution /
# Dynamic Slicing, T3.3 Fault Localization) — the same job FocusTracer does.
# Names/units/targets are taken directly from D1.1 Table 8/9, not invented;
# see docs/AI4SWENG_KPI_Metrik_Referansi.docx for the full mapping. Values
# below are still simulated (calibrated to D1.1's baseline/target ranges),
# not yet wired to real FocusTracer runs — that's a separate future step.
if KIO_REAL_KPI_ROLE == "bugfix":
    bugfix_duration_hist = meter.create_histogram("kio.bugfix.duration_hours", unit="h")       # KPI 6.1
    issue_resolution_hist = meter.create_histogram("kio.issue.resolution_hours", unit="h")     # KPI 1.2
    slicing_success_hist = meter.create_histogram("kio.slicing.success_rate", unit="1")        # WP3 task metric
    customer_reported_counter = meter.create_counter("kio.issue.customer_reported_count", unit="1")  # KPI 6.2
    # "Accuracy" metric requested in the meeting, method left to us: fix@1 —
    # the bug-fix equivalent of pass@1 — did the LLM's *first* suggested patch
    # actually pass? Real outcome determination needs a real bug + real test
    # run (FocusTracer, not ready yet); until then _evaluate_fix_success()
    # below is a clearly-marked placeholder, swappable for the real result
    # without changing this metric's name/shape.
    fix_attempt_counter = meter.create_counter("kio.fix.attempt_count", unit="1")  # outcome=success|failure
else:
    bugfix_duration_hist = issue_resolution_hist = slicing_success_hist = customer_reported_counter = None
    fix_attempt_counter = None

# KPI 1.1 (Code generation speed) and KPI 3.1 (Code quality improvement) are
# both mapped by D1.1's traceability matrix to KIO3 (nlp-requirements) AND
# KIO4 (architecture-to-code) — even though neither role literally "generates
# code" itself, D1.1 measures these end-to-end from the pipeline's first step.
# Concrete units (minutes, %), not raw "% of baseline", mirroring the KIO2
# bugfix metrics above for the same reason (a dashboard-legible absolute
# number beats an abstract ratio with no baseline shown alongside it).
if KIO_REAL_KPI_ROLE in ("nlp-requirements", "architecture-to-code"):
    codegen_duration_hist = meter.create_histogram("kio.codegen.duration_minutes", unit="min")  # KPI 1.1
    code_quality_hist = meter.create_histogram("kio.code_quality.score_pct", unit="%")           # KPI 3.1
else:
    codegen_duration_hist = code_quality_hist = None

# KPI 3.2 (Review score increase) is D1.1-mapped to KIO4 only (architecture-to-code).
if KIO_REAL_KPI_ROLE == "architecture-to-code":
    review_score_hist = meter.create_histogram("kio.review.score", unit="1")  # KPI 3.2
else:
    review_score_hist = None

# KIO7 (AI-SysDev) in D1.1 is tied to "most KPIs" (1.1, 1.2, 2.x, 3.x, 4.1,
# 5.1, 6.x, 7.1, 9.x) — far too broad to simulate wholesale without it reading
# as noise. Only the KPIs where KIO7 is a clear primary/major owner (not
# already covered by kio2-sim/kio3/kio4 above) are implemented here: developer
# productivity, time-to-market, annual cost saving, refactoring reduction,
# technical debt reduction. Concrete units again, same rationale as above.
if KIO_REAL_KPI_ROLE == "ai-sysdev":
    dev_productivity_hist = meter.create_histogram("kio.dev_productivity.features_per_day", unit="1")  # KPI 4.1
    time_to_market_hist = meter.create_histogram("kio.time_to_market.days", unit="d")                  # KPI 5.1
    cost_saving_hist = meter.create_histogram("kio.cost_saving.pct", unit="%")                          # KPI 7.1
    refactoring_hist = meter.create_histogram("kio.refactoring.hours_per_feature", unit="h")            # KPI 9.1
    tech_debt_hist = meter.create_histogram("kio.tech_debt.hours_per_100loc", unit="h")                 # KPI 9.2
else:
    dev_productivity_hist = time_to_market_hist = cost_saving_hist = None
    refactoring_hist = tech_debt_hist = None

# KIO8 in D1.1 = the KIO uniquely tied to KPI 8.3 (Cross-Architecture Build
# Success Rate), and one of three KIOs (with KIO7/KIO10) tied to KPI 2.1/2.2
# (energy). KIO7's "ai-sysdev" role above deliberately left 2.1/2.2 out
# (see comment above) since neither was covered anywhere yet; they're
# implemented here under KIO8 instead, its clearer/more specific owner.
if KIO_REAL_KPI_ROLE == "green-deploy":
    lifecycle_energy_hist = meter.create_histogram("kio.lifecycle_energy.pct_of_baseline", unit="%")       # KPI 2.1
    deploy_energy_eff_hist = meter.create_histogram("kio.deploy_energy.tokens_per_s_per_w", unit="1")      # KPI 2.2
    cross_arch_build_counter = meter.create_counter("kio.cross_arch_build.success_count", unit="1")        # KPI 8.3
else:
    lifecycle_energy_hist = deploy_energy_eff_hist = cross_arch_build_counter = None

# KIO13 in D1.1 = the KIO uniquely tied to KPI 8.1 (Adoption rate) and
# KPI 8.2 (Active usage & satisfaction) — the only two KPIs D1.1 maps to a
# single KIO with no co-owners, so both are implemented together here.
if KIO_REAL_KPI_ROLE == "adoption":
    adoption_rate_hist = meter.create_histogram("kio.adoption.active_user_pct", unit="%")  # KPI 8.1
    adoption_usage_hist = meter.create_histogram("kio.adoption.usage_pct", unit="%")       # KPI 8.2 (usage half)
    adoption_mos_hist = meter.create_histogram("kio.adoption.mos_score", unit="1")         # KPI 8.2 (MOS half)
else:
    adoption_rate_hist = adoption_usage_hist = adoption_mos_hist = None

# --------------------------------------------------------------------------- #
# Logs pipeline — unstructured / string telemetry
# --------------------------------------------------------------------------- #
log_provider = LoggerProvider(resource=resource)
log_provider.add_log_record_processor(
    BatchLogRecordProcessor(OTLPLogExporter(endpoint=ENDPOINT, insecure=_INSECURE))
)
_logs.set_logger_provider(log_provider)

logger = logging.getLogger(KIO_ID)
logger.setLevel(logging.INFO)
logger.addHandler(LoggingHandler(level=logging.INFO, logger_provider=log_provider))
# Also echo to stdout for `docker logs`.
logger.addHandler(logging.StreamHandler())

# --------------------------------------------------------------------------- #
# Traces pipeline — one trace per simulated request, nested spans show the
# step-by-step sequence of the operation (the "Trace Waterfalls" panel in the
# KIO Detail dashboard, via Tempo).
# --------------------------------------------------------------------------- #
trace_provider = TracerProvider(resource=resource)
trace_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=ENDPOINT, insecure=_INSECURE))
)
trace.set_tracer_provider(trace_provider)
tracer = trace.get_tracer("kio.instrumentation")

# --------------------------------------------------------------------------- #
# Langfuse client — separate from the OTel tracer above. Deliberately its own
# HTTPS stream to a dedicated Langfuse server, per senior's request.
# Construction itself can throw (bad key format, unreachable host at client-
# init time, etc.); never let that take down metrics/logs/traces/heartbeat.
# --------------------------------------------------------------------------- #
try:
    langfuse_client = get_client()
except Exception:
    logging.getLogger(KIO_ID).warning("Langfuse client init failed; continuing without it", exc_info=True)
    langfuse_client = None

# Sample unstructured payloads a KIO might emit as free-form strings.
SUMMARY_TEMPLATES = [
    "Completed {task} on session {sid}; model={llm} produced {tok} tokens.",
    "LLM raw output: 'Refactored module, extracted {n} helper functions, added type hints.'",
    "Analysis note: detected {n} TODO markers and {m} deprecated API calls.",
    "Test report: generated {n} test cases, {m} assertions, coverage estimate {cov}%.",
    "Debug trace summary: root cause narrowed to {n} candidate stack frames.",
]

_stop = threading.Event()


def emit_unstructured(session_id, tokens):
    tmpl = random.choice(SUMMARY_TEMPLATES)
    msg = tmpl.format(
        task=TASK_TYPE, sid=session_id, llm=KIO_LLM, tok=tokens,
        n=random.randint(1, 12), m=random.randint(0, 30),
        cov=random.randint(55, 98),
    )
    # Attributes stay bounded (G3); the message body carries the free string.
    logger.info(msg, extra={"kio.id": KIO_ID, "session.id": session_id,
                            "llm": KIO_LLM, "task_type": TASK_TYPE})
    return msg


def emit_trace(session_id, work_s, in_tokens, out_tokens, is_error, error_type=None):
    """Build one root span + sequential child spans mirroring the request's
    real timeline (explicit start/end timestamps, no extra wall-clock sleep)."""
    total_ns = max(int(work_s * 1e9), 1_000_000)
    t0 = time.time_ns()

    root = tracer.start_span(
        "kio.request",
        start_time=t0,
        attributes={
            "kio.id": KIO_ID, "session.id": session_id, "llm": KIO_LLM,
            "task_type": TASK_TYPE, "kio.status": "error" if is_error else "ok",
        },
    )
    ctx = trace.set_span_in_context(root)
    cursor = t0
    remaining = total_ns

    def child(name, share, attributes=None, status_error=False):
        nonlocal cursor, remaining
        dur = max(int(total_ns * share), 200_000)  # floor 0.2ms so spans stay visible
        dur = min(dur, remaining)
        span = tracer.start_span(name, context=ctx, start_time=cursor, attributes=attributes or {})
        if status_error:
            span.set_status(Status(StatusCode.ERROR, error_type or "error"))
        span.end(end_time=cursor + dur)
        cursor += dur
        remaining -= dur

    if TASK_TYPE == "code-analysis":
        child("repo_scan", 0.15, {
            "repo.lines": _repo_stats["lines"],
            "repo.directories": _repo_stats["directories"],
            "repo.files": _repo_stats["files"],
        })

    child("prepare_prompt", 0.08)

    if is_error:
        child("llm_call", 0.77, {"llm.model": KIO_LLM, "llm.tokens.input": in_tokens},
              status_error=True)
        root.set_status(Status(StatusCode.ERROR, error_type or "error"))
    else:
        child("llm_call", 0.77, {
            "llm.model": KIO_LLM,
            "llm.tokens.input": in_tokens,
            "llm.tokens.output": out_tokens,
        })
        child("postprocess", max(remaining / total_ns, 0.01))
        root.set_status(Status(StatusCode.OK))

    root.end(end_time=t0 + total_ns)


def emit_langfuse_trace(session_id, in_tokens, out_tokens, cost, is_error, error_type, summary_text):
    """Langfuse counterpart to emit_trace(): same session_id, but carries the
    LLM-specific payload (prompt/completion summary, token usage, cost) that
    Tempo's generic spans don't. Kept as a small, isolated function so a
    Langfuse outage/misconfig can never affect the OTel pipeline above."""
    if langfuse_client is None:
        return
    try:
        with langfuse_client.start_as_current_observation(
            as_type="span", name="kio.request",
            input={"task_type": TASK_TYPE},
        ) as root_span:
            with propagate_attributes(
                session_id=session_id,
                metadata={"kioid": KIO_ID, "tasktype": TASK_TYPE},
                tags=[KIO_ID, TASK_TYPE],
            ):
                with langfuse_client.start_as_current_observation(
                    as_type="generation", name="llm_call", model=KIO_LLM,
                ) as gen:
                    gen.update(
                        output=summary_text,
                        usage_details={"input": in_tokens, "output": out_tokens},
                        cost_details={"total": cost} if cost else None,
                        level="ERROR" if is_error else "DEFAULT",
                        status_message=error_type if is_error else None,
                    )
            root_span.update(output={"status": "error" if is_error else "ok"})
    except Exception:
        # Never let a Langfuse hiccup break the simulated request itself.
        logger.debug("Langfuse emit failed (server may still be starting up)", exc_info=True)


def emit_real_kpi_metrics(labels, is_error):
    """D1.1-aligned KPIs, gated by KIO_REAL_KPI_ROLE (empty = none emitted).
    Simulated values, deliberately kept inside D1.1's baseline/target bands so
    the dashboard reads like plausible pilot-sprint progress, not noise.

    Unlike the LLM-performance metrics above (tok/s, energy, error rate),
    which have a real measurement path once KIO2_REAL_LLM_ENABLED=true, every
    metric emitted here is project-management-level (D1.1 Table 8/9) and has
    no real variant at all yet — it would require the actual KIO module
    (FocusTracer for "bugfix", or whichever team eventually owns KIO3/KIO4's
    real roles) to report it for real. So source is unconditionally
    "simulated" here, never derived from data_source."""
    if not KIO_REAL_KPI_ROLE:
        return
    labels_kpi = {**labels, "source": "simulated"}

    if KIO_REAL_KPI_ROLE == "bugfix":
        # KIO2 (Bug Locate & Fix / LLM Debugger).
        # KPI 6.1 — Bug-fix time: baseline ~8-12h, target <=20% reduction.
        bugfix_duration_hist.record(round(random.uniform(6.0, 10.0), 2), labels_kpi)
        # KPI 1.2 — Issue resolution speed: baseline ~8-12h, target <=30% reduction.
        issue_resolution_hist.record(round(random.uniform(5.0, 9.0), 2), labels_kpi)
        # WP3 task metric — Dynamic slicing success rate: target >=85%, realistic
        # variance means it dips below target sometimes rather than always "passing".
        slicing_success_hist.record(round(random.uniform(0.75, 0.97), 3), labels_kpi)
        # KPI 6.2 — Customer-reported issues: rare event, not one per request.
        if is_error and random.random() < 0.05:
            customer_reported_counter.add(random.randint(1, 2), labels_kpi)
        # "Accuracy" KPI (fix@1) — see _evaluate_fix_success()'s docstring for
        # why this is a placeholder, not yet a real test-suite result.
        outcome = "success" if _evaluate_fix_success() else "failure"
        fix_attempt_counter.add(1, {**labels_kpi, "outcome": outcome})

    elif KIO_REAL_KPI_ROLE in ("nlp-requirements", "architecture-to-code"):
        # KIO3 (NLP -> Formal Requirements) / KIO4 (Architecture-to-Code Planner).
        # KPI 1.1 — Code generation speed: baseline ~100-120 min (100%),
        # target <=70% (~70-84 min). Simulated mostly under baseline, with
        # realistic variance rather than always beating target.
        codegen_duration_hist.record(round(random.uniform(65.0, 95.0), 1), labels_kpi)
        # KPI 3.1 — Code quality improvement: baseline 100%, target <=70%.
        code_quality_hist.record(round(random.uniform(65.0, 90.0), 1), labels_kpi)
        if KIO_REAL_KPI_ROLE == "architecture-to-code":
            # KPI 3.2 — Review score increase: baseline ~3.5/5, target ~4.2/5.
            review_score_hist.record(round(random.uniform(3.6, 4.4), 2), labels_kpi)

    elif KIO_REAL_KPI_ROLE == "ai-sysdev":
        # KIO7 (AI-SysDev). D1.1 ties KIO7 to most KPIs; only the ones where
        # KIO7 is a clear primary owner (and not already covered by kio2-sim/
        # kio3/kio4 above) are simulated here.
        # KPI 4.1 — Developer productivity: baseline ~0.5-0.8 features/day,
        # target increase.
        dev_productivity_hist.record(round(random.uniform(0.6, 1.1), 2), labels_kpi)
        # KPI 5.1 — Time-to-market: baseline ~30-45 days, target <=20% reduction.
        time_to_market_hist.record(round(random.uniform(24.0, 38.0), 1), labels_kpi)
        # KPI 7.1 — Annual cost saving: target range, realistic variance.
        cost_saving_hist.record(round(random.uniform(12.0, 28.0), 1), labels_kpi)
        # KPI 9.1 — Refactoring effort reduction: baseline ~4-6h/feature.
        refactoring_hist.record(round(random.uniform(2.5, 4.5), 2), labels_kpi)
        # KPI 9.2 — Technical debt reduction: baseline ~3-5h/100loc.
        tech_debt_hist.record(round(random.uniform(1.8, 3.5), 2), labels_kpi)

    elif KIO_REAL_KPI_ROLE == "green-deploy":
        # KIO8 (Cross-Architecture / Energy-Efficient Deploy).
        # KPI 2.1 — Lifecycle energy reduction: baseline 100%, target <=85%
        # (>=15% reduction). Realistic variance, occasionally short of target.
        lifecycle_energy_hist.record(round(random.uniform(78.0, 96.0), 1), labels_kpi)
        # KPI 2.2 — Deployment energy efficiency (tokens/s/W): reported as a
        # concrete absolute number rather than "% of baseline" for the same
        # dashboard-legibility reason as the KIO2/KIO3/KIO4 metrics above; an
        # assumed unoptimized baseline of ~7.5 tok/s/W, target >=15% improvement.
        deploy_energy_eff_hist.record(round(random.uniform(6.5, 10.5), 2), labels_kpi)
        # KPI 8.3 — Cross-Architecture Build Success Rate: target >=1 verified
        # heterogeneous target (FPGA/ARM/RISC-V); rare, discrete event, not
        # something that happens on every simulated request.
        if random.random() < 0.05:
            cross_arch_build_counter.add(1, labels_kpi)

    elif KIO_REAL_KPI_ROLE == "adoption":
        # KIO13 (Adoption & Usage Tracking).
        # KPI 8.1 — Adoption rate: baseline 0%, target >=50% within 2 pilot
        # sprints. Simulated mid-ramp, since a fixed pilot has no "day zero".
        adoption_rate_hist.record(round(random.uniform(32.0, 58.0), 1), labels_kpi)
        # KPI 8.2 — Active usage & satisfaction: baseline usage ~0%/MOS ~3.0,
        # target usage >=60% / MOS >=4.0.
        adoption_usage_hist.record(round(random.uniform(45.0, 68.0), 1), labels_kpi)
        adoption_mos_hist.record(round(random.uniform(3.4, 4.3), 2), labels_kpi)


# --------------------------------------------------------------------------- #
# Background heartbeat (Contract §2.3)
# --------------------------------------------------------------------------- #
def heartbeat_loop():
    while not _stop.is_set():
        heartbeat_counter.add(1, {"kio.id": KIO_ID, "llm": KIO_LLM, "task_type": TASK_TYPE})
        _stop.wait(HEARTBEAT_INTERVAL_S)


# --------------------------------------------------------------------------- #
# Simulated request workload
# --------------------------------------------------------------------------- #
def simulate_request(session_id: str | None = None):
    """Runs one request. `session_id` is normally generated here (internal-
    timer mode); when driven by the NATS consumer it's instead the id carried
    by the incoming KIOEnvelope, so telemetry binds to the session the
    Workflow API/Session Manager are already tracking. Returns a small dict
    ({"status": "ok"|"error", "output": {...}, "error": ...}) so a caller
    (the NATS consumer) can report the outcome back as a KIOResult."""
    session_id = session_id or str(uuid.uuid4())
    labels = {"kio.id": KIO_ID, "llm": KIO_LLM, "task_type": TASK_TYPE}
    active_sessions.add(1, labels)
    try:
        real_result = None
        real_joules = None
        if KIO2_REAL_LLM_ENABLED:
            real_result, real_joules = _measure_energy_during(
                lambda: _call_ollama_real(_build_debug_prompt())
            )

        # "source" reflects which MODE this KIO is running in (real Ollama
        # attempted vs fully simulated) — not whether this one call happened
        # to succeed. See KIO Detail's "Data source" panel.
        data_source = "real" if KIO2_REAL_LLM_ENABLED else "simulated"
        labels_src = {**labels, "source": data_source}

        if real_result is not None and real_result["ok"]:
            # REAL SUCCESS — every number below is measured from the actual
            # Ollama response, nothing fabricated.
            work_s = max(real_result["duration_s"], 0.01)
            in_tokens = real_result["input_tokens"] or 0
            out_tokens = real_result["output_tokens"] or 0
            tps = real_result["tokens_per_second"]
            is_error = False
            error_type = None
        elif real_result is not None and not real_result["ok"]:
            # REAL FAILURE — the Ollama call was genuinely attempted and
            # genuinely failed (unreachable / timeout / bad model / HTTP
            # error). This IS the request's real outcome: record it as a real
            # error with the real reason, instead of quietly generating a
            # fresh simulated "successful" request on top of it — that used
            # to hide real Ollama downtime from the error-rate metric.
            work_s = max(real_result["duration_s"], 0.01)
            in_tokens = 0
            out_tokens = 0
            tps = 0.0
            is_error = True
            error_type = real_result["error_type"]
        else:
            # Real path fully disabled (KIO2_REAL_LLM_ENABLED=false) -> the
            # original dummy simulation, unchanged.
            work_s = random.uniform(0.2, 2.5)
            time.sleep(min(work_s, 0.4))  # keep loop responsive; report full latency below

            in_tokens = random.randint(150, 1200)
            out_tokens = random.randint(80, 2000)
            tps = out_tokens / max(work_s, 0.05)
            is_error = random.random() < 0.07  # ~7% simulated error rate
            error_type = random.choice(["timeout", "internal", "rate_limit"]) if is_error else None

        llm_token_counter.add(in_tokens, {**labels_src, "direction": "input"})
        llm_token_counter.add(out_tokens, {**labels_src, "direction": "output"})
        tokens_per_second.record(round(tps, 2), labels_src)

        if real_joules is not None:
            joules = real_joules  # real, integrated GPU power draw over the real call
        else:
            joules = out_tokens * LLM_JOULES_PER_TOKEN.get(KIO_LLM, 2.0) * random.uniform(0.85, 1.15)
        energy_counter.add(round(joules, 2), labels_src)

        if KIO2_REAL_LLM_ENABLED:
            # Best-effort — only recorded when a real power-exporter/NVML
            # source is actually reachable; silently skipped otherwise (no
            # fabricated temperature, unlike the dummy energy formula above).
            gpu_temp_c = _read_gpu_temperature_celsius()
            if gpu_temp_c is not None:
                gpu_temp_hist.record(round(gpu_temp_c, 1), labels_src)

        cost = (in_tokens + out_tokens) / 1000.0 * LLM_COST_PER_1K.get(KIO_LLM, 0.0)
        if cost:
            llm_cost_counter.add(round(cost, 6), labels_src)

        latency_ms = work_s * 1000.0
        emit_trace(session_id, work_s, in_tokens, out_tokens, is_error, error_type)

        if is_error:
            error_counter.add(1, {**labels_src, "error_type": error_type})
            request_counter.add(1, {**labels_src, "status": "error"})
            request_duration.record(latency_ms, labels_src)
            reason = "REAL Ollama error" if data_source == "real" else "simulated"
            summary_text = f"Request failed on session {session_id}: {reason} ({error_type})"
            logger.warning(
                summary_text,
                extra={"kio.id": KIO_ID, "session.id": session_id, "llm": KIO_LLM, "task_type": TASK_TYPE},
            )
        else:
            request_counter.add(1, {**labels_src, "status": "ok"})
            request_duration.record(latency_ms, labels_src)
            # Always source="simulated" here, even when data_source=="real":
            # unlike tok/s/energy/error-rate above, accuracy (fix@1 stand-in)
            # has no real measurement path yet at all (would need FocusTracer
            # to actually run/verify a fix) — tagging it "real" just because
            # the Ollama call itself was real would be misleading. See Section
            # 9.7/9.8 for why this stays a placeholder.
            accuracy_hist.record(round(random.uniform(0.6, 0.99), 3), {**labels_src, "source": "simulated"})
            if real_result is not None and real_result.get("ok") and real_result.get("text"):
                summary_text = f"[real qwen2.5:3b via Ollama] {real_result['text'].strip()[:400]}"
                logger.info(summary_text, extra={"kio.id": KIO_ID, "session.id": session_id,
                                                  "llm": KIO_LLM, "task_type": TASK_TYPE})
            else:
                summary_text = emit_unstructured(session_id, out_tokens)

        # Langfuse: parallel LLM-specific stream, same session_id as the OTel
        # trace above so both systems can be correlated by a human.
        emit_langfuse_trace(session_id, in_tokens, out_tokens, cost, is_error, error_type, summary_text)

        # Real-project KPIs (D1.1) — no-op unless KIO_REAL_KPI_ROLE is set.
        emit_real_kpi_metrics(labels, is_error)

        return {
            "status": "error" if is_error else "ok",
            "output": {"tokens_out": out_tokens, "summary": summary_text[:200]},
            "error": error_type,
        }
    finally:
        active_sessions.add(-1, labels)


# --------------------------------------------------------------------------- #
# NATS-driven trigger mode (orchestration layer, NATS_ENABLED=true).
# --------------------------------------------------------------------------- #
async def handle_task_envelope(nc, msg_data: bytes) -> dict:
    """Core of the NATS task handler, factored out of _nats_consumer_main so
    it's directly unit-testable against a fake `nc` (anything with an async
    `publish(subject, bytes)`), without needing a real NATS server. Parses
    one KIOEnvelope, runs simulate_request() off-loop, publishes the
    KIOResult back, and returns the result dict for assertions in tests."""
    try:
        envelope = KIOEnvelope.from_json(msg_data)
    except Exception:
        logger.warning("Malformed KIOEnvelope, dropping", exc_info=True)
        return {"status": "error", "output": {}, "error": "malformed_envelope"}

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, simulate_request, envelope.session_id)
    except Exception as exc:
        logger.exception("simulate_request() raised while handling a NATS task")
        result = {"status": "error", "output": {}, "error": f"handler_exception: {exc}"}

    kio_result = KIOResult(
        session_id=envelope.session_id,
        kio_id=KIO_ID,
        envelope_id=envelope.envelope_id,
        status=result["status"],
        output=result.get("output", {}),
        error=result.get("error"),
    )
    try:
        await nc.publish(result_subject(KIO_ID), kio_result.to_json().encode())
    except Exception:
        logger.warning("Failed to publish KIOResult back to NATS", exc_info=True)
    return result


async def _nats_consumer_main():
    """Subscribes to kio.tasks.<KIO_ID> and drives handle_task_envelope() for
    every message, then publishes a KIOResult back on kio.results.<KIO_ID>
    for the Planner to register as lineage. No silent fallback here: if NATS
    itself is unreachable there is no meaningful degraded mode, so a
    connection failure is logged and re-raised — Docker's
    `restart: unless-stopped` handles the retry."""
    import nats

    nc = await nats.connect(NATS_URL, connect_timeout=10)
    subject = task_subject(KIO_ID)

    async def _on_task(msg):
        await handle_task_envelope(nc, msg.data)

    await nc.subscribe(subject, cb=_on_task)
    logger.info(f"NATS consumer listening on {subject}", extra={
        "kio.id": KIO_ID, "session.id": "bootstrap", "llm": KIO_LLM, "task_type": TASK_TYPE,
    })
    try:
        while not _stop.is_set():
            await asyncio.sleep(1)
    finally:
        await nc.close()


def _internal_timer_loop():
    """Baseline demo/simulation loop. Runs unconditionally (in a background
    thread) regardless of NATS_ENABLED — this is what actually keeps kio3/kio4
    producing continuous telemetry. Enabling NATS_ENABLED adds an ADDITIONAL,
    independent trigger path (handle_task_envelope(), for on-demand dispatch
    via the Workflow API/Planner) on top of this; it was never meant to
    replace it, and the first cut of the NATS integration wrongly made it an
    either/or — with nothing actually calling the Workflow API, kio3/kio4 went
    completely quiet ("No data" on every panel). Fixed 2026-08."""
    while not _stop.is_set():
        simulate_request()
        _stop.wait(REQUEST_INTERVAL_S * random.uniform(0.5, 1.5))


def main():
    def _shutdown(*_):
        _stop.set()
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info(
        f"KIO {KIO_ID} online: llm={KIO_LLM} task={TASK_TYPE} -> {ENDPOINT}",
        extra={"kio.id": KIO_ID, "session.id": "bootstrap", "llm": KIO_LLM, "task_type": TASK_TYPE},
    )
    hb = threading.Thread(target=heartbeat_loop, daemon=True)
    hb.start()

    timer_thread = threading.Thread(target=_internal_timer_loop, daemon=True)
    timer_thread.start()

    if NATS_ENABLED:
        logger.info(f"{KIO_ID}: NATS-driven mode ALSO enabled (on top of the internal timer), connecting to {NATS_URL}")
        asyncio.run(_nats_consumer_main())
    else:
        timer_thread.join()

    log_provider.shutdown()
    trace_provider.shutdown()
    if langfuse_client is not None:
        langfuse_client.shutdown()
    print(f"{KIO_ID} shutting down.")


if __name__ == "__main__":
    main()
