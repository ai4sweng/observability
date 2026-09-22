#!/usr/bin/env python3
"""
check_connectivity — preflight for a remote KIO, BEFORE touching your own code.

Two checks, in order:

  1) TCP reachability — can this machine open a socket to the central
     collector's OTLP gRPC port (host:5317)? Catches the common failures
     (wrong IP, firewall closed on the central machine, not on the same
     network/Tailscale) with a clear message instead of a silent "No data".

  2) Real OTLP export — actually sends ONE kio.heartbeat datapoint through the
     full path (collector -> VictoriaMetrics). If this machine can reach the
     port AND the collector accepts the export, you will see the id below show
     up in Grafana. This proves the whole pipeline end-to-end, not just the TCP
     handshake.

Usage:
    pip install -r requirements.txt
    OTEL_EXPORTER_OTLP_ENDPOINT=http://<central-ip>:5317 python check_connectivity.py
    #  (or put it in .env and load it first)

The test id defaults to "kio-preflight" so it never collides with a real KIO.
Override with KIO_ID if you want to test your assigned id specifically.
Exit code 0 = both checks passed, non-zero = something to fix (see output).
"""
import os
import socket
import sys
import time
from urllib.parse import urlparse


def _endpoint() -> str:
    return os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:5317")


def _host_port(endpoint: str):
    # Accept "http://host:5317", "host:5317", or bare "host".
    parsed = urlparse(endpoint if "://" in endpoint else f"//{endpoint}", scheme="")
    host = parsed.hostname or "localhost"
    port = parsed.port or 5317
    return host, port


def check_tcp(host: str, port: int, timeout: float = 5.0) -> bool:
    print(f"[1/2] TCP reachability  ->  {host}:{port}")
    try:
        with socket.create_connection((host, port), timeout=timeout):
            print(f"      OK — the port is open and reachable from this machine.\n")
            return True
    except OSError as exc:
        print(f"      FAIL — could not connect ({exc}).")
        print("      Fix ideas:")
        print("        - Is OTEL_EXPORTER_OTLP_ENDPOINT the CENTRAL machine's address, not localhost?")
        print("        - Is the central stack running (docker compose up) and port 5317 published?")
        print("        - Firewall on the CENTRAL machine must allow inbound 5317 (see NETWORK.md).")
        print("        - Same LAN, or both on Tailscale/VPN? (see NETWORK.md)\n")
        return False


def check_export(endpoint: str) -> bool:
    kio_id = os.environ.get("KIO_ID", "kio-preflight")
    print(f"[2/2] Real OTLP export  ->  one kio.heartbeat as kio.id={kio_id!r}")
    try:
        from opentelemetry import metrics
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.resources import Resource
    except ImportError as exc:
        print(f"      FAIL — OpenTelemetry packages not installed ({exc}).")
        print("      Run: pip install -r requirements.txt\n")
        return False

    insecure = endpoint.startswith("http://")
    resource = Resource.create({"service.name": kio_id, "kio.id": kio_id,
                                "deployment.environment": "preflight"})
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=endpoint, insecure=insecure),
        export_interval_millis=60000,  # long; we force_flush manually below
    )
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)
    meter = metrics.get_meter("kio.preflight")
    meter.create_counter("kio.heartbeat", unit="1").add(1, {"kio.id": kio_id})

    # force_flush returns True if the export completed within the timeout.
    ok = provider.force_flush(timeout_millis=10000)
    provider.shutdown()
    if ok:
        print("      OK — export accepted by the collector.")
        print(f"      Now open Grafana -> KIO Detail and look for kio.id={kio_id!r}")
        print("      in the KIO dropdown within ~30-60s (heartbeat + scrape delay).\n")
        return True
    print("      FAIL — export did not complete (collector unreachable or rejecting).")
    print("      The TCP check may still pass while the collector rejects the export,")
    print("      e.g. if TLS/auth is required — see NETWORK.md 'Security / TLS'.\n")
    return False


def main() -> int:
    endpoint = _endpoint()
    host, port = _host_port(endpoint)
    print(f"Central OTLP endpoint: {endpoint}\n")
    started = time.monotonic()

    tcp_ok = check_tcp(host, port)
    export_ok = check_export(endpoint) if tcp_ok else False

    print(f"Done in {time.monotonic() - started:.1f}s.")
    if tcp_ok and export_ok:
        print("RESULT: PASS — this machine can push telemetry to the central platform.")
        return 0
    print("RESULT: FAIL — fix the item(s) above, then re-run. See NETWORK.md.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
