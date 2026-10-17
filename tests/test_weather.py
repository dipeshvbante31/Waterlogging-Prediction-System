from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.config import Settings
from app.services.weather_service import WeatherService, WeatherUnavailable
from tests.conftest import api_df, csv_bytes

NOW = datetime(2026, 10, 14, 10, 30, tzinfo=timezone.utc)


def hourly(missing_at=None, unit="mm"):
    start = datetime(2026, 10, 11, 0, 0)
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(24 * 5)]
    vals = [1.0] * len(times)
    if missing_at:
        vals[times.index(missing_at)] = None
    return {"hourly": {"time": times, "precipitation": vals}, "hourly_units": {"precipitation": unit},
            "current": {"time": "2026-10-14T10:30", "precipitation": 0.2, "temperature_2m": 29.1}}


def svc(handler, **kw):
    s = Settings(weather_max_retries=kw.get("retries", 2), weather_cache_ttl_s=kw.get("ttl", 600), _env_file=None)
    return WeatherService(s, httpx.Client(transport=httpx.MockTransport(handler)))


def test_windows_sum_hourly_values_and_report_times():
    w = svc(lambda r: httpx.Response(200, json=hourly())).forecast(19.07, 72.87, NOW)
    assert w["rainfall_windows_mm"] == {"rainfall_1h_mm": 1.0, "rainfall_24h_mm": 24.0, "rainfall_72h_mm": 72.0}
    assert w["reference_hour_utc"].startswith("2026-10-14T10:00") and w["timezone"] == "UTC"
    assert w["windows"]["rainfall_24h_mm"]["kind"] == "recent_model_analysis" and w["forecast_next_24h_total_mm"] == 24.0 and not w["stale"]
    assert w["windows"]["rainfall_24h_mm"]["hours_present"] == 24


def test_incomplete_window_is_none_never_invented():
    w = svc(lambda r: httpx.Response(200, json=hourly(missing_at="2026-10-14T05:00"))).forecast(19, 72.8, NOW)
    assert w["rainfall_windows_mm"]["rainfall_1h_mm"] == 1.0 and w["rainfall_windows_mm"]["rainfall_24h_mm"] is None


def test_wrong_unit_or_shape_rejected():
    with pytest.raises(WeatherUnavailable, match="unit"):
        svc(lambda r: httpx.Response(200, json=hourly(unit="inch"))).forecast(19, 72.8, NOW)
    with pytest.raises(WeatherUnavailable, match="format"):
        svc(lambda r: httpx.Response(200, json={"hourly": {"time": ["x"], "precipitation": []}})).forecast(19, 72.8, NOW)


def test_retries_then_succeeds_and_caches(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def h(r):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json=hourly())
    s = svc(h)
    s.forecast(19, 72.8, NOW); n = calls["n"]
    s.forecast(19, 72.8, NOW)
    assert n == 3 and calls["n"] == 3  # retried twice, second call served from cache


def test_bounded_retries_then_unavailable(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}
    def h(r):
        calls["n"] += 1
        return httpx.Response(500)
    with pytest.raises(WeatherUnavailable):
        svc(h, retries=2).forecast(19, 72.8, NOW)
    assert calls["n"] == 3


