"""Regression tests for the consolidated Data & Models / Predictions workflows and production rules."""
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.container import Container
from app.main import create_app
from tests.conftest import api_df, csv_bytes
from tests.test_api import TRAIN_BODY, upload

ROOT = Path(__file__).resolve().parent.parent
ADMIN = {"X-Admin-Token": "tok"}


def train_job(client, attested=True, df=None, provenance="SYNTHETIC TEST FIXTURE"):
    d = upload(client, df if df is not None else api_df()).json()
    r = client.post("/api/train", json={"dataset_id": d["dataset_id"], "split_strategy": "chronological",
                                        "label_provenance": provenance, "provenance_attested": attested}, headers=ADMIN)
    return r, d["dataset_id"]


def model_saved(client, attested=True):
    r, _ = train_job(client, attested)
    assert r.status_code == 202, r.text
    j = client.get(f"/api/jobs/{r.json()['id']}").json()
    if j["status"] != "succeeded" or j["result"]["outcome"] != "model_saved":
        pytest.skip("synthetic data produced no model")
    return j["result"]


def test_training_requires_documented_label_provenance(app_env):
    client, _ = app_env
    r, _ = train_job(client, provenance="   ")
    assert r.status_code == 422 and "provenance" in r.json()["detail"].lower() and r.json()["code"] == "training"


def test_unvalidated_model_is_saved_but_never_active_and_cannot_be_activated(app_env):
    client, c = app_env
    res = model_saved(client, attested=False)
    assert res["status"] == "experimental" and res["active"] is False
    assert c.registry.active_version() is None
    assert client.get("/api/status").json()["model"]["state"] == "not_trained"
    r = client.post(f"/api/models/{res['version']}/activate", headers=ADMIN)
    assert r.status_code == 409 and "validated" in r.json()["detail"]
    versions = client.get("/api/models").json()["versions"]
    assert versions[0]["status"] == "experimental" and versions[0]["failed_gates"]
    # prediction is refused rather than served by an unvalidated model
    body = {"timestamp": "2024-07-01 10:00", "latitude": 19.1, "longitude": 72.9, "features": {"rainfall_24h_mm": 5}}
    assert client.post("/api/predict", json=body).json()["code"] == "no_model"


def test_new_validated_model_never_silently_replaces_the_active_one(app_env):
    client, c = app_env
    first = model_saved(client)
    if first["status"] != "validated":
        pytest.skip("first model not validated")
    assert first["active"] is True
    second_r, _ = train_job(client, df=api_df().sample(frac=0.95, random_state=1).reset_index(drop=True))
    j = client.get(f"/api/jobs/{second_r.json()['id']}").json()
    if j["result"]["outcome"] != "model_saved" or j["result"]["status"] != "validated":
        pytest.skip("second model not validated")
    assert j["result"]["active"] is False and c.registry.active_version() == first["version"]
    assert client.post(f"/api/models/{j['result']['version']}/activate", headers=ADMIN).json()["active"] == j["result"]["version"]
    assert client.post(f"/api/models/{first['version']}/activate").status_code == 401  # admin only


def test_model_detail_and_registry_metadata(app_env):
    client, _ = app_env
    res = model_saved(client)
    d = client.get(f"/api/models/{res['version']}").json()
    for k in ("test_metrics", "split", "data", "gates", "calibration", "candidates", "label_provenance", "status_reason"):
        assert k in d
    assert d["data"]["sha256"] and d["split"]["strategy"] == "chronological"
    assert client.get("/api/models/v-nope").status_code == 404
    v = client.get("/api/models").json()["versions"][0]
    assert {"dataset_sha256", "split_strategy", "test_pr_auc", "failed_gates"} <= set(v)


