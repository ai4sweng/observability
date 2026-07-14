FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kio_simulator.py .

# Give the code-analysis KIO something real to scan.
ENV REPO_SCAN_PATH=/app

CMD ["python", "-u", "kio_simulator.py"]