def test_timeout_and_rate_limit_handled(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    def timeout(r): raise httpx.ReadTimeout("slow")
    with pytest.raises(WeatherUnavailable, match="ReadTimeout"):
        svc(timeout, retries=1).forecast(19, 72.8, NOW)
    with pytest.raises(WeatherUnavailable, match="rate limited"):
        svc(lambda r: httpx.Response(429, headers={"retry-after": "1"}), retries=1).forecast(19, 72.8, NOW)


def test_client_error_is_not_retried():
    calls = {"n": 0}
    def h(r):
        calls["n"] += 1
        return httpx.Response(400)
    with pytest.raises(WeatherUnavailable, match="rejected"):
        svc(h).forecast(19, 72.8, NOW)
    assert calls["n"] == 1


def test_stale_cache_served_when_provider_down(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    state = {"up": True}
    s = svc(lambda r: httpx.Response(200, json=hourly()) if state["up"] else httpx.Response(503), ttl=0)
    s.forecast(19, 72.8, NOW)
    state["up"] = False
    assert s.forecast(19, 72.8, NOW)["stale"] is True


def test_elevation_and_geocode_parsing_and_errors():
    def h(r):
        if "elevation" in str(r.url):
            return httpx.Response(200, json={"elevation": [11.0]})
        return httpx.Response(200, json={"results": [{"name": "Mumbai", "admin1": "Maharashtra", "country": "India", "latitude": 19.07, "longitude": 72.88}]})
    s = svc(h)
    assert s.elevation(19.07, 72.87) == 11.0 and s.geocode("Mumbai")[0]["country"] == "India"
    with pytest.raises(WeatherUnavailable):
        svc(lambda r: httpx.Response(200, json={"elevation": []})).elevation(1, 1)


def test_resolve_features_labels_sources_and_marks_unsupplied_missing():
    from app.services.feature_service import resolve_features

    def h(r):
        return httpx.Response(200, json={"elevation": [11.0]}) if "elevation" in str(r.url) else httpx.Response(200, json=hourly())
    res = resolve_features(svc(h), 19.07, 72.87, NOW, ["rainfall_1h_mm", "rainfall_24h_mm", "elevation_m", "slope_deg", "drainage_capacity"], now=NOW)
    f = res["features"]
    assert f["rainfall_24h_mm"]["value"] == 24.0 and f["rainfall_24h_mm"]["source"] == "open-meteo" and f["rainfall_24h_mm"]["kind"] == "recent_model_analysis"
    assert f["elevation_m"]["kind"] == "dem_elevation"
    assert res["missing"] == ["slope_deg", "drainage_capacity"] and f["slope_deg"]["value"] is None and f["slope_deg"]["status"] == "unavailable"
    res2 = resolve_features(svc(h), 19.07, 72.87, NOW, ["slope_deg"], manual={"slope_deg": 2.5}, now=NOW)
    assert res2["features"]["slope_deg"] == {"value": 2.5, "source": "user-supplied", "kind": "user_supplied", "status": "available"} and not res2["missing"]


def test_genuine_zero_is_distinct_from_missing():
    data = hourly()
    data["hourly"]["precipitation"] = [0.0] * len(data["hourly"]["time"])
    w = svc(lambda r: httpx.Response(200, json=data)).forecast(19, 72.8, NOW)
    assert w["windows"]["rainfall_24h_mm"]["value_mm"] == 0.0 and w["windows"]["rainfall_24h_mm"]["status"] == "available"
    data["hourly"]["precipitation"][data["hourly"]["time"].index("2026-10-14T09:00")] = None
    w = svc(lambda r: httpx.Response(200, json=data)).forecast(19, 72.8, NOW)
    assert w["windows"]["rainfall_24h_mm"]["value_mm"] is None and w["windows"]["rainfall_24h_mm"]["status"] == "unavailable"
    assert w["windows"]["rainfall_24h_mm"]["kind"] == "unavailable" and w["windows"]["rainfall_24h_mm"]["hours_present"] == 23


def test_negative_or_non_numeric_values_are_missing_not_zero():
    data = hourly()
    data["hourly"]["precipitation"][data["hourly"]["time"].index("2026-10-14T10:00")] = -1
    data["hourly"]["precipitation"][data["hourly"]["time"].index("2026-10-14T09:00")] = "x"
    w = svc(lambda r: httpx.Response(200, json=data)).forecast(19, 72.8, NOW)
    assert w["windows"]["rainfall_1h_mm"]["value_mm"] is None and w["current"]["status"] == "available"


def test_non_utc_offset_refused():
    d = hourly(); d["utc_offset_seconds"] = 19800
    with pytest.raises(WeatherUnavailable, match="non-UTC"):
        svc(lambda r: httpx.Response(200, json=d)).forecast(19, 72.8, NOW)


def test_current_block_is_labelled_model_analysis_and_missing_current_is_unavailable():
    w = svc(lambda r: httpx.Response(200, json=hourly())).forecast(19, 72.8, NOW)
    assert w["current"]["kind"] == "current_model_analysis" and "not a station observation" in w["kind_labels"]["current_model_analysis"]
    d = hourly(); d["current"] = {}
    assert svc(lambda r: httpx.Response(200, json=d)).forecast(19, 72.8, NOW)["current"]["status"] == "unavailable"


def test_forecast_hours_are_labelled_forecast_never_observed():
    w = svc(lambda r: httpx.Response(200, json=hourly())).forecast(19, 72.8, NOW)
    assert {x["kind"] for x in w["forecast_next_24h"]} == {"forecast"}


def test_stale_result_reports_last_updated(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    state = {"up": True}
    s = svc(lambda r: httpx.Response(200, json=hourly()) if state["up"] else httpx.Response(503), ttl=0)
    fresh = s.forecast(19, 72.8, NOW); state["up"] = False
    stale = s.forecast(19, 72.8, NOW)
    assert stale["stale"] is True and stale["last_updated_utc"] == fresh["last_updated_utc"] and stale["data_age_s"] >= 0


def test_windows_at_uses_archive_for_old_timestamps_and_refuses_future():
    seen = []
    def h(r):
        seen.append(str(r.url))
        start = datetime(2026, 1, 1)
        times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(24 * 40)]
        return httpx.Response(200, json={"hourly": {"time": times, "precipitation": [2.0] * len(times)}, "hourly_units": {"precipitation": "mm"}})
    s = svc(h)
    r = s.windows_at(19.0, 72.8, datetime(2026, 1, 20, 12, 0, tzinfo=timezone.utc), now=NOW)
    assert r["kind"] == "historical_reanalysis" and r["windows"]["rainfall_24h_mm"]["value_mm"] == 48.0 and "archive" in seen[0]
    with pytest.raises(WeatherUnavailable, match="future"):
        s.windows_at(19.0, 72.8, NOW + timedelta(days=2), now=NOW)
    recent = svc(lambda q: httpx.Response(200, json=hourly())).windows_at(19.0, 72.8, NOW - timedelta(hours=2), now=NOW)
    assert recent["kind"] == "recent_model_analysis"


def test_geocode_dedupes_validates_and_filters_country():
    seen = {}
    def h(r):
        seen["url"] = str(r.url)
        base = {"name": "Andheri", "admin1": "Maharashtra", "country": "India", "latitude": 19.1136, "longitude": 72.8697}
        return httpx.Response(200, json={"results": [base, {**base, "latitude": 19.1139}, {**base, "latitude": 95}, {**base, "name": ""}, {**base, "latitude": "x"}]})
    res = svc(h).geocode("Andheri")
    assert len(res) == 1 and "countryCode=IN" in seen["url"]


def test_api_key_is_not_sent_to_free_endpoint_and_never_logged(caplog):
    seen = []
    s = Settings(open_meteo_api_key="SECRET-KEY-123", _env_file=None)
    w = WeatherService(s, httpx.Client(transport=httpx.MockTransport(lambda r: (seen.append(str(r.url)), httpx.Response(200, json=hourly()))[1])))
    with caplog.at_level("DEBUG"):
        w.forecast(19, 72.8, NOW)
    assert "SECRET-KEY-123" not in seen[0] and "SECRET-KEY-123" not in caplog.text
    s2 = Settings(open_meteo_api_key="SECRET-KEY-123", weather_forecast_url="https://customer-api.open-meteo.com/v1/forecast", _env_file=None)
    seen.clear()
    WeatherService(s2, httpx.Client(transport=httpx.MockTransport(lambda r: (seen.append(str(r.url)), httpx.Response(200, json=hourly()))[1]))).forecast(19, 72.8, NOW)
    assert "apikey=SECRET-KEY-123" in seen[0]


def test_auth_failure_has_safe_message():
    with pytest.raises(WeatherUnavailable) as e:
        svc(lambda r: httpx.Response(401, text="bad key SECRET")).forecast(19, 72.8, NOW)
    assert "SECRET" not in str(e.value)


# ------------------------------------------------------------------ API integration (mocked provider)
def _mock_provider(c):
    def h(r):
        return httpx.Response(200, json={"elevation": [11.0]}) if "elevation" in str(r.url) else httpx.Response(200, json=hourly_now())
    c.weather = WeatherService(c.settings, httpx.Client(transport=httpx.MockTransport(h)))


def hourly_now():
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(days=3)
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(24 * 5 + 1)]
    return {"hourly": {"time": times, "precipitation": [0.5] * len(times)}, "hourly_units": {"precipitation": "mm"},
            "current": {"time": times[72], "precipitation": 0.1, "temperature_2m": 28}}


def test_weather_api_and_provider_outage(app_env):
    client, c = app_env
    _mock_provider(c)
    r = client.get("/api/weather?lat=19.07&lon=72.87").json()
    assert r["weather"]["rainfall_windows_mm"]["rainfall_24h_mm"] == 12.0 and r["elevation_m"] == 11.0
    assert r["in_configured_region"] is True and "not proof" in r["note"]
    c.weather = WeatherService(c.settings, httpx.Client(transport=httpx.MockTransport(lambda q: httpx.Response(500))))
    c.settings.weather_max_retries = 0
    out = client.get("/api/weather?lat=19.0&lon=72.8")
    assert out.status_code == 503 and out.json()["code"] == "weather_unavailable"
    assert client.get("/api/weather?lat=999&lon=0").status_code == 422


def test_weather_predict_unavailable_when_model_needs_slope_and_drainage(app_env, admin):
    client, c = app_env
    _mock_provider(c)
    body = {"latitude": 19.07, "longitude": 72.87}
    r = client.post("/api/weather/predict", json=body)
    assert r.status_code == 409 and r.json()["code"] == "no_model"
    d = client.post("/api/datasets?kind=train", files={"file": ("d.csv", csv_bytes(api_df()), "text/csv")}).json()
    j = client.post("/api/train", json={"dataset_id": d["dataset_id"], "provenance_attested": True,
                                        "label_provenance": "SYNTHETIC TEST FIXTURE"}, headers=admin).json()
    if client.get(f"/api/jobs/{j['id']}").json()["result"]["outcome"] != "model_saved":
        pytest.skip("no model")
    out = client.post("/api/weather/predict", json=body).json()
    assert out["available"] is False and set(out["missing_inputs"]) == {"slope_deg", "drainage_capacity"}
    assert "result" not in out  # no misleading prediction is produced


def test_weather_predict_works_when_model_only_needs_supplied_inputs(app_env, admin):
    client, c = app_env
    _mock_provider(c)
    df = api_df().drop(columns=["slope_deg", "drainage_capacity"])
    d = client.post("/api/datasets?kind=train", files={"file": ("d.csv", csv_bytes(df), "text/csv")}).json()
    j = client.post("/api/train", json={"dataset_id": d["dataset_id"], "provenance_attested": True,
                                        "label_provenance": "SYNTHETIC TEST FIXTURE"}, headers=admin).json()
    if client.get(f"/api/jobs/{j['id']}").json()["result"]["outcome"] != "model_saved":
        pytest.skip("no model")
    out = client.post("/api/weather/predict", json={"latitude": 19.07, "longitude": 72.87, "save_history": True}).json()
    assert out["available"] is True and 0 <= out["result"]["probability"] <= 1
    assert out["inputs_used"]["rainfall_24h_mm"] == 12.0 and any("model-derived" in w for w in out["warnings"])
    assert out["explanation"]["items"] and client.get("/api/history").json()["items"][0]["source"] == "weather"


def test_weather_endpoint_separates_availability_from_coverage(app_env):
    client, c = app_env
    _mock_provider(c)
    inside = client.get("/api/weather?lat=19.07&lon=72.87").json()
    assert inside["coverage"]["eligible"] is True and inside["weather"]["windows"]["rainfall_24h_mm"]["kind"] == "recent_model_analysis"
    navi = client.get("/api/weather?lat=19.03&lon=73.03").json()   # Navi Mumbai: inside the MMR box (was wrongly outside before)
    assert navi["coverage"]["eligible"] is True
    delhi = client.get("/api/weather?lat=28.6&lon=77.2").json()
    assert delhi["weather"]["windows"]["rainfall_24h_mm"]["status"] == "available"   # weather still works
    assert delhi["coverage"]["eligible"] is False and "outside the configured model coverage" in delhi["coverage"]["reason"]


def test_elevation_failure_is_explained_not_hidden(app_env):
    client, c = app_env
    def h(r):
        return httpx.Response(500) if "elevation" in str(r.url) else httpx.Response(200, json=hourly_now())
    c.settings.weather_max_retries = 0
    c.weather = WeatherService(c.settings, httpx.Client(transport=httpx.MockTransport(h)))
    r = client.get("/api/weather?lat=19.07&lon=72.87").json()
    assert r["elevation"]["status"] == "unavailable" and r["elevation"]["error"] and r["elevation_m"] is None


def test_geocode_endpoint_ranks_in_region_first(app_env):
    client, c = app_env
    def h(r):
        return httpx.Response(200, json={"results": [
            {"name": "Andheri", "admin1": "Rajasthan", "country": "India", "latitude": 26.0, "longitude": 75.0},
            {"name": "Andheri", "admin1": "Maharashtra", "country": "India", "latitude": 19.1136, "longitude": 72.8697}]})
    c.weather = WeatherService(c.settings, httpx.Client(transport=httpx.MockTransport(h)))
    res = client.get("/api/geocode?q=Andheri").json()["results"]
    assert res[0]["admin1"] == "Maharashtra" and res[0]["coverage_eligible"] is True and res[1]["coverage_eligible"] is False
    assert client.get("/api/geocode?q=a").status_code == 422


def test_features_endpoint_provenance_and_manual_fill(app_env):
    client, c = app_env
    _mock_provider(c)
    r = client.post("/api/features", json={"latitude": 19.07, "longitude": 72.87, "manual": {"slope_deg": 1.5}}).json()
    f = r["features"]
    assert f["rainfall_24h_mm"]["source"] == "open-meteo" and f["slope_deg"]["source"] == "user-supplied"
    assert f["drainage_capacity"]["status"] == "unavailable" and f["drainage_capacity"]["value"] is None and r["ready"] is False
    assert client.post("/api/features", json={"latitude": 19.07, "longitude": 72.87, "timestamp": "01/07/2024"}).status_code == 422
    assert client.post("/api/features", json={"latitude": 19.07, "longitude": 72.87, "timestamp": "2099-01-01 10:00"}).json()["features"]["rainfall_24h_mm"]["status"] == "unavailable"


def test_features_when_provider_down_are_unavailable_never_zero(app_env):
    client, c = app_env
    c.settings.weather_max_retries = 0
    c.weather = WeatherService(c.settings, httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    r = client.post("/api/features", json={"latitude": 19.07, "longitude": 72.87}).json()
    assert all(v["value"] is None for k, v in r["features"].items()) and r["ready"] is False and r["notes"]


def test_weather_status_probe_is_a_real_request_not_a_cache_read(app_env):
    client, c = app_env
    counter = {"n": 0}

    def h(r):
        counter["n"] += 1
        return httpx.Response(200, json=hourly_now())
    c.weather = WeatherService(c.settings, httpx.Client(transport=httpx.MockTransport(h)))
    c.weather.forecast(19.076, 72.878)                      # warms the cache for the default location
    assert counter["n"] == 1
    st = client.get("/api/weather/status").json()
    assert st["ok"] is True and st["stale"] is False and counter["n"] == 2  # the probe made its own request
    client.get("/api/weather/status")
    assert counter["n"] == 2                                  # but repeated dashboard loads inside a minute reuse the last probe result


def test_weather_status_reports_stale_or_failure_when_provider_goes_down(app_env, monkeypatch):
    client, c = app_env
    monkeypatch.setattr("time.sleep", lambda s: None)
    c.settings.weather_max_retries = 0
    state = {"up": True}
    c.weather = WeatherService(c.settings, httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=hourly_now()) if state["up"] else httpx.Response(500))))
    assert client.get("/api/weather/status").json()["stale"] is False
    state["up"] = False
    c._weather_probe = None                                   # next probe is due
    st = client.get("/api/weather/status").json()
    assert st["ok"] is True and st["stale"] is True            # only a stale fallback exists: never reported as live
    c.weather = WeatherService(c.settings, httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    c._weather_probe = None
    bad = client.get("/api/weather/status").json()
    assert bad["ok"] is False and "unavailable" in bad["error"].lower() and "checked_at_utc" in bad
