"""Backend regression tests for the redesign: dashboard stats, explorer, contract, inputs, history, storage/config, weather current block."""
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd
import pytest
from sqlalchemy import select

from app.config import Settings
from app.container import Container
from app.database.models import PredictionRecord
from app.services.data_validation import humanize_reason
from app.services.schema import DEFAULT_SCHEMA
from app.services.weather_service import WeatherService, WeatherUnavailable, normalize_current
from app.storage import LocalArtifactStore, StorageError
from tests.conftest import api_df, csv_bytes
from tests.test_api import upload
from tests.test_weather import NOW, hourly, hourly_now, svc

ROOT = Path(__file__).resolve().parent.parent
ADMIN = {"X-Admin-Token": "tok"}
FEATS = ("rainfall_1h_mm", "rainfall_24h_mm", "rainfall_72h_mm", "elevation_m", "slope_deg", "drainage_capacity")
import os

def trained(client, exclude=(), attested=True):
    d = upload(client, api_df()).json()["dataset_id"]
    r = client.post("/api/train", json={"dataset_id": d, "label_provenance": "SYNTHETIC TEST FIXTURE", "provenance_attested": attested, "exclude_features": list(exclude)}, headers=ADMIN)
    assert r.status_code == 202, r.text
    j = client.get(f"/api/jobs/{r.json()['id']}").json()
    if j["status"] != "succeeded" or j["result"]["outcome"] != "model_saved" or j["result"]["status"] != "validated":
        pytest.skip("synthetic data did not yield a validated model")
    return j["result"]["version"]


def predict_body(i=0, **over):
    row = api_df().iloc[i]
    b = {"timestamp": row["timestamp"], "latitude": float(row["latitude"]), "longitude": float(row["longitude"]),
         "features": {k: float(row[k]) for k in FEATS}, "save_history": True}
    b.update(over)
    return b


# ------------------------------------------------------------------ dashboard
def test_dashboard_empty_state_is_honest(app_env):
    d = app_env[0].get("/api/dashboard").json()
    assert d["total_records"] == 0 and d["high_risk"] == 0 and d["average_probability"] is None and d["overall_level"] is None
    assert d["latest"] is None and d["recent"] == [] and d["distribution"] == {} and d["insights"] == [] and d["active_model"] is None
    assert set(d["definitions"]) == {"total_records", "high_risk", "average_probability", "overall_level"}


def test_dashboard_statistics_have_defined_sources_and_match_stored_records(app_env):
    client, c = app_env
    trained(client)
    for i in range(6):
        assert client.post("/api/predict", json=predict_body(i, location_name=f"Place {i}")).status_code == 200
    d = client.get("/api/dashboard").json()
    with c.session() as s:
        rows = s.scalars(select(PredictionRecord)).all()
    assert d["total_records"] == len(rows) == 6                                  # counts saved predictions only (not dataset or training rows)
    assert d["datasets_available"] >= 1 and d["total_records"] != d["datasets_available"]
    cats = [r.risk_level for r in rows if r.risk_level in ("Low", "Medium", "High")]
    assert d["high_risk"] == sum(1 for x in cats if x == "High") and d["high_risk_denominator"] == len(cats)
    assert d["average_probability"] == pytest.approx(sum(r.probability for r in rows) / 6) and d["average_probability_n"] == 6
    assert sum(d["distribution"].values()) == 6 and d["distribution_denominator"] == 6
    assert d["latest"]["location_name"] == "Place 5" and len(d["recent"]) == 6 and d["recent"][0]["id"] > d["recent"][-1]["id"]
    thr = c.registry.get_metadata(c.registry.active_version()).get("risk_thresholds")
    if thr:
        assert d["overall_level"] in ("Low", "Medium", "High")
    else:
        assert d["overall_level"] is None                                         # no thresholds -> no invented category
    assert d["active_model"]["status"] == "validated"


