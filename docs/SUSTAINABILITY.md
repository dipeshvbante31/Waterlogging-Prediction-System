# Sustainability review
| Area | Status / guidance |
|---|---|
| Reliability | Strict production config validation at startup; health endpoints (`/health`, `/health/ready`); weather failures isolated; bounded retries, timeouts, cache + stale fallback; jobs persisted, stale jobs recovered; model artifacts hash-verified. Single process = single point of failure. |
| Data provenance | Model metadata stores dataset SHA-256, split ranges, library versions, label-provenance text, attestation. Source verification of public data is **still outstanding** (see DATA_SOURCES.md). |
| Operating cost | Zero paid APIs. Needs one small container + Postgres + a small persistent disk (~1 GB). Open-Meteo free tier is non-commercial; commercial use needs a plan. |
| Scaling limits | Training is CPU-bound in-process; one training job at a time; 200k-row / 20 MB upload caps; map capped at 2000 points; rate limiter per process. Beyond this, add a worker process/queue. |
| Privacy | No accounts/cookies. Uploads expire (24 h), results expire, history opt-in and pruned (90 d). Stored history contains coordinates and inputs — treat as potentially sensitive if tied to addresses. Logs avoid payloads. |
| Maintenance | Pin/upgrade dependencies regularly; re-run tests; retrain when data changes (models are versioned, old versions kept); sklearn upgrades can break old joblib artifacts — metadata records versions; retrain on upgrade. |
| Disaster recovery | Back up (1) PostgreSQL (`pg_dump` daily), (2) the `STORAGE_PATH` volume (`models/`), (3) your original labelled CSVs (uploads are deleted by design — keep the source of truth elsewhere). Restore: restore DB + volume, run `alembic upgrade head`, start app; verify with `/health/ready` and `models/ACTIVE.json`. Untested restore drill — perform one. |
