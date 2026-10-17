# Deployment (provider-independent; nothing here was deployed or Docker-built — see docs/VERIFICATION in the About page)
**Env**: see `.env.example`. Required in production: `APP_ENV=production`, `ADMIN_TOKEN`, `DATABASE_URL` (PostgreSQL), `STORAGE_PATH` on a persistent volume.
**Docker**: `docker build -t wps .` → `docker run -p 8000:8000 -v wps-data:/data -e ADMIN_TOKEN=... -e DATABASE_URL=... wps`
(entrypoint runs `alembic upgrade head` then Gunicorn with `uvicorn_worker.UvicornWorker`; health: `/health`).
**Migrations**: `alembic upgrade head` (tested on SQLite only; PostgreSQL untested). Dev/test SQLite auto-creates tables.
**Render**: `render.yaml` provisions a Docker web service + disk + Postgres. A persistent disk needs a paid plan; free Postgres expires. Not deployed/tested.
**Ephemeral filesystems**: without a persistent volume, models and results disappear on redeploy; with SQLite the history does too.
**Backup**: see SUSTAINABILITY.md. **Scheduled cleanup**: built in; also `python -m app.cli cleanup` from cron.
**Behind a proxy**: terminate TLS there; add authentication if the app is public.

## Production configuration checklist
The app **refuses to start** with `APP_ENV=production` unless: `ADMIN_TOKEN` (>=16 chars) and `SECRET_KEY` are set, `DATABASE_URL` is PostgreSQL
(or `ALLOW_SQLITE_IN_PRODUCTION=true` for a throwaway demo), and `CORS_ALLOWED_ORIGINS` has no `*`. Verified with real Gunicorn start/refusal on SQLite.
* Generate secrets: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Set them in the platform's secret store, never in git.
* CORS: leave empty for same-origin use; otherwise list exact origins.
* Weather: no key needed for Open-Meteo's free endpoints. Commercial customers set `OPEN_METEO_API_KEY` **and** the `WEATHER_*_URL` values to `customer-api.open-meteo.com`.
* Coverage: `CITY` / `CITY_CONFIG_PATH` / `CITY_BBOX`.
## docker-compose (PostgreSQL)
`cp .env.example .env`, fill `ADMIN_TOKEN`, `SECRET_KEY`, `POSTGRES_PASSWORD`; `docker compose up -d --build`. Order: db healthy → `migrate` job (`alembic upgrade head`) → app.
Not executed by the author (no Docker available). Persistent volumes: `pgdata` (database) and `appdata` (`/data/storage`: models, results).
## Render
`render.yaml` provisions the Docker service, a disk (paid plan) and Postgres; set `SECRET_KEY` manually (ADMIN_TOKEN is generated). Remaining manual steps: confirm plan/disk, set `CORS_ALLOWED_ORIGINS`, verify `/health/ready`, run one backup/restore drill.
## Graceful shutdown
Gunicorn handles SIGTERM (`graceful_timeout=30`): verified locally. Running training/batch threads are abandoned on shutdown and marked `abandoned` at the next start.
