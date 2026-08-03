# Shared image for both orchestrator services (workflow-api, planner) — same
# dependencies, different entrypoint via docker-compose's `command:`.
# Build context is the repo root (see docker-compose.yml) so this can copy
# the orchestrator/ package as a whole.
FROM python:3.11-slim

WORKDIR /app

COPY orchestrator/requirements.txt ./orchestrator/requirements.txt
RUN pip install --no-cache-dir -r orchestrator/requirements.txt

COPY orchestrator/ ./orchestrator/

ENV PYTHONPATH=/app

# Default entrypoint is the Workflow API; docker-compose overrides `command`
# for the planner service to run `python -m orchestrator.planner` instead.
CMD ["uvicorn", "orchestrator.workflow_api:app", "--host", "0.0.0.0", "--port", "8080"]