def test_dashboard_risk_category_pending_is_not_counted_as_high(app_env):
    client, c = app_env
    from app.database.models import utcnow
    with c.session() as s:
        s.add(PredictionRecord(source="single", model_version="v", model_status="validated", latitude=19, longitude=72.9, predicted_class=1, probability=0.9, risk_level="pending_validation"))
    d = client.get("/api/dashboard").json()
    assert d["total_records"] == 1 and d["high_risk"] == 0 and d["high_risk_denominator"] == 0 and d["distribution"] == {"pending_validation": 1}
    assert any("no risk category" in i for i in d["insights"])


# ------------------------------------------------------------------ data explorer vs upload
def test_explorer_reports_structure_quality_statistics_and_preview(app_env):
    client, _ = app_env
    df = api_df().head(400).copy()
    df = df.astype({"rainfall_1h_mm": object})
    df.loc[0, "latitude"] = 95                      # invalid
    df.loc[1, "rainfall_1h_mm"] = "abc"             # non-numeric
    df["extra_note"] = "x"
    did = upload(client, df).json()["dataset_id"]
    e = client.get(f"/api/datasets/{did}/explore?limit=10").json()
    assert e["n_rows"] == 400 and e["n_columns"] == 11 and e["kind"] == "train"
    cols = {c["name"]: c for c in e["columns"]}
    assert cols["extra_note"]["recognised_as"] is None and cols["latitude"]["invalid_rows"] == 1 and cols["rainfall_1h_mm"]["invalid_rows"] == 1
    assert cols["rainfall_24h_mm"]["type"] == "number" and cols["timestamp"]["type"] == "text"
    assert all(r["present"] for r in e["required_columns"]) and {r["name"] for r in e["required_columns"]} >= {"timestamp", "latitude", "longitude", "waterlogging_occurred"}
    st = {s["feature"]: s for s in e["statistics"]}
    assert st["rainfall_24h_mm"]["count"] <= 398 and st["rainfall_24h_mm"]["min"] >= 0 and st["rainfall_24h_mm"]["unit"] == "mm"
    assert len(e["preview"]["rows"]) == 10 and e["validation"]["n_rows_rejected"] == 2
    p2 = client.get(f"/api/datasets/{did}/explore?offset=390&limit=25").json()["preview"]
    assert len(p2["rows"]) == 10 and p2["offset"] == 390
    assert client.get(f"/api/datasets/{did}/explore?limit=1000").status_code == 422 and client.get("/api/datasets/" + "0" * 32 + "/explore").status_code == 404


def test_explorer_flags_missing_required_columns_without_guessing(app_env):
    client, _ = app_env
    did = upload(client, pd.DataFrame({"when": ["2024-07-01"], "rain": [5]})).json()["dataset_id"]
    e = client.get(f"/api/datasets/{did}/explore").json()
    assert [r["name"] for r in e["required_columns"] if not r["present"]] == ["timestamp", "latitude", "longitude", "waterlogging_occurred"]
    assert all(c["recognised_as"] is None for c in e["columns"]) and e["status"] == "invalid" and e["statistics"] == []


def test_dataset_list_filters(app_env):
    client, _ = app_env
    upload(client, api_df().head(300))
    upload(client, api_df().drop(columns=["waterlogging_occurred"]).head(50), "predict")
    upload(client, pd.DataFrame({"a": [1]}))
    kinds = lambda q: sorted({i["kind"] for i in client.get("/api/datasets" + q).json()["items"]})
    assert kinds("?kind=predict") == ["predict"] and len(client.get("/api/datasets?status=invalid").json()["items"]) == 1
    assert len(client.get("/api/datasets?kind=train&status=validated").json()["items"]) <= 1
    assert client.get("/api/datasets?kind=bogus").status_code == 422


