#!/usr/bin/env python3
"""
kio_otel — minimal, contract-faithful OpenTelemetry helper for a real KIO.

A drop-in starting point for a KIO team that has its OWN code and wants to push
telemetry to the AI4SWENG central platform (OTel Collector -> VictoriaMetrics /
VictoriaLogs / Tempo -> Grafana). It implements ONLY the mandatory metric set
from the Integration Guide (§5), the 60s heartbeat, an optional per-request
trace, and an optional log stream — nothing else (no Langfuse, no NATS, no
random data).

Everything is configured from environment variables, so pointing a KIO at a
different machine is a config change, never a code change:

    OTEL_EXPORTER_OTLP_ENDPOINT   e.g. http://192.168.1.50:5317  (gRPC, port 5317)
    OTEL_RESOURCE_ATTRIBUTES      e.g. service.name=kio1,service.version=1.0.0,kio.id=kio1,deployment.environment=production
    OTEL_EXPORTER_OTLP_HEADERS    e.g. Authorization=Bearer <token>   (the central collector requires this)

As a convenience the resource attributes can instead be given as discrete vars
(KIO_ID / KIO_LLM / KIO_TASK_TYPE / SERVICE_VERSION); OTEL_RESOURCE_ATTRIBUTES,
when present, wins.

Requires: opentelemetry-api, opentelemetry-sdk, opentelemetry-exporter-otlp-proto-grpc
(see requirements.txt).
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Optional

from opentelemetry import metrics, trace, _logs
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.trace import Status, StatusCode

_log = logging.getLogger("kio_otel")


def _parse_resource_attributes() -> dict:
    """Build the OTel Resource attribute dict. Discrete convenience vars
    (KIO_ID, ...) are the base; OTEL_RESOURCE_ATTRIBUTES (the guide-standard
    var) is layered on top so it stays authoritative when both are set."""
    attrs = {}

    kio_id = os.environ.get("KIO_ID")
    if kio_id:
        attrs["service.name"] = kio_id
        attrs["kio.id"] = kio_id
    if os.environ.get("SERVICE_VERSION"):
        attrs["service.version"] = os.environ["SERVICE_VERSION"]
    if os.environ.get("DEPLOYMENT_ENVIRONMENT"):
        attrs["deployment.environment"] = os.environ["DEPLOYMENT_ENVIRONMENT"]
    if os.environ.get("KIO_LLM"):
        attrs["llm"] = os.environ["KIO_LLM"]
    if os.environ.get("KIO_TASK_TYPE"):
        attrs["task_type"] = os.environ["KIO_TASK_TYPE"]

    raw = os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            attrs[k.strip()] = v.strip()

    attrs.setdefault("service.name", attrs.get("kio.id", "kioX"))
    attrs.setdefault("kio.id", attrs.get("service.name", "kioX"))
    attrs.setdefault("service.version", "0.0.0")
    attrs.setdefault("deployment.environment", "production")
    return attrs


class _Request:
    """Handle yielded by KIOTelemetry.request(); the caller records LLM usage
    on it. Duration, request/error counting and trace status are automatic."""

    def __init__(self, telemetry: "KIOTelemetry", session_id: str):
        self._t = telemetry
        self.session_id = session_id

    def record_tokens(self, *, input: int = 0, output: int = 0) -> None:
        """kio.llm.token_count — direction=input|output."""
        if input:
            self._t._llm_tokens.add(int(input), {**self._t._base, "direction": "input"})
        if output:
            self._t._llm_tokens.add(int(output), {**self._t._base, "direction": "output"})

    def record_cost_usd(self, amount: float) -> None:
        """kio.llm.cost_usd — estimated LLM cost in USD."""
        if amount:
            self._t._llm_cost.add(float(amount), self._t._base)


class KIOTelemetry:
    """One instance per KIO process. Sets up metrics (+ optional traces/logs),
    exposes the mandatory instruments, and runs the heartbeat.

        kio = KIOTelemetry()
        kio.start_heartbeat()
        with kio.request() as req:
            ...                 # your work + req.record_tokens(...)
            kio.log("did a thing")
        kio.shutdown()          # flush before exit
    """

    def __init__(self, *, enable_traces: bool = True, enable_logs: bool = True):
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:5317")
        export_interval_ms = int(os.environ.get("EXPORT_INTERVAL_MS", "5000"))
        self.heartbeat_interval_s = int(os.environ.get("HEARTBEAT_INTERVAL_S", "60"))
        # TLS off for plain http:// (LAN / Tailscale / VPN); https:// keeps TLS on.
        insecure = endpoint.startswith("http://")

        attrs = _parse_resource_attributes()
        self.kio_id = attrs["kio.id"]
        resource = Resource.create(attrs)

        self._base = {"kio.id": self.kio_id}
        if "llm" in attrs:
            self._base["llm"] = attrs["llm"]
        if "task_type" in attrs:
            self._base["task_type"] = attrs["task_type"]

        # --- Metrics pipeline ---
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=endpoint, insecure=insecure),
            export_interval_millis=export_interval_ms,
        )
        self._meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(self._meter_provider)
        meter = metrics.get_meter("kio.instrumentation")

        # --- Mandatory metric set (Integration Guide §5) ---
        self._request_count = meter.create_counter("kio.request.count", unit="1")
        self._request_duration = meter.create_histogram("kio.request.duration_ms", unit="ms")
        self._error_count = meter.create_counter("kio.request.error_count", unit="1")
        self._llm_tokens = meter.create_counter("kio.llm.token_count", unit="tokens")
        self._llm_cost = meter.create_counter("kio.llm.cost_usd", unit="USD")
        self._active_sessions = meter.create_up_down_counter("kio.session.active_count", unit="1")
        self._heartbeat = meter.create_counter("kio.heartbeat", unit="1")

        # --- Optional traces pipeline ---
        self._tracer = None
        self._tracer_provider = None
        if enable_traces:
            self._tracer_provider = TracerProvider(resource=resource)
            self._tracer_provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=insecure))
            )
            trace.set_tracer_provider(self._tracer_provider)
            self._tracer = trace.get_tracer("kio.instrumentation")

        # --- Optional logs pipeline (string telemetry -> VictoriaLogs) ---
        self._log_provider = None
        self._logger = None
        if enable_logs:
            self._log_provider = LoggerProvider(resource=resource)
            self._log_provider.add_log_record_processor(
                BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint, insecure=insecure))
            )
            _logs.set_logger_provider(self._log_provider)
            self._logger = logging.getLogger(self.kio_id)
            self._logger.setLevel(logging.INFO)
            self._logger.propagate = False
            self._logger.addHandler(LoggingHandler(level=logging.INFO, logger_provider=self._log_provider))

        self._stop = threading.Event()
        self._hb_thread: Optional[threading.Thread] = None
        _log.info("kio_otel ready: kio.id=%s -> %s (insecure=%s)", self.kio_id, endpoint, insecure)

    # ----------------------------------------------------------------- #
    def start_heartbeat(self) -> None:
        """Heartbeat: tick every 60s; >120s silence == stale in the registry."""
        if self._hb_thread is not None:
            return

        def _loop():
            while not self._stop.is_set():
                self._heartbeat.add(1, self._base)
                self._stop.wait(self.heartbeat_interval_s)

        self._hb_thread = threading.Thread(target=_loop, daemon=True, name="kio-heartbeat")
        self._hb_thread.start()

    # ----------------------------------------------------------------- #
    def log(self, message: str, **attributes) -> None:
        """Emit one unstructured log line -> VictoriaLogs (and nowhere else if
        logs are disabled). kio.id / session.id are attached automatically;
        keep any extra attributes bounded (no unique ids, no secrets)."""
        if self._logger is None:
            return
        extra = {"kio.id": self.kio_id, **attributes}
        self._logger.info(message, extra=extra)

    # ----------------------------------------------------------------- #
    @contextmanager
    def request(self, session_id: Optional[str] = None):
        """Wrap one unit of work. Normal exit -> status=ok; an exception ->
        status=error with a bounded error_type, then re-raised; the active
        session count is always released."""
        session_id = session_id or str(uuid.uuid4())
        self._active_sessions.add(1, self._base)
        start = time.monotonic()
        span = None
        if self._tracer is not None:
            span = self._tracer.start_span(
                "kio.request", attributes={**self._base, "session.id": session_id}
            )
        req = _Request(self, session_id)
        try:
            yield req
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000.0
            error_type = type(exc).__name__[:40] or "internal"
            self._error_count.add(1, {**self._base, "error_type": error_type})
            self._request_count.add(1, {**self._base, "status": "error"})
            self._request_duration.record(duration_ms, self._base)
            if span is not None:
                span.set_status(Status(StatusCode.ERROR, error_type))
                span.end()
            raise
        else:
            duration_ms = (time.monotonic() - start) * 1000.0
            self._request_count.add(1, {**self._base, "status": "ok"})
            self._request_duration.record(duration_ms, self._base)
            if span is not None:
                span.set_status(Status(StatusCode.OK))
                span.end()
        finally:
            self._active_sessions.add(-1, self._base)

    # ----------------------------------------------------------------- #
    @property
    def meter(self):
        """Raw meter, for custom (self-service) metrics beyond the mandatory 7."""
        return metrics.get_meter("kio.instrumentation")

    @property
    def base_labels(self) -> dict:
        return dict(self._base)

    # ----------------------------------------------------------------- #
    def shutdown(self) -> None:
        """Flush and stop. MUST be called before a short-lived process exits."""
        self._stop.set()
        for name, provider in (
            ("meter", self._meter_provider),
            ("tracer", self._tracer_provider),
            ("logger", self._log_provider),
        ):
            if provider is not None:
                try:
                    provider.shutdown()
                except Exception:
                    _log.warning("%s provider shutdown failed", name, exc_info=True)
