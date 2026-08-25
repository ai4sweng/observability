#!/usr/bin/env python3
"""
kio_otel — minimal, contract-faithful OpenTelemetry helper for a REAL KIO.

This is a drop-in starting point for a KIO team that has its OWN codebase and
wants to push telemetry to the AI4SWENG central platform (OTel Collector ->
VictoriaMetrics/VictoriaLogs/Tempo -> Grafana). It is deliberately small: it
implements ONLY the mandatory metric set from the Integration Contract (§2.1)
plus the 60s heartbeat (§2.3) and an optional per-request trace — nothing from
the internal simulator (no Langfuse, no NATS, no D1.1 KPIs, no random data).

Copy this single file into your project and wrap your real request handler with
`with kio.request(...) as req:` — see example_kio.py. Everything is configured
from environment variables, exactly the ones the contract lists, so pointing a
KIO at a different machine is a config change, never a code change:

    OTEL_EXPORTER_OTLP_ENDPOINT   e.g. http://192.168.1.50:4317  (gRPC, port 4317)
    OTEL_RESOURCE_ATTRIBUTES      e.g. service.name=kio1,service.version=1.0.0,kio.id=kio1,deployment.environment=production
    OTEL_EXPORTER_OTLP_HEADERS    e.g. Authorization=Bearer <token>   (only if the collector has auth enabled)

As a convenience for local/LAN testing, the resource attributes can instead be
given as discrete vars (KIO_ID / KIO_LLM / KIO_TASK_TYPE / SERVICE_VERSION);
OTEL_RESOURCE_ATTRIBUTES, when present, takes precedence and is authoritative
(that is what the contract standardizes on).

Requires: opentelemetry-api, opentelemetry-sdk, opentelemetry-exporter-otlp-proto-grpc
(see requirements.txt — pinned to the same versions the rest of the repo uses).
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Optional

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import Status, StatusCode

_log = logging.getLogger("kio_otel")


def _parse_resource_attributes() -> dict:
    """Build the OTel Resource attribute dict.

    Precedence (later wins): discrete convenience vars (KIO_ID, ...) are the
    base; OTEL_RESOURCE_ATTRIBUTES (the contract-standard var) is layered on
    top so it stays authoritative when both are set."""
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

    # Contract-standard var: "k1=v1,k2=v2". Authoritative — overrides the above.
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
    on it. Duration, request/error counting and the trace status are handled
    automatically by the context manager."""

    def __init__(self, telemetry: "KIOTelemetry", session_id: str):
        self._t = telemetry
        self.session_id = session_id

    def record_tokens(self, *, input: int = 0, output: int = 0) -> None:
        """kio.llm.token_count — direction=input|output (Contract §2.1)."""
        if input:
            self._t._llm_tokens.add(int(input), {**self._t._base, "direction": "input"})
        if output:
            self._t._llm_tokens.add(int(output), {**self._t._base, "direction": "output"})

    def record_cost_usd(self, amount: float) -> None:
        """kio.llm.cost_usd — estimated LLM cost in USD (Contract §2.1)."""
        if amount:
            self._t._llm_cost.add(float(amount), self._t._base)