# ------------------------------------------------------------------ the reported input problem
def test_contract_is_the_single_source_for_single_and_batch(app_env):
    client, c = app_env
    before = client.get("/api/contract").json()
    assert before["for_active_model"] is False and "drainage_capacity" in [f["name"] for f in before["features"]]
    trained(client)
    k = client.get("/api/contract").json()
    names = [f["name"] for f in k["features"]]
    assert names == c.registry.get_metadata(c.registry.active_version())["input_columns"] and k["for_active_model"] is True
    src = {f["name"]: f["source"] for f in k["features"]}
    assert src["rainfall_24h_mm"] == "provider" and src["slope_deg"] == "derived" and src["drainage_capacity"] == "user"
    assert k["csv_columns"][:3] == ["timestamp", "latitude", "longitude"] and set(k["csv_columns"][3:]) == set(names)
    assert all(f["unit"] and f["description"] for f in k["features"]) and {f["name"]: f["max"] for f in k["features"]}["slope_deg"] == 90
    # batch refuses a file that does not satisfy exactly this contract, naming the missing columns
    thin = api_df().drop(columns=["waterlogging_occurred", "slope_deg", "drainage_capacity"]).head(10)
    d = upload(client, thin, "predict").json()["dataset_id"]
    job = client.get(f"/api/jobs/{client.post('/api/predict/batch', json={'dataset_id': d}).json()['id']}").json()
    assert job["status"] == "failed" and "slope_deg" in job["error"] and "drainage_capacity" in job["error"]


def test_missing_feature_never_defaults_to_zero_and_message_is_actionable(app_env):
    client, _ = app_env
    trained(client)
    body = predict_body(); body["features"].pop("drainage_capacity"); body["features"].pop("slope_deg")
    r = client.post("/api/predict", json=body)                       # keys absent: refused, naming exactly what is missing
    assert r.status_code == 422 and "slope_deg" in r.json()["detail"] and "drainage_capacity" in r.json()["detail"] and "lacks columns" in r.json()["detail"]
    body["features"]["slope_deg"] = None; body["features"]["drainage_capacity"] = None   # explicit nulls: allowed, imputed and REPORTED (never zero-filled)
    ok = client.post("/api/predict", json=body)
    assert ok.status_code == 200 and set(ok.json()["result"]["imputed_inputs"].split(",")) == {"slope_deg", "drainage_capacity"}


def test_out_of_range_input_is_rejected_with_plain_language(app_env):
    client, _ = app_env
    trained(client)
    body = predict_body(); body["features"]["slope_deg"] = 120
    r = client.post("/api/predict", json=body)
    assert r.status_code == 422 and "slope_deg is outside the allowed range" in r.json()["detail"] and "0–90" in r.json()["detail"] and "degrees" in r.json()["detail"]
    assert "out_of_range:" not in r.json()["detail"]
    body = predict_body(); body["features"]["rainfall_24h_mm"] = -5
    assert "rainfall_24h_mm is outside the allowed range" in client.post("/api/predict", json=body).json()["detail"]
    body = predict_body(timestamp="01/07/2024")
    assert "ISO 8601" in client.post("/api/predict", json=body).json()["detail"]


def test_humanize_reason_covers_codes():
    for code, frag in (("missing:latitude", "latitude is required"), ("non_numeric:slope_deg", "must be a number"), ("out_of_range:elevation_m", "-100–9000"),
                       ("inconsistent_rainfall_windows", "1 h ≤ 24 h"), ("invalid_timestamp", "ISO 8601"), ("duplicate", "duplicate"), ("unknown_code", "unknown_code")):
        assert frag in humanize_reason(code, DEFAULT_SCHEMA)


def test_training_can_exclude_features_no_source_can_supply(app_env):
    client, c = app_env
    v = trained(client, exclude=["drainage_capacity"])
    md = c.registry.get_metadata(v)
    assert "drainage_capacity" not in md["input_columns"] and "drainage_capacity" not in md["feature_columns"] and "drainage_capacity" in md["dropped_low_coverage"]
    assert "slope_deg" in md["input_columns"]
    k = client.get("/api/contract").json()
    assert "drainage_capacity" not in [f["name"] for f in k["features"]] and "drainage_capacity" not in k["csv_columns"]
    body = predict_body(); body["features"].pop("drainage_capacity")
    r = client.post("/api/predict", json=body)
    assert r.status_code == 200 and not r.json()["result"]["imputed_inputs"]       # nothing missing for this model
    assert client.post("/api/train", json={"dataset_id": "0" * 32, "label_provenance": "x", "exclude_features": ["nonsense"]}, headers=ADMIN).status_code == 422