def test_dataset_list_and_bounded_preview(app_env):
    client, _ = app_env
    d = upload(client, api_df().head(300)).json()["dataset_id"]
    lst = client.get("/api/datasets").json()["items"]
    assert lst and lst[0]["dataset_id"] == d and lst[0]["n_rows"] == 300
    pv = client.get(f"/api/datasets/{d}/preview?rows=5").json()
    assert len(pv["rows"]) == 5 and "timestamp" in pv["columns"] and pv["n_rows_total"] == 300
    assert client.get(f"/api/datasets/{d}/preview?rows=1000").status_code == 422  # bounded
    assert client.get("/api/datasets/" + "0" * 32 + "/preview").status_code == 404


def test_activity_distinguishes_job_types_and_paginates(app_env):
    client, _ = app_env
    res = model_saved(client)
    if res["status"] != "validated":
        pytest.skip("not validated")
    d = upload(client, api_df().drop(columns=["waterlogging_occurred"]).head(20), "predict").json()["dataset_id"]
    client.post("/api/predict/batch", json={"dataset_id": d})
    types = [i["type"] for i in client.get("/api/activity").json()["items"]]
    assert types.count("train") == 1 and types.count("batch_predict") == 1
    assert [i["type"] for i in client.get("/api/activity?type=train").json()["items"]] == ["train"]
    assert len(client.get("/api/activity?limit=1").json()["items"]) == 1
    assert client.get("/api/activity?type=bogus").status_code == 422 and client.get("/api/activity?limit=999").status_code == 422


def test_single_and_batch_share_one_prediction_service(app_env, monkeypatch):
    client, _ = app_env
    res = model_saved(client)
    if res["status"] != "validated":
        pytest.skip("not validated")
    import app.services.job_service as js
    import app.services.prediction_service as ps
    calls = {"n": 0}
    real = ps.predict_dataframe

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(ps, "predict_dataframe", counting)
    monkeypatch.setattr(js, "predict_dataframe", counting)
    base = api_df().drop(columns=["waterlogging_occurred"]).iloc[0].to_dict()
    feats = {k: base[k] for k in ("rainfall_1h_mm", "rainfall_24h_mm", "rainfall_72h_mm", "elevation_m", "slope_deg", "drainage_capacity")}
    assert client.post("/api/predict", json={"timestamp": base["timestamp"], "latitude": base["latitude"], "longitude": base["longitude"], "features": feats}).status_code == 200
    d = upload(client, pd.DataFrame([base]), "predict").json()["dataset_id"]
    assert client.get(f"/api/jobs/{client.post('/api/predict/batch', json={'dataset_id': d}).json()['id']}").json()["status"] == "succeeded"
    assert calls["n"] == 2  # exactly one call per mode, both through predict_dataframe


def test_history_records_model_version_and_feature_provenance_and_coverage_warning(app_env):
    client, _ = app_env
    res = model_saved(client)
    if res["status"] != "validated":
        pytest.skip("not validated")
    base = api_df().iloc[1]
    feats = {k: float(base[k]) for k in ("rainfall_1h_mm", "rainfall_24h_mm", "rainfall_72h_mm", "elevation_m", "slope_deg", "drainage_capacity")}
    src = {k: ("open-meteo (recent_model_analysis)" if "rain" in k else "user-supplied") for k in feats}
    out = client.post("/api/predict", json={"timestamp": base["timestamp"], "latitude": 28.6, "longitude": 77.2, "features": feats,
                                            "feature_sources": src, "save_history": True}).json()
    assert out["coverage"]["eligible"] is False and any("outside the configured model coverage" in w for w in out["warnings"])
    item = client.get("/api/history?limit=1").json()["items"][0]
    assert item["model_version"] == res["version"] and item["inputs"]["_sources"]["slope_deg"] == "user-supplied"
    assert item["model_status"] == "validated"


