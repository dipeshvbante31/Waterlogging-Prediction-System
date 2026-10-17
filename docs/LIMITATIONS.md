# Limitations
1. No verified labelled waterlogging data is bundled; no trained production model exists. Predictions are unavailable until you supply data.
2. "validated" depends on a user attestation about label quality and on a single held-out split; it is not proof of street-level accuracy.
3. Live weather is model analysis/reanalysis/forecast, not gauge data; it may differ from the training rainfall source. Slope/drainage cannot come from the weather provider and must be user-supplied.
4. Models are valid only inside the training time range and locations; out-of-region points are flagged.
5. Open-Meteo endpoints/terms were not verified from the build environment; the live weather call was never executed there. An opt-in live test (`RUN_LIVE_WEATHER=1`) exists for a networked machine.
6. UI behaviour was tested in jsdom against a running server (Plotly stubbed), not in a real browser: visual layout and contrast are unverified. The dashboard screenshot used as a design reference was not available to the author, so the design follows the written specification only.
7. SQLite is for local/dev; PostgreSQL migration path is written but not tested against Postgres. Docker image and Render blueprint were not built/deployed.
8. Rate limiting and job pools are per process; run one worker unless using PostgreSQL and accepting per-worker limits.
9. No user accounts: privileged endpoints (train, activate, delete history, cleanup) require a shared `ADMIN_TOKEN` header, enforced server-side and mandatory in production; prediction/upload endpoints are open (rate-limited per process, per client IP — behind a proxy configure forwarded headers). Put the app behind HTTPS and, if public, an auth proxy.
10. Datasets with fewer than ~hundreds of events cannot support reliable temporal/spatial hold-outs; the trainer refuses rather than guess.
11. Uploads are bounded (20 MB / 200k rows) and parsed in memory; batch jobs are not streamed. Very large files need chunked processing and a real worker queue.
12. The model-coverage box is an approximate rectangle (Mumbai Metropolitan Region), not an administrative boundary; override with `CITY_BBOX`.
13. "Validated" depends on a user attestation of label quality; the software cannot verify it.
