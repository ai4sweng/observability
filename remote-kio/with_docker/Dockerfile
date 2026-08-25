# A container that runs the same demo KIO as ../with_script, but packaged.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kio_otel.py main.py ./

# -u = unbuffered stdout, so `docker compose logs -f` shows output live.
CMD ["python", "-u", "main.py"]