def test_dem_derived_slope_values():
    base = {"lat": 19.0, "lon": 72.8}

    def provider(slope_m_per_m):
        def h(r):
            lats = [float(x) for x in dict(r.url.params)["latitude"].split(",")]
            return httpx.Response(200, json={"elevation": [round(10 + slope_m_per_m * (la - 19.0) * 111_320, 3) for la in lats]})
        return svc(h)
    flat = provider(0.0).slope(**base)
    assert flat["value_deg"] == 0.0 and len(flat["elevations_m"]) == 5
    import math
    steep = provider(0.1).slope(**base)   # 10 % grade north-south
    assert steep["value_deg"] == pytest.approx(math.degrees(math.atan(0.1)), abs=0.05)
    for bad in ({"elevation": [1, 2]}, {"elevation": [1, 2, 3, 4, "x"]}, {}):
        with pytest.raises(WeatherUnavailable):
            svc(lambda r, b=bad: httpx.Response(200, json=b)).slope(**base)


# ------------------------------------------------------------------ history / details / location name
def test_history_filters_detail_and_location_names(app_env):
    client, _ = app_env
    trained(client)
    client.post("/api/predict", json=predict_body(0, location_name="Andheri East, Maharashtra"))
    client.post("/api/predict", json=predict_body(1, location_name="Colaba"))
    client.post("/api/predict", json=predict_body(2))
    h = client.get("/api/history").json()
    assert h["total"] == 3 and {i["location_name"] for i in h["items"]} == {"Andheri East, Maharashtra", "Colaba", None}
    assert [i["location_name"] for i in client.get("/api/history?q=andheri").json()["items"]] == ["Andheri East, Maharashtra"]
    assert client.get("/api/history?q=zzz").json()["total"] == 0 and client.get("/api/history?source=single").json()["total"] == 3
    assert client.get("/api/history?source=weather").json()["total"] == 0 and client.get("/api/history?source=bogus").status_code == 422
    one = client.get(f"/api/history/{h['items'][0]['id']}").json()
    assert one["record"]["inputs"]["_sources"]["rainfall_24h_mm"] == "user-supplied" and one["disclaimer"]
    assert client.get("/api/history/999999").status_code == 404
    csv = client.get("/api/history/export.csv").text.splitlines()
    assert csv[0].startswith("id,created_at,source,location_name") and len(csv) == 4
    assert client.post("/api/predict", json=predict_body(location_name="x" * 121)).status_code == 422


def test_history_export_neutralises_formula_in_location_name(app_env):
    client, _ = app_env
    trained(client)
    client.post("/api/predict", json=predict_body(0, location_name="=HYPERLINK(\"http://evil\")"))
    assert "'=HYPERLINK" in client.get("/api/history/export.csv").text


def test_prediction_response_links_to_saved_record(app_env):
    client, _ = app_env
    trained(client)
    r = client.post("/api/predict", json=predict_body(0)).json()
    assert r["history_id"] and client.get(f"/api/history/{r['history_id']}").status_code == 200
    r2 = client.post("/api/predict", json=predict_body(0, save_history=False)).json()
    assert r2["history_id"] is None


# ------------------------------------------------------------------ weather: current block, fresh requests, single flight
def full_current(**over):
    cur = {"time": "2026-10-14T10:00", "temperature_2m": 30.1, "apparent_temperature": 34.0, "relative_humidity_2m": 80, "weather_code": 63, "is_day": 1,
           "wind_speed_10m": 10.0, "wind_direction_10m": 90, "precipitation": 1.2, "rain": 1.2}
    cur.update(over)
    units = {"temperature_2m": "°C", "apparent_temperature": "°C", "relative_humidity_2m": "%", "wind_speed_10m": "km/h", "wind_direction_10m": "°", "precipitation": "mm", "rain": "mm"}
    return cur, units


