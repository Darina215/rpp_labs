import os

port = os.environ.get("PORT", "8000")
log_level = os.environ.get("LOG_LEVEL", "info")

os.execvp(
    "gunicorn",
    [
        "gunicorn",
        "--workers", "1",
        "--bind", f"0.0.0.0:{port}",
        "--log-level", log_level,
        "--access-logfile", "-",
        "--error-logfile", "-",
        "wsgi:app",
    ],
)
