# Architecture

```
Browser (Jinja2 pages + vanilla JS, Plotly; vendored, CSP script-src 'self')
   │ JSON
FastAPI app (app/main.py)  ── routers: pages, /api
   │            │
   │            └─ services: data_validation → feature_engineering → training_service → model_registry
   │                         prediction_service, explainability_service, weather_service, dataset/job/cleanup services
   ├─ SQLAlchemy (SQLite dev | PostgreSQL via DATABASE_URL; Alembic migrations): datasets, jobs, model_versions, predictions, cleanup_log
   └─ ArtifactStore (local dir; interface allows S3-compatible later): uploads/, results/, models/<version>/{model.joblib,metadata.json}, models/ACTIVE.json
```
* **Jobs**: training (1 worker) and batch prediction (2 workers) run in thread pools; status is persisted; claiming is an
  atomic `UPDATE … WHERE status='queued'`; one training job at a time (409 otherwise); stale jobs are marked `abandoned`.
* **Model lifecycle**: `not_trained → experimental → validated`. A validated model is not replaced by an experimental one.
  Artifacts carry a SHA-256 checked on load; models are never loaded from user uploads.
* **Storage lifecycle**: uploads/results expire (default 24 h); cleanup never deletes files of queued/running jobs and
  retries failures on the next run; history pruned after N days; runs at startup, periodically, and via `python -m app.cli cleanup`.
* **Pages**: Dashboard, Data Explorer (inspect stored uploads), Upload CSV (validate + store), Predictions (single | batch), Live Weather, Model Training (train + registry), Explainability, History (+ prediction details). There are no map views anywhere: locations are plain text/coordinates.
* **One prediction path**: single, live-feature and batch predictions all go through `prediction_service.predict_dataframe`; `feature_service` resolves inputs with per-feature provenance.
* **Model activation**: only validated models can be active; a new validated model never replaces the active one without explicit admin activation.
* **Weather isolation**: weather failures return 503 on weather routes only; training/prediction on files is unaffected.