def test_current_block_is_normalised_with_units_condition_and_times():
    cur, units = full_current()
    n = normalize_current(cur, units)
    assert n["temperature_c"] == 30.1 and n["apparent_temperature_c"] == 34.0 and n["relative_humidity_pct"] == 80 and n["wind_speed_kmh"] == 10.0
    assert n["wind_direction_label"] == "E" and n["precipitation_mm"] == 1.2 and n["condition"] == {"code": 63, "label": "Rain", "icon": "rain", "status": "available"}
    assert n["observation_time_utc"] == "2026-10-14T10:00:00+00:00" and n["kind"] == "current_model_analysis" and n["is_day"] is True
    assert all(f["status"] == "available" for f in n["fields"].values())


def test_missing_invalid_or_unit_mismatched_current_fields_are_unavailable_not_zero():
    cur, units = full_current(relative_humidity_2m=None, wind_speed_10m="n/a", precipitation=-3, temperature_2m=999)
    cur.pop("rain"); units["apparent_temperature"] = "°F"
    n = normalize_current(cur, units)
    f = n["fields"]
    assert f["relative_humidity_pct"]["value"] is None and "not reported" in f["relative_humidity_pct"]["reason"]
    assert f["wind_speed_kmh"]["value"] is None and f["precipitation_mm"]["value"] is None and f["temperature_c"]["value"] is None and f["rain_mm"]["value"] is None
    assert "unexpected unit" in f["apparent_temperature_c"]["reason"] and n["precipitation_mm"] is None
    empty = normalize_current({}, {})
    assert empty["status"] == "unavailable" and empty["condition"]["label"] == "Not reported" and empty["observation_time_utc"] is None and empty["wind_direction_label"] is None
    unk = normalize_current(full_current(weather_code=1234)[0], full_current()[1])
    assert unk["condition"]["icon"] == "unknown" and unk["condition"]["status"] == "unavailable"


def test_wind_direction_compass_points():
    for deg, lab in ((0, "N"), (45, "NE"), (180, "S"), (270, "W"), (359, "N"), (225, "SW")):
        assert normalize_current(full_current(wind_direction_10m=deg)[0], full_current()[1])["wind_direction_label"] == lab


def test_fresh_requests_bypass_cache_but_default_calls_use_it():
    calls = {"n": 0}

    def h(r):
        calls["n"] += 1
        return httpx.Response(200, json=hourly())
    s = svc(h, ttl=600)
    s.forecast(19, 72.8, NOW); s.forecast(19, 72.8, NOW)
    assert calls["n"] == 1                                    # cached
    s.forecast(19, 72.8, NOW, fresh=True); s.forecast(19, 72.8, NOW, fresh=True)
    assert calls["n"] == 3 and s.requests_made == 3           # every user-triggered refresh is a new provider request


def test_weather_endpoint_makes_a_new_request_on_every_call(app_env):
    client, c = app_env
    n = {"n": 0}

    def h(r):
        n["n"] += 1
        return httpx.Response(200, json={"elevation": [11.0]}) if "elevation" in str(r.url) else httpx.Response(200, json=hourly_now())
    c.settings.weather_cache_ttl_s = 600
    c.weather = WeatherService(c.settings, httpx.Client(transport=httpx.MockTransport(h)))
    client.get("/api/weather?lat=19.07&lon=72.87"); a = n["n"]
    client.get("/api/weather?lat=19.07&lon=72.87"); b = n["n"]
    assert a == 2 and b == 4                                    # forecast + elevation each time
    client.get("/api/weather?lat=19.07&lon=72.87&fresh=false"); assert n["n"] == 4   # explicit opt-in to the cache
    r = client.get("/api/weather?lat=19.07&lon=72.87&name=Andheri").json()
    assert r["location"] == {"name": "Andheri", "latitude": 19.07, "longitude": 72.87} and r["weather"]["current"]["fields"]["temperature_c"]["status"] in ("available", "unavailable")


