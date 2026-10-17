from datetime import timedelta

import pandas as pd
import pytest
from sqlalchemy import select, update

from app.database.models import CleanupLog, Dataset, Job, PredictionRecord, utcnow
from app.services.cleanup_service import run_cleanup
from tests.conftest import api_df, csv_bytes, make_synthetic

TRAIN_BODY = {"split_strategy": "chronological", "label_provenance": "SYNTHETIC TEST FIXTURE", "provenance_attested": True}


def upload(client, df, kind="train", name="d.csv"):
    return client.post(f"/api/datasets?kind={kind}", files={"file": (name, csv_bytes(df), "text/csv")})


def train(client, admin, df=None):
    df = df if df is not None else api_df()
    d = upload(client, df).json()
    r = client.post("/api/train", json={"dataset_id": d["dataset_id"], **TRAIN_BODY}, headers=admin)
    assert r.status_code == 202, r.text
    return client.get(f"/api/jobs/{r.json()['id']}").json()


def test_health_and_ready(app_env):
    client, _ = app_env
    assert client.get("/health").json() == {"status": "ok"}
    r = client.get("/health/ready")
    assert r.status_code == 200 and r.json()["database"] == "ok" and r.json()["storage"] == "ok"


def test_status_untrained_state(app_env):
    client, _ = app_env
    s = client.get("/api/status").json()
    assert s["model"]["state"] == "not_trained" and s["predictions_recorded"] == 0 and s["risk_distribution"] == {}
    assert s["region"]["key"] == "mumbai"


def test_security_headers(app_env):
    r = app_env[0].get("/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-request-id"]


def test_template_has_header_only(app_env):
    t = app_env[0].get("/api/template.csv").text.strip().split("\n")
    assert len(t) == 1 and "waterlogging_occurred" in t[0]


def test_predict_without_model_is_refused(app_env):
    client, _ = app_env
    r = client.post("/api/predict", json={"timestamp": "2024-07-01", "latitude": 19.0, "longitude": 72.8,
                                          "features": {"rainfall_24h_mm": 10}})
    assert r.status_code == 409 and r.json()["code"] == "no_model"
    d = upload(client, make_synthetic().drop(columns=["waterlogging_occurred"]).head(10), "predict").json()
    assert client.post("/api/predict/batch", json={"dataset_id": d["dataset_id"]}).status_code == 409


def test_invalid_csv_gives_report_not_crash(app_env):
    client, _ = app_env
    bad = pd.DataFrame({"foo": [1, 2], "bar": [3, 4]})
    r = upload(client, bad)
    assert r.status_code == 201 and r.json()["status"] == "invalid"
    assert any("Missing required column" in e for e in r.json()["report"]["fatal_errors"])


def test_upload_rules(app_env):
    client, _ = app_env
    assert client.post("/api/datasets?kind=train", files={"file": ("x.txt", b"a,b\n1,2", "text/plain")}).status_code == 415
    assert client.post("/api/datasets?kind=train", files={"file": ("x.csv", b"", "text/csv")}).status_code == 400
    assert client.post("/api/datasets?kind=train", files={"file": ("x.csv", b"a\x00b", "text/csv")}).status_code == 415
    assert client.post("/api/datasets?kind=train", files={"file": ("x.csv", b"x" * (3 * 1024 * 1024), "text/csv")}).status_code == 413
    assert client.post("/api/datasets?kind=evil", files={"file": ("x.csv", b"a,b\n1,2", "text/csv")}).status_code == 422


def test_malicious_filename_is_display_only(app_env):
    client, c = app_env
    r = upload(client, make_synthetic().head(30), name="../../etc/passwd.csv").json()
    with c.session() as s:
        ds = s.get(Dataset, r["dataset_id"])
    assert ds.storage_key == f"uploads/{ds.id}.csv" and ".." not in ds.display_name and "/" not in ds.display_name


def test_rejected_rows_download(app_env):
    client, _ = app_env
    df = make_synthetic().head(50)
    df.loc[0, "latitude"] = 95
    d = upload(client, df).json()
    txt = client.get(f"/api/datasets/{d['dataset_id']}/rejected.csv").text
    assert "out_of_range:latitude" in txt


def test_train_requires_admin_token(app_env):
    client, _ = app_env
    d = upload(client, make_synthetic()).json()
    assert client.post("/api/train", json={"dataset_id": d["dataset_id"]}).status_code == 401
    assert client.post("/api/train", json={"dataset_id": d["dataset_id"]}, headers={"X-Admin-Token": "bad"}).status_code == 401


