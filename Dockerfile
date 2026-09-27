FROM python:3.11-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY app.py .

ENV PATH="/opt/venv/bin:$PATH"

EXPOSE 8000

ENTRYPOINT ["gunicorn", "--bind", "0.0.0.0:8000", "app:app"]