def test_identical_concurrent_requests_share_one_provider_call():
    gate, calls = threading.Event(), {"n": 0}

    def h(r):
        calls["n"] += 1
        gate.wait(2)
        return httpx.Response(200, json=hourly())
    s = svc(h, ttl=0)
    out, errs = [], []

    def worker():
        try:
            out.append(s.forecast(19, 72.8, NOW, fresh=True))
        except Exception as e:  # pragma: no cover
            errs.append(e)
    ts = [threading.Thread(target=worker) for _ in range(5)]
    [t.start() for t in ts]; time.sleep(0.3); gate.set(); [t.join(5) for t in ts]
    assert not errs and len(out) == 5 and calls["n"] == 1


def test_provider_errors_are_shared_and_not_cached_as_success(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def h(r):
        calls["n"] += 1
        return httpx.Response(500)
    s = svc(h, retries=0, ttl=600)
    for _ in range(2):
        with pytest.raises(WeatherUnavailable):
            s.forecast(19, 72.8, NOW, fresh=True)
    assert calls["n"] == 2


def test_weather_failure_does_not_break_other_endpoints(app_env):
    client, c = app_env
    c.settings.weather_max_retries = 0
    c.weather = WeatherService(c.settings, httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert client.get("/api/weather?lat=19&lon=72.8").status_code == 503
    for url in ("/api/dashboard", "/api/status", "/api/history", "/api/datasets", "/api/contract", "/health/ready"):
        assert client.get(url).status_code == 200, url
    for page in ("/", "/predictions", "/weather", "/history"):
        assert client.get(page).status_code == 200


def test_api_key_never_appears_in_responses_or_logs(app_env, caplog):
    client, c = app_env
    c.settings.open_meteo_api_key = "SUPER-SECRET-KEY-123"
    c.settings.weather_max_retries = 0
    c.weather = WeatherService(c.settings, httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="invalid key SUPER-SECRET-KEY-123"))))
    with caplog.at_level("DEBUG"):
        r = client.get("/api/weather?lat=19&lon=72.8")
    assert r.status_code == 503 and "SUPER-SECRET-KEY-123" not in r.text and "SUPER-SECRET-KEY-123" not in caplog.text
    assert "credentials" in r.json()["detail"]
    for p in ("/", "/weather", "/about"):
        assert "SUPER-SECRET" not in client.get(p).text


# ------------------------------------------------------------------ storage, config, Windows compatibility
def test_storage_rejects_empty_or_whitespace_keys_and_traversal(tmp_path):
    st = LocalArtifactStore(tmp_path)
    for bad in ("", " ", "   ", "\t", "a b", "../x", "..\\x", "a/../../b", "/abs", "C:\\temp\\x", "a\x00b", "a/./../b"):
        with pytest.raises(StorageError):
            st.put_bytes(bad, b"x")
        with pytest.raises(StorageError):
            st.get_bytes(bad)
    st.put_bytes("models/v1/metadata.json", b"{}")
    assert st.exists("models/v1/metadata.json") and not (tmp_path.parent / "x").exists()


def test_sqlite_parent_directory_is_created_automatically(tmp_path):
    from app.database.db import make_engine
    target = tmp_path / "does" / "not" / "exist" / "app.db"
    eng = make_engine(f"sqlite:///{target.as_posix()}")
    with eng.connect() as conn:
        conn.exec_driver_sql("select 1")
    assert target.parent.is_dir()
    s = Settings(database_url=f"sqlite:///{(tmp_path / 'a' / 'b' / 'x.db').as_posix()}", storage_path=str(tmp_path / "deep" / "store"), app_env="test", _env_file=None)
    cont = Container(s, run_jobs_inline=True)
    cont.init_schema()
    assert (tmp_path / "deep" / "store").is_dir() and (tmp_path / "a" / "b" / "x.db").is_file()


