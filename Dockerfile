# Persistent hot-lane worker. State lives in the /data volume (DB + backups).
# Docker Official Images publisher on ECR Public; pinned Python 3.11-slim index.
FROM public.ecr.aws/docker/library/python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce

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
