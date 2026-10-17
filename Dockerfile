FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
RUN useradd --create-home --uid 10001 appuser
COPY requirements.txt .
RUN pip install -r requirements.txt "psycopg[binary]>=3.1"
COPY alembic.ini gunicorn.conf.py wsgi.py ./
COPY migrations ./migrations
COPY app ./app
RUN mkdir -p /data/storage && chown -R appuser:appuser /data /srv
USER appuser
ENV APP_ENV=production LOG_JSON=true STORAGE_PATH=/data/storage PORT=8000
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import os,urllib.request as u; u.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)"
# Run migrations (idempotent) then start the ASGI server.
CMD ["sh", "-c", "alembic upgrade head && exec gunicorn -c gunicorn.conf.py wsgi:app"]