def test_all_text_file_io_is_explicit_utf8_for_windows():
    """Path.read_text/open() default to the locale encoding (cp1252 on Windows); every call in app code must pass encoding=."""
    offenders = []
    for p in (ROOT / "app").rglob("*.py"):
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\.(read_text|write_text)\(", line) and "encoding" not in line:
                offenders.append(f"{p.name}:{n}")
            if re.search(r"(?<![\w.])open\(", line) and "encoding" not in line and "'rb'" not in line and '"rb"' not in line and "wb" not in line:
                offenders.append(f"{p.name}:{n}")
    assert not offenders, offenders
    assert Settings.model_config["env_file_encoding"] == "utf-8"


def test_utf8_metadata_and_csv_roundtrip_with_non_ascii_place_names(app_env):
    client, _ = app_env
    trained(client)
    name = "Vikhroli (विक्रोळी), मुंबई"
    client.post("/api/predict", json=predict_body(0, location_name=name))
    assert client.get("/api/history").json()["items"][0]["location_name"] == name
    assert name in client.get("/api/history/export.csv").content.decode("utf-8")
    assert client.get("/api/verification").status_code == 200


def test_config_errors_are_useful(tmp_path):
    with pytest.raises(ValueError, match="CITY_BBOX"):
        Container(Settings(database_url=f"sqlite:///{tmp_path/'c.db'}", storage_path=str(tmp_path / "s"), city_bbox="19,72,18,73", _env_file=None))
    with pytest.raises(KeyError, match="Unknown city"):
        Container(Settings(database_url=f"sqlite:///{tmp_path/'d.db'}", storage_path=str(tmp_path / "s"), city="atlantis", _env_file=None))
    c = Container(Settings(database_url=f"sqlite:///{tmp_path/'e.db'}", storage_path=str(tmp_path / "s"), city_bbox="18.9,72.7,19.4,73.1", _env_file=None))
    assert c.city.coverage(19.0, 72.9)["eligible"] and not c.city.coverage(19.6, 72.9)["eligible"]


def test_legacy_city_json_with_zoom_key_still_loads(tmp_path):
    from app.cities import load_cities
    f = tmp_path / "cities.json"
    legacy_key = "default" + "_zoom"          # key written by the retired map feature; must be ignored, not rejected
    f.write_text('[{"key":"pune","name":"Pune","timezone":"Asia/Kolkata","min_lat":18.4,"min_lon":73.7,"max_lat":18.65,"max_lon":74.0,"center_lat":18.52,"center_lon":73.85,"%s":11}]' % legacy_key, encoding="utf-8")
    assert "pune" in load_cities(str(f))


def test_temporary_upload_cleanup_still_applies(app_env):
    from app.database.models import Dataset, utcnow
    from app.services.cleanup_service import run_cleanup
    client, c = app_env
    d = upload(client, api_df().head(50)).json()["dataset_id"]
    with c.session() as s:
        s.get(Dataset, d).expires_at = utcnow() - timedelta(hours=1)
    assert run_cleanup(c)["files_deleted"] == 1 and client.get(f"/api/datasets/{d}/explore").status_code == 404




def test_migration_0002_adds_location_name_and_roundtrips(tmp_path):
    import os
    import sqlite3
    import subprocess
    import sys

    db = tmp_path / "m.db"

    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db.as_posix()}"
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(ROOT), env.get("PYTHONPATH", "")])
    )

    def run(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    result = run("upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr

    with sqlite3.connect(db) as connection:
        columns = [
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(predictions)"
            )
        ]

    assert "location_name" in columns

    result = run("check")
    assert result.returncode == 0, result.stdout + result.stderr

    result = run("downgrade", "0001")
    assert result.returncode == 0, result.stdout + result.stderr

    with sqlite3.connect(db) as connection:
        columns = [
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(predictions)"
            )
        ]

    assert "location_name" not in columns

    result = run("upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr
