"""OPT-IN live verification against the real Open-Meteo service. Skipped unless RUN_LIVE_WEATHER=1 and the network is reachable.
This is the ONLY test that proves live connectivity; mocked tests do not.
Run:  RUN_LIVE_WEATHER=1 python -m pytest tests/integration -m live -p no:cacheprovider"""
import os

import pytest

from app.config import Settings
from app.services.weather_service import WeatherService, WeatherUnavailable

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.environ.get("RUN_LIVE_WEATHER") != "1", reason="set RUN_LIVE_WEATHER=1 to call the real provider")]
MUMBAI = (19.076, 72.8777)


@pytest.fixture(scope="module")
def ws():
    return WeatherService(Settings(weather_cache_ttl_s=0, weather_max_retries=1, _env_file=None))


def test_live_forecast_current_block_has_real_values(ws):
    w = ws.forecast(*MUMBAI, fresh=True)
    cur = w["current"]
    assert cur["status"] == "available" and cur["fields"]["temperature_c"]["status"] == "available"
    assert -10 < cur["temperature_c"] < 55 and cur["observation_time_utc"] and 0 <= cur["relative_humidity_pct"] <= 100
    assert w["windows"]["rainfall_24h_mm"]["hours_present"] in range(0, 25) and w["timezone"] == "UTC" and w["stale"] is False


def test_live_second_request_is_a_new_provider_call(ws):
    before = ws.requests_made
    ws.forecast(*MUMBAI, fresh=True)
    assert ws.requests_made == before + 1


def test_live_elevation_slope_and_geocoding(ws):
    assert -50 < ws.elevation(*MUMBAI, fresh=True) < 500
    assert 0 <= ws.slope(*MUMBAI, fresh=True)["value_deg"] < 45
    res = ws.geocode("Mumbai")
    assert any("Maharashtra" in (r.get("admin1") or "") for r in res) and all(-90 <= r["latitude"] <= 90 for r in res)


def test_live_invalid_location_is_handled(ws):
    assert ws.geocode("zzzzqqqxxxnotaplace") == []
    with pytest.raises(WeatherUnavailable):
        ws.forecast(500, 500, fresh=True)
