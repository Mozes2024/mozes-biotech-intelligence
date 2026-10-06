# Persistent hot-lane worker. State lives in the /data volume (DB + backups).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MOZES_DB_PATH=/data/mozes-hot.db \
    MOZES_HTTP_CACHE=/data/http-cache \
    MOZES_BACKUP_DIR=/data/backups \
    MOZES_SEC_HOT=1

RUN pip install --no-cache-dir tzdata && useradd --create-home --uid 10001 mozes
WORKDIR /app
COPY pyproject.toml README.md ./
COPY mozes ./mozes
RUN pip install --no-cache-dir . && mkdir -p /data && chown mozes:mozes /data

USER mozes
VOLUME ["/data"]
CMD ["python", "-m", "mozes.hot_monitor", "--interval", "60"]
