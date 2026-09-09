# Metrics query API. Build context is the repo root (see docker-compose.yml)
# so the metrics_api/ package can be copied as a whole, matching how
# orchestrator/Dockerfile is set up.
#
# Note kio_simulator.py is NOT needed here: the metric registry is generated
# from it at development time (scripts/generate_metric_registry.py) and
# committed as metrics_api/registry_data.py.
FROM python:3.11-slim

WORKDIR /app

COPY metrics_api/requirements.txt ./metrics_api/requirements.txt
RUN pip install --no-cache-dir -r metrics_api/requirements.txt

COPY metrics_api/ ./metrics_api/

ENV PYTHONPATH=/app

CMD ["uvicorn", "metrics_api.app:app", "--host", "0.0.0.0", "--port", "8081"]
