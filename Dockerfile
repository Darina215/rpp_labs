# syntax=docker/dockerfile:1.4

# --- Стадия 1: сборка зависимостей ---------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# --- Стадия 2: рантайм ---------------------------------------------------
FROM python:3.11-slim

WORKDIR /app

# Непривилегированный пользователь (требование задачи 4.2)
RUN useradd --create-home --uid 1000 --shell /bin/bash app

# Переменные окружения: порт и уровень логирования (требование 4.2, п. 1)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000 \
    LOG_LEVEL=info

COPY --from=builder /opt/venv /opt/venv

# Копируем все файлы проекта (а не только app.py)
COPY --chown=app:app . .

USER app

EXPOSE 8000

# Проверка готовности по маршруту GET /health (требование 4.2, п. 3)
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health')" || exit 1

# gunicorn с одним воркером; порт и уровень логирования из окружения
ENTRYPOINT ["sh", "-c", "exec gunicorn --workers 1 --bind 0.0.0.0:${PORT} --log-level ${LOG_LEVEL} --access-logfile - --error-logfile - wsgi:app"]