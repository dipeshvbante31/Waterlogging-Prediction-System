# Waterlogging Prediction System

Configurable urban waterlogging **risk modelling platform** (FastAPI + scikit-learn). Initial region: Mumbai Metropolitan Region; other cities via configuration and compatible CSVs.

> **Honest status:** no verified labelled waterlogging dataset and no validated model are bundled, so **no street-level prediction is claimed**; predictions stay unavailable until you supply labelled data and a model passes every validation gate. Weather values are Open-Meteo model analysis/forecast, never proof of waterlogging. What was and was not verified: About page / `app/verification.json`.

## Pages
Dashboard · Data Explorer (inspect uploaded datasets) · Upload CSV (drag-and-drop, validation) · Predictions (single | batch) · Live Weather · Model Training (train, registry, activation) · Explainability · History (+ per-prediction details). There are no maps: locations are entered as a place name or coordinates.

## Local setup (Windows 11 / any OS)
```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env
python -m uvicorn wsgi:app --reload      # http://127.0.0.1:8000  (development only)
python -m pytest                          # full test suite (no network needed)
```
Optional browser-style UI test: see `tests/ui/ui_e2e.js`. Optional live weather check (needs internet): `RUN_LIVE_WEATHER=1 python -m pytest tests/integration -m live`.

## Live Weather
Open-Meteo's free endpoints need **no API key**. Every click on *Get live weather* sends a fresh server-side request (`/api/weather?fresh=true`). Provider failures show an error with Retry; any cached fallback is labelled stale with its timestamp. `OPEN_METEO_API_KEY` is only for Open-Meteo's paid customer API and is only ever sent to `customer-api.open-meteo.com`.

## Prediction inputs
`GET /api/contract` lists what the active model needs (units, ranges, where each value comes from). Rainfall and elevation come from the provider, `slope_deg` is derived from DEM samples (approximate, overridable), `drainage_capacity` has no public source: enter it, or train without it (Model Training → “Leave out features”). Nothing is ever defaulted to 0.

## Configuration & production
Environment-based (`.env.example`). Production refuses to start without `ADMIN_TOKEN`, `SECRET_KEY`, PostgreSQL `DATABASE_URL`. `docker compose up -d --build` (PostgreSQL + migration job + Gunicorn/Uvicorn) or Render via `render.yaml`; migrations: `alembic upgrade head`. See `docs/DEPLOYMENT.md`, `docs/SUSTAINABILITY.md`. Not Docker-built or deployed by the author.

## Docs
`docs/DATA_SOURCES.md` · `DATA_DICTIONARY.md` · `ARCHITECTURE.md` · `MODEL_EVALUATION.md` · `LIMITATIONS.md` · `DECISIONS.md` · `CHANGELOG.md` · `VIVA_GUIDE.md`

## Licences & attribution
Plotly.js (MIT) vendored in `app/static/vendor`. Weather: Open-Meteo (check current terms/attribution). Logo and icons are original (`app/static/img`). © 2026 Dipesh Vishnu Bante. All rights reserved.
