import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
worker_class = "uvicorn_worker.UvicornWorker"
# Keep 1 worker unless DATABASE_URL points to PostgreSQL: in-process job pools and the in-memory
# rate limiter are per worker. Jobs are claimed atomically in the DB, so extra workers are safe, but
# SQLite + several writers is not recommended.
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
timeout = 120
graceful_timeout = 30
accesslog = "-"