def test_full_workflow_train_predict_batch_history_map(app_env, admin):
    client, c = app_env
    job = train(client, admin)
    assert job["status"] == "succeeded", job
    assert job["result"]["outcome"] in ("model_saved", "no_eligible_model")
    if job["result"]["outcome"] != "model_saved":
        pytest.skip("synthetic data produced no eligible model")
    st = client.get("/api/status").json()["model"]
    assert st["state"] in ("validated", "experimental") and st["version"] == job["result"]["version"]
    assert client.get("/api/models").json()["active"] == st["version"]
    assert "permutation_importance" in client.get("/api/models/active").json()

    base = api_df().drop(columns=["waterlogging_occurred"])
    rec = base.iloc[0].to_dict()
    body = {"timestamp": rec["timestamp"], "latitude": rec["latitude"], "longitude": rec["longitude"],
            "features": {k: rec[k] for k in ("rainfall_1h_mm", "rainfall_24h_mm", "rainfall_72h_mm", "elevation_m",
                                              "slope_deg", "drainage_capacity")}, "save_history": True}
    r = client.post("/api/predict", json=body)
    assert r.status_code == 200, r.text
    res = r.json()
    assert 0 <= res["result"]["probability"] <= 1 and res["disclaimer"] and res["model"]["status"] == st["state"]
    if st["state"] != "validated":
        assert res["result"]["risk_level"] == "pending_validation" and res["risk_note"]

    bad = client.post("/api/predict", json={**body, "features": {**body["features"], "rainfall_1h_mm": -3}})
    assert bad.status_code == 422

    pd_ = upload(client, base.head(40), "predict").json()
    bj = client.post("/api/predict/batch", json={"dataset_id": pd_["dataset_id"], "save_history": True}).json()
    done = client.get(f"/api/jobs/{bj['id']}").json()
    assert done["status"] == "succeeded" and done["result"]["n_predicted"] == 40 and done["result"]["history_saved"] == 40
    csv = client.get(f"/api/jobs/{bj['id']}/download/predictions")
    assert csv.status_code == 200 and csv.text.splitlines()[0].startswith("row_id")
    assert client.get(f"/api/jobs/{bj['id']}/download/../../etc/passwd").status_code == 404
    assert client.get(f"/api/jobs/{bj['id']}/download/other").status_code == 404

    h = client.get("/api/history?limit=5").json()
    assert h["total"] == 41 and len(h["items"]) == 5
    assert client.get("/api/" + "map/points").status_code == 404  # the retired map endpoint stays gone
    exp = client.get("/api/history/export.csv")
    assert exp.status_code == 200 and exp.text.splitlines()[0].startswith("id,created_at,source")
    assert len(exp.text.strip().splitlines()) == 42
    assert client.delete("/api/history").status_code == 401
    assert client.delete("/api/history", headers=admin).json()["deleted"] == 41


def test_batch_incompatible_columns_fails_job_cleanly(app_env, admin):
    client, _ = app_env
    if train(client, admin)["result"]["outcome"] != "model_saved":
        pytest.skip("no model")
    thin = pd.DataFrame({"timestamp": ["2024-07-01"], "latitude": [19.0], "longitude": [72.8], "rainfall_24h_mm": [5]})
    d = upload(client, thin, "predict").json()
    bj = client.post("/api/predict/batch", json={"dataset_id": d["dataset_id"]}).json()
    j = client.get(f"/api/jobs/{bj['id']}").json()
    assert j["status"] == "failed" and "lacks columns" in j["error"]


def test_training_failures_are_reported(app_env, admin):
    client, _ = app_env
    d = upload(client, make_synthetic(n=80, seed=2).assign(waterlogging_occurred=lambda x: [i % 2 for i in range(80)])).json()
    r = client.post("/api/train", json={"dataset_id": d["dataset_id"], **TRAIN_BODY}, headers=admin)
    j = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert j["status"] == "failed" and "Insufficient data" in j["error"]
    assert client.get("/api/status").json()["model"]["state"] == "not_trained"


def test_concurrent_train_is_rejected(app_env, admin):
    client, c = app_env
    d = upload(client, make_synthetic()).json()
    with c.session() as s:
        s.add(Job(type="train", status="running", heartbeat_at=utcnow()))
    r = client.post("/api/train", json={"dataset_id": d["dataset_id"], **TRAIN_BODY}, headers=admin)
    assert r.status_code == 409 and r.json()["code"] == "job_conflict"


def test_job_claim_is_atomic(app_env):
    from app.services import job_service as js
    _, c = app_env
    with c.session() as s:
        j = Job(type="train"); s.add(j); s.flush(); jid = j.id
    assert js._claim(c, jid) is True and js._claim(c, jid) is False


def test_unknown_resources_404(app_env):
    client, _ = app_env
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/datasets/" + "0" * 32).status_code == 404
    assert client.post("/api/models/nope/activate", headers={"X-Admin-Token": "tok"}).status_code == 404


def test_validation_error_is_clean(app_env):
    r = app_env[0].post("/api/predict", json={"latitude": "x"})
    assert r.status_code == 422 and "Invalid request" in r.json()["detail"]


def test_rate_limit(tmp_path):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.container import Container
    from app.main import create_app
    s = Settings(database_url=f"sqlite:///{tmp_path/'r.db'}", storage_path=str(tmp_path / "s"), rate_limit_per_min=3,
                 app_env="test", _env_file=None)
    with TestClient(create_app(s, Container(s, run_jobs_inline=True))) as cl:
        codes = [cl.post("/api/predict", json={"timestamp": "2024-01-01", "latitude": 19, "longitude": 72.8}).status_code for _ in range(5)]
    assert codes[:3] == [409] * 3 and codes[3:] == [429, 429]


