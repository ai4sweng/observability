#!/usr/bin/env python3
"""
GPU power exporter — run this NATIVELY on the host (Windows or Linux, not in
Docker), next to Ollama. Exposes the current NVIDIA GPU power draw as a tiny
JSON HTTP endpoint that kio2 (running inside a Linux container) can poll.

Why this exists: a container has no visibility into the host's GPU unless
it's been explicitly passed through. Since Ollama runs natively on the host
(simplest, most reliable setup for a single dev machine, Windows or Linux),
the actual power draw happens on the host — so this script reads it there via
NVML and serves it over plain HTTP on localhost, which the container can
reach through the `host.docker.internal` DNS name (works out of the box on
Docker Desktop; on native Linux Docker Engine, docker-compose.yml maps it via
`extra_hosts: host-gateway`).

Usage (same on both platforms):
    pip install nvidia-ml-py   # provides `import pynvml` — NVIDIA's current official package
    python power_exporter.py            # serves on 0.0.0.0:9400 by default
    python power_exporter.py --port 9400 --gpu-index 0

Then set in docker-compose.yml (kio2's environment):
    GPU_POWER_EXPORTER_URL: "http://host.docker.internal:9400/power"

Response format:
    GET /power  ->  {"watts": 87.3, "gpu_index": 0, "temperature_c": 61.0}

("temperature_c" added 2026-08 for the KIO Detail dashboard's GPU temperature
gauge; kio_simulator.py treats it as optional — if a given exporter/NVML
version doesn't return it, the field is just omitted, no crash.)
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    import pynvml
except ImportError:
    raise SystemExit("nvidia-ml-py is required: pip install nvidia-ml-py")


def make_handler(gpu_index):
    pynvml.nvmlInit()
    handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_index)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # keep stdout quiet; this runs unattended next to Ollama

        def do_GET(self):
            if self.path != "/power":
                self.send_response(404)
                self.end_headers()
                return
            try:
                milliwatts = pynvml.nvmlDeviceGetPowerUsage(handle)
                payload = {"watts": milliwatts / 1000.0, "gpu_index": gpu_index}
                try:
                    payload["temperature_c"] = float(
                        pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                    )
                except Exception:
                    pass  # older driver/NVML without temperature support — watts still returned
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(exc).encode("utf-8"))

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9400)
    parser.add_argument("--gpu-index", type=int, default=0)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    handler = make_handler(args.gpu_index)
    server = HTTPServer((args.host, args.port), handler)
    print(f"power_exporter listening on http://{args.host}:{args.port}/power (GPU {args.gpu_index})")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
