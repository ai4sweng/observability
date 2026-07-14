#!/usr/bin/env python3
"""
AI4SWENG KIO telemetry simulator.

Emits contract-compliant dummy telemetry over OTLP/gRPC for a single KIO:
  * the mandatory metric set (Integration Contract §2.1),
  * optional self-service metrics (tokens/sec, GPU energy, accuracy, repo stats),
  * a 60s heartbeat (§2.3),
  * an unstructured/string log stream (extension beyond the contract minimum).

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

# --------------------------------------------------------------------------- #
# Metrics pipeline
# --------------------------------------------------------------------------- #
metric_exporter = OTLPMetricExporter(endpoint=ENDPOINT, insecure=ENDPOINT.startswith("http://"))
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

# --------------------------------------------------------------------------- #
# Logs pipeline — unstructured / string telemetry
# --------------------------------------------------------------------------- #
log_provider = LoggerProvider(resource=resource)
log_provider.add_log_record_processor(
    BatchLogRecordProcessor(OTLPLogExporter(endpoint=ENDPOINT, insecure=ENDPOINT.startswith("http://")))
)
_logs.set_logger_provider(log_provider)

logger = logging.getLogger(KIO_ID)
logger.setLevel(logging.INFO)
logger.addHandler(LoggingHandler(level=logging.INFO, logger_provider=log_provider))
# Also echo to stdout for `docker logs`.
logger.addHandler(logging.StreamHandler())

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
    start = time.monotonic()
    try:
        # ~ dummy work
        work_s = random.uniform(0.2, 2.5)
        time.sleep(min(work_s, 0.4))  # keep loop responsive; report full latency below

        in_tokens = random.randint(150, 1200)
        out_tokens = random.randint(80, 2000)
        tps = out_tokens / max(work_s, 0.05)
        is_error = random.random() < 0.07  # ~7% error rate

        llm_token_counter.add(in_tokens, {**labels, "direction": "input"})
        llm_token_counter.add(out_tokens, {**labels, "direction": "output"})
        tokens_per_second.record(round(tps, 2), labels)

        joules = out_tokens * LLM_JOULES_PER_TOKEN.get(KIO_LLM, 2.0) * random.uniform(0.85, 1.15)
        energy_counter.add(round(joules, 2), labels)

        cost = (in_tokens + out_tokens) / 1000.0 * LLM_COST_PER_1K.get(KIO_LLM, 0.0)
        if cost:
            llm_cost_counter.add(round(cost, 6), labels)

        latency_ms = work_s * 1000.0

        if is_error:
            error_counter.add(1, {**labels, "error_type": random.choice(["timeout", "internal", "rate_limit"])})
            request_counter.add(1, {**labels, "status": "error"})
            request_duration.record(latency_ms, labels)
            logger.warning(
                f"Request failed on session {session_id}: simulated {random.choice(['timeout', 'internal', 'rate_limit'])}",
                extra={"kio.id": KIO_ID, "session.id": session_id, "llm": KIO_LLM, "task_type": TASK_TYPE},
            )
        else:
            request_counter.add(1, {**labels, "status": "ok"})
            request_duration.record(latency_ms, labels)
            accuracy_hist.record(round(random.uniform(0.6, 0.99), 3), labels)
            emit_unstructured(session_id, out_tokens)
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
    print(f"{KIO_ID} shutting down.")


if __name__ == "__main__":
    main()