class KIOTelemetry:
    """One instance per KIO process. Sets up the OTel metrics (and, optionally,
    traces) pipeline, exposes the mandatory instruments, and runs the heartbeat.

    Typical lifecycle:
        kio = KIOTelemetry()
        kio.start_heartbeat()
        ...
        with kio.request() as req:
            ...                 # your real work + req.record_tokens(...)
        ...
        kio.shutdown()          # flush before exit (critical for short-lived processes)
    """

    def __init__(self, *, enable_traces: bool = True):
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        export_interval_ms = int(os.environ.get("EXPORT_INTERVAL_MS", "5000"))
        self.heartbeat_interval_s = int(os.environ.get("HEARTBEAT_INTERVAL_S", "60"))
        # TLS off for plain http:// endpoints (LAN / Tailscale insecure mode);
        # https:// keeps TLS on. Same rule the rest of the repo uses.
        insecure = endpoint.startswith("http://")

        attrs = _parse_resource_attributes()
        self.kio_id = attrs["kio.id"]
        resource = Resource.create(attrs)

        # Base labels attached to every metric. kio.id / llm / task_type are
        # also promoted from the Resource by the collector, but including them
        # here keeps the series identical whichever path a value arrives by.
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

        # --- Mandatory metric set (Integration Contract §2.1) ---
        self._request_count = meter.create_counter("kio.request.count", unit="1")
        self._request_duration = meter.create_histogram("kio.request.duration_ms", unit="ms")
        self._error_count = meter.create_counter("kio.request.error_count", unit="1")
        self._llm_tokens = meter.create_counter("kio.llm.token_count", unit="tokens")
        self._llm_cost = meter.create_counter("kio.llm.cost_usd", unit="USD")
        self._active_sessions = meter.create_up_down_counter("kio.session.active_count", unit="1")
        self._heartbeat = meter.create_counter("kio.heartbeat", unit="1")

        # --- Optional traces pipeline (powers the trace waterfall view) ---
        self._tracer = None
        self._tracer_provider = None
        if enable_traces:
            self._tracer_provider = TracerProvider(resource=resource)
            self._tracer_provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=insecure))
            )
            trace.set_tracer_provider(self._tracer_provider)
            self._tracer = trace.get_tracer("kio.instrumentation")

        self._stop = threading.Event()
        self._hb_thread: Optional[threading.Thread] = None
        _log.info("kio_otel ready: kio.id=%s -> %s (insecure=%s)", self.kio_id, endpoint, insecure)

    # ----------------------------------------------------------------- #
    # Heartbeat (Contract §2.3): tick every 60s; >120s silence == stale.
    # ----------------------------------------------------------------- #
    def start_heartbeat(self) -> None:
        if self._hb_thread is not None:
            return

        def _loop():
            while not self._stop.is_set():
                self._heartbeat.add(1, self._base)
                self._stop.wait(self.heartbeat_interval_s)

        self._hb_thread = threading.Thread(target=_loop, daemon=True, name="kio-heartbeat")
        self._hb_thread.start()

    # ----------------------------------------------------------------- #
    # Per-request instrumentation.
    # ----------------------------------------------------------------- #
    @contextmanager
    def request(self, session_id: Optional[str] = None):
        """Wrap one unit of work (one KIO 'request'). On normal exit the
        request is counted status=ok; on exception it is counted status=error
        with a bounded error_type, the exception is re-raised, and the active
        session count is always released. Yields a _Request for recording LLM
        token/cost usage."""
        session_id = session_id or str(uuid.uuid4())
        self._active_sessions.add(1, self._base)
        start = time.monotonic()
        span = None
        if self._tracer is not None:
            span = self._tracer.start_span(
                "kio.request",
                attributes={**self._base, "session.id": session_id},
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
    # Direct access to the raw instruments, for anything the context
    # manager above doesn't cover (custom G7 self-service metrics, etc.).
    # ----------------------------------------------------------------- #
    @property
    def meter(self):
        return metrics.get_meter("kio.instrumentation")

    @property
    def base_labels(self) -> dict:
        return dict(self._base)

    # ----------------------------------------------------------------- #
    # Flush and stop. MUST be called before a short-lived process exits,
    # or the last export interval's data (and the final heartbeat) is lost.
    # ----------------------------------------------------------------- #
    def shutdown(self) -> None:
        self._stop.set()
        try:
            self._meter_provider.shutdown()  # force-flushes buffered metrics
        except Exception:
            _log.warning("metric provider shutdown failed", exc_info=True)
        if self._tracer_provider is not None:
            try:
                self._tracer_provider.shutdown()
            except Exception:
                _log.warning("tracer provider shutdown failed", exc_info=True)