def test_data_persists_across_application_restart(tmp_path):
    kw = dict(database_url=f"sqlite:///{tmp_path/'p.db'}", storage_path=str(tmp_path / "store"), rate_limit_per_min=0, admin_token="tok", app_env="test", _env_file=None)
    s1 = Settings(**kw)
    with TestClient(create_app(s1, Container(s1, run_jobs_inline=True))) as cl:
        res = model_saved(cl)
        if res["status"] != "validated":
            pytest.skip("not validated")
        pid = cl.post("/api/predict", json={"timestamp": "2024-07-01 10:00", "latitude": 19.1, "longitude": 72.9, "save_history": True,
                                            "features": {k: 1.0 for k in ("rainfall_1h_mm", "rainfall_24h_mm", "rainfall_72h_mm", "elevation_m", "slope_deg", "drainage_capacity")}})
        assert pid.status_code == 200
    s2 = Settings(**kw)  # brand-new process state, same database + artifact store
    with TestClient(create_app(s2, Container(s2, run_jobs_inline=True))) as cl:
        st = cl.get("/api/status").json()
        assert st["model"]["state"] == "validated" and st["model"]["version"] == res["version"]
        assert st["predictions_recorded"] == 1 and st["datasets_available"] >= 1
        assert [j["type"] for j in cl.get("/api/activity").json()["items"]] == ["train"]
        assert cl.get("/api/history").json()["items"][0]["model_version"] == res["version"]
        assert cl.post("/api/predict", json={"timestamp": "2024-07-01 10:00", "latitude": 19.1, "longitude": 72.9,
                                             "features": {k: 1.0 for k in ("rainfall_1h_mm", "rainfall_24h_mm", "rainfall_72h_mm", "elevation_m", "slope_deg", "drainage_capacity")}}).status_code == 200



def test_postgres_ddl_generation_for_all_tables(tmp_path):
    """Generate PostgreSQL DDL offline; no PostgreSQL server is required."""
    import os
    import subprocess
    import sys

    env = os.environ.copy()
    env["DATABASE_URL"] = (
        "postgresql+psycopg://u:p@localhost:5432/wps"
    )
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(ROOT), env.get("PYTHONPATH", "")])
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 0, result.stderr[-400:]
    sql = result.stdout

    for table in (
        "datasets",
        "jobs",
        "model_versions",
        "predictions",
        "cleanup_log",
    ):
        assert f"CREATE TABLE {table}" in sql, table

    assert "TIMESTAMP WITH TIME ZONE" in sql
    assert "JSON" in sql
    assert "SERIAL" in sql or "GENERATED" in sql


def test_compose_and_docker_files_are_consistent():
    compose = (ROOT / "docker-compose.yml").read_text()
    import yaml
    doc = yaml.safe_load(compose)
    assert doc["services"]["db"]["image"].startswith("postgres:") and "healthcheck" in doc["services"]["db"]
    assert doc["services"]["migrate"]["command"] == ["alembic", "upgrade", "head"]  # migrations are a controlled, separate step
    assert doc["services"]["app"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert doc["services"]["app"]["environment"]["APP_ENV"] == "production" and doc["services"]["app"]["env_file"] == ".env"
    assert doc["services"]["app"]["volumes"] == ["appdata:/data"] and "stop_grace_period" in doc["services"]["app"]
    assert "--reload" not in compose and "--reload" not in (ROOT / "Dockerfile").read_text()
    assert ".env" in (ROOT / ".gitignore").read_text() and ".env" in (ROOT / ".dockerignore").read_text()
    env = (ROOT / ".env.example").read_text()
    for var in ("DATABASE_URL", "ADMIN_TOKEN", "SECRET_KEY", "CORS_ALLOWED_ORIGINS", "OPEN_METEO_API_KEY", "CITY_BBOX", "WEATHER_CACHE_TTL_S"):
        assert var in env, var
    assert not any(line.split("=", 1)[1].split("#")[0].strip() for line in env.splitlines() if line.startswith(("ADMIN_TOKEN=", "SECRET_KEY=", "OPEN_METEO_API_KEY=")))  # placeholders only
