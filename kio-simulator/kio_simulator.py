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
    sequence of an operation ("işlem sırası").

Real LLMs are NOT invoked; values are randomly generated. One KIO of type
"code-analysis" additionally reports real line/directory counts of a scanned
repo path so at least one KIO carries genuine structured extra data.

Config comes from environment variables (see docker-compose.yml).
"""
import os
import random
import signal
import threading
import time
import uuid

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
EXPORT_INTERVAL_MS = int(os.environ.get("EXPORT_INTERVAL_MS", "5000"))
HEARTBEAT_INTERVAL_S = int(os.environ.get("HEARTBEAT_INTERVAL_S", "60"))
REQUEST_INTERVAL_S = float(os.environ.get("REQUEST_INTERVAL_S", "3"))
REPO_SCAN_PATH = os.environ.get("REPO_SCAN_PATH", "/app")

# Real-project KPI role (D1.1 Project Management Handbook). Empty by default;
# set to "bugfix" only for the KIO(s) mapped to KIO2 (Bug Locate & Fix / LLM
# Debugger) in D1.1's traceability matrix. Other real-KPI roles (e.g. for
# KIO3/KIO4/KIO7) can be added the same way later without touching this file's
# core logic — see docs/AI4SWENG_KPI_Metrik_Referansi.docx.
KIO_REAL_KPI_ROLE = os.environ.get("KIO_REAL_KPI_ROLE", "")

# LLM pricing per 1K tokens (dummy) so cost varies believably by model.
LLM_COST_PER_1K = {
    "qwen2.5:3b": 0.0,
    "llama3.1:8b": 0.0,
    "gpt-4o-mini": 0.00060,
    "gpt-4o": 0.01000,
    "claude-sonnet": 0.00300,
}

# Approx. GPU joules per output token (dummy), bigger models cost more energy.
LLM_JOULES_PER_TOKEN = {
    "qwen2.5:3b": 0.8,
    "llama3.1:8b": 2.1,
    "gpt-4o-mini": 3.5,
    "gpt-4o": 9.0,
    "claude-sonnet": 6.0,
}

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
else:
    bugfix_duration_hist = issue_resolution_hist = slicing_success_hist = customer_reported_counter = None

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
# step-by-step sequence of the operation ("işlem sırası" in the KIO Detail
# dashboard, via Tempo).
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
    """D1.1-aligned KPIs for KIOs with a "bugfix" real-KPI role (KIO2 today).
    Simulated values, deliberately kept inside D1.1's baseline/target bands so
    the dashboard reads like plausible pilot-sprint progress, not noise."""
    if KIO_REAL_KPI_ROLE != "bugfix":
        return
    # KPI 6.1 — Bug-fix time: baseline ~8-12h, target <=20% reduction.
    bugfix_duration_hist.record(round(random.uniform(6.0, 10.0), 2), labels)
    # KPI 1.2 — Issue resolution speed: baseline ~8-12h, target <=30% reduction.
    issue_resolution_hist.record(round(random.uniform(5.0, 9.0), 2), labels)
    # WP3 task metric — Dynamic slicing success rate: target >=85%, realistic
    # variance means it dips below target sometimes rather than always "passing".
    slicing_success_hist.record(round(random.uniform(0.75, 0.97), 3), labels)
    # KPI 6.2 — Customer-reported issues: rare event, not one per request.
    if is_error and random.random() < 0.05:
        customer_reported_counter.add(random.randint(1, 2), labels)


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
def simulate_request():
    session_id = str(uuid.uuid4())
    labels = {"kio.id": KIO_ID, "llm": KIO_LLM, "task_type": TASK_TYPE}
    active_sessions.add(1, labels)
    try:
        # ~ dummy work
        work_s = random.uniform(0.2, 2.5)
        time.sleep(min(work_s, 0.4))  # keep loop responsive; report full latency below

        in_tokens = random.randint(150, 1200)
        out_tokens = random.randint(80, 2000)
        tps = out_tokens / max(work_s, 0.05)
        is_error = random.random() < 0.07  # ~7% error rate
        error_type = random.choice(["timeout", "internal", "rate_limit"]) if is_error else None

        llm_token_counter.add(in_tokens, {**labels, "direction": "input"})
        llm_token_counter.add(out_tokens, {**labels, "direction": "output"})
        tokens_per_second.record(round(tps, 2), labels)

        joules = out_tokens * LLM_JOULES_PER_TOKEN.get(KIO_LLM, 2.0) * random.uniform(0.85, 1.15)
        energy_counter.add(round(joules, 2), labels)

        cost = (in_tokens + out_tokens) / 1000.0 * LLM_COST_PER_1K.get(KIO_LLM, 0.0)
        if cost:
            llm_cost_counter.add(round(cost, 6), labels)

        latency_ms = work_s * 1000.0
        emit_trace(session_id, work_s, in_tokens, out_tokens, is_error, error_type)

        if is_error:
            error_counter.add(1, {**labels, "error_type": error_type})
            request_counter.add(1, {**labels, "status": "error"})
            request_duration.record(latency_ms, labels)
            summary_text = f"Request failed on session {session_id}: simulated {error_type}"
            logger.warning(
                summary_text,
                extra={"kio.id": KIO_ID, "session.id": session_id, "llm": KIO_LLM, "task_type": TASK_TYPE},
            )
        else:
            request_counter.add(1, {**labels, "status": "ok"})
            request_duration.record(latency_ms, labels)
            accuracy_hist.record(round(random.uniform(0.6, 0.99), 3), labels)
            summary_text = emit_unstructured(session_id, out_tokens)

        # Langfuse: parallel LLM-specific stream, same session_id as the OTel
        # trace above so both systems can be correlated by a human.
        emit_langfuse_trace(session_id, in_tokens, out_tokens, cost, is_error, error_type, summary_text)

        # Real-project KPIs (D1.1) — only emits anything for "bugfix"-role KIOs.
        emit_real_kpi_metrics(labels, is_error)
    finally:
        active_sessions.add(-1, labels)


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

    while not _stop.is_set():
        simulate_request()
        _stop.wait(REQUEST_INTERVAL_S * random.uniform(0.5, 1.5))

    log_provider.shutdown()
    trace_provider.shutdown()
    if langfuse_client is not None:
        langfuse_client.shutdown()
    print(f"{KIO_ID} shutting down.")


if __name__ == "__main__":
    main()