def test_production_refuses_to_start_with_unsafe_config(tmp_path):
    import pytest
    from app.config import ConfigError, Settings
    from app.container import Container
    from app.main import create_app
    base = dict(database_url=f"sqlite:///{tmp_path/'p.db'}", storage_path=str(tmp_path / "s"), app_env="production", _env_file=None)
    with pytest.raises(ConfigError) as e:
        create_app(Settings(**base, admin_token=None), Container(Settings(**base), run_jobs_inline=True))
    msg = str(e.value)
    assert "ADMIN_TOKEN" in msg and "SECRET_KEY" in msg and "PostgreSQL" in msg


def test_production_starts_with_safe_config(tmp_path):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.container import Container
    from app.main import create_app
    s = Settings(database_url=f"sqlite:///{tmp_path/'p.db'}", storage_path=str(tmp_path / "s"), app_env="production",
                 admin_token="x" * 24, secret_key="y" * 24, allow_sqlite_in_production=True, _env_file=None)
    with TestClient(create_app(s, Container(s, run_jobs_inline=True))) as cl:
        assert cl.post("/api/admin/cleanup").status_code == 401
        assert cl.post("/api/admin/cleanup", headers={"X-Admin-Token": "x" * 24}).status_code == 200


def test_cors_only_for_listed_origins(tmp_path):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.container import Container
    from app.main import create_app
    s = Settings(database_url=f"sqlite:///{tmp_path/'c.db'}", storage_path=str(tmp_path / "s"), app_env="test",
                 cors_allowed_origins="https://ok.example", _env_file=None)
    with TestClient(create_app(s, Container(s, run_jobs_inline=True))) as cl:
        good = cl.get("/health", headers={"Origin": "https://ok.example"})
        bad = cl.get("/health", headers={"Origin": "https://evil.example"})
    assert good.headers.get("access-control-allow-origin") == "https://ok.example"
    assert "access-control-allow-origin" not in bad.headers


# ------------------------------------------------------------------ cleanup
def test_cleanup_expires_files_but_protects_active_jobs(app_env):
    client, c = app_env
    a = upload(client, make_synthetic().head(30)).json()["dataset_id"]
    b = upload(client, make_synthetic().head(30)).json()["dataset_id"]
    past = utcnow() - timedelta(hours=1)
    with c.session() as s:
        s.execute(update(Dataset).values(expires_at=past))
        s.add(Job(type="train", status="running", dataset_id=b, heartbeat_at=utcnow()))
    stats = run_cleanup(c)
    assert stats["datasets_expired"] == 1 and stats["files_deleted"] == 1
    assert not c.store.exists(f"uploads/{a}.csv") and c.store.exists(f"uploads/{b}.csv")
    with c.session() as s:
        assert s.get(Dataset, a).status == "expired" and s.get(Dataset, b).status != "expired"
        assert s.scalar(select(CleanupLog.id)) is not None
    assert client.get(f"/api/datasets/{a}").status_code == 404
    with c.session() as s:  # once the job finishes, the next run may clean the file
        s.execute(update(Job).where(Job.dataset_id == b).values(status="succeeded"))
    assert run_cleanup(c)["datasets_expired"] == 1


def test_cleanup_marks_stale_jobs_abandoned_and_prunes_history(app_env):
    _, c = app_env
    old = utcnow() - timedelta(days=200)
    with c.session() as s:
        s.add(Job(type="train", status="running", heartbeat_at=utcnow() - timedelta(hours=5)))
        s.add(Job(type="train", status="running", heartbeat_at=utcnow()))
        s.add(PredictionRecord(source="single", model_version="v", model_status="experimental", latitude=19, longitude=72.8,
                               predicted_class=0, probability=0.1, risk_level="x", created_at=old))
        s.add(PredictionRecord(source="single", model_version="v", model_status="experimental", latitude=19, longitude=72.8,
                               predicted_class=0, probability=0.1, risk_level="x"))
    stats = run_cleanup(c)
    assert stats["jobs_abandoned"] == 1 and stats["history_pruned"] == 1
    with c.session() as s:
        assert sorted(s.scalars(select(Job.status))) == ["abandoned", "running"]
        assert s.query(PredictionRecord).count() == 1


def test_cleanup_failure_is_counted_and_retried(app_env, monkeypatch):
    client, c = app_env
    a = upload(client, make_synthetic().head(30)).json()["dataset_id"]
    with c.session() as s:
        s.execute(update(Dataset).values(expires_at=utcnow() - timedelta(hours=1)))
    from app.storage import StorageError
    monkeypatch.setattr(c.store, "delete", lambda k: (_ for _ in ()).throw(StorageError("boom")))
    assert run_cleanup(c)["errors"] == 1
    monkeypatch.undo()
    assert run_cleanup(c)["datasets_expired"] == 1
