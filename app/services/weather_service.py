"""Open-Meteo integration (all URLs configurable). Independent of the ML code.

What Open-Meteo actually provides: gridded NWP/reanalysis model output, NOT station observations. Every value this
service returns therefore carries an explicit ``kind``:

  current_model_analysis      "current" block of the forecast API (model analysis at the current time)
  recent_model_analysis       hourly precipitation for past hours served by the forecast API (``past_days``)
  historical_reanalysis       hourly precipitation from the Historical Weather (archive) API
  forecast                    hourly precipitation for future hours
  unavailable                 missing/invalid in the provider response — never converted to 0

A rainfall window (1/24/72 h) is only a number when EVERY hour in it is present and valid; otherwise value=None.
Endpoint shapes follow Open-Meteo's public documentation as understood when written; they could not be exercised
against the live service from the build sandbox (network blocked) and are covered by mocked tests only.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx

from app.config import Settings

log = logging.getLogger("wps.weather")
WINDOWS = {"rainfall_1h_mm": 1, "rainfall_24h_mm": 24, "rainfall_72h_mm": 72}
KIND_LABELS = {
    "current_model_analysis": "Current conditions (model analysis, not a station observation)",
    "recent_model_analysis": "Recent past precipitation (model analysis, not gauge data)",
    "historical_reanalysis": "Historical precipitation (reanalysis, not gauge data)",
    "forecast": "Forecast precipitation",
    "unavailable": "Unavailable",
}


class WeatherUnavailable(Exception):
    pass


# Open-Meteo "current" variables -> (internal name, expected unit, min, max). A unit mismatch is never converted silently.
_CURRENT_FIELDS = {
    "temperature_2m": ("temperature_c", "°C", -90, 60),
    "apparent_temperature": ("apparent_temperature_c", "°C", -120, 80),
    "relative_humidity_2m": ("relative_humidity_pct", "%", 0, 100),
    "wind_speed_10m": ("wind_speed_kmh", "km/h", 0, 500),
    "wind_direction_10m": ("wind_direction_deg", "°", 0, 360),
    "precipitation": ("precipitation_mm", "mm", 0, 1000),
    "rain": ("rain_mm", "mm", 0, 1000),
}
CURRENT_PARAM = ",".join(list(_CURRENT_FIELDS) + ["weather_code", "is_day"])
# WMO weather interpretation codes -> (label, icon key). Icon keys are drawn by the front end from local inline SVG.
_WMO = {0: ("Clear sky", "clear"), 1: ("Mainly clear", "partly"), 2: ("Partly cloudy", "partly"), 3: ("Overcast", "cloud"),
        45: ("Fog", "fog"), 48: ("Rime fog", "fog"), 51: ("Light drizzle", "drizzle"), 53: ("Drizzle", "drizzle"), 55: ("Dense drizzle", "drizzle"),
        56: ("Freezing drizzle", "drizzle"), 57: ("Dense freezing drizzle", "drizzle"), 61: ("Slight rain", "rain"), 63: ("Rain", "rain"),
        65: ("Heavy rain", "rain"), 66: ("Freezing rain", "rain"), 67: ("Heavy freezing rain", "rain"), 71: ("Slight snow", "snow"), 73: ("Snow", "snow"),
        75: ("Heavy snow", "snow"), 77: ("Snow grains", "snow"), 80: ("Slight rain showers", "rain"), 81: ("Rain showers", "rain"),
        82: ("Violent rain showers", "rain"), 85: ("Slight snow showers", "snow"), 86: ("Heavy snow showers", "snow"),
        95: ("Thunderstorm", "thunder"), 96: ("Thunderstorm with slight hail", "thunder"), 99: ("Thunderstorm with heavy hail", "thunder")}
_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def _num_in(v, lo, hi):
    f = _valid_any(v)
    return f if f is not None and lo <= f <= hi else None


def _valid_any(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def normalize_current(cur: dict | None, units: dict | None) -> dict:
    """Provider 'current' block -> one internal schema. Missing/invalid/unit-mismatched fields are 'unavailable' (never 0)."""
    cur, units = cur or {}, units or {}
    fields, any_ok = {}, False
    for api, (name, unit, lo, hi) in _CURRENT_FIELDS.items():
        raw = cur.get(api)
        val = _num_in(raw, lo, hi)
        if raw is None:
            f = {"value": None, "unit": unit, "status": "unavailable", "reason": "not reported by the provider"}
        elif val is None:
            f = {"value": None, "unit": unit, "status": "unavailable", "reason": "invalid value in provider response"}
        elif units.get(api) not in (None, unit):
            f = {"value": None, "unit": unit, "status": "unavailable", "reason": f"unexpected unit '{units.get(api)}'"}
        else:
            f = {"value": val, "unit": unit, "status": "available", "reason": None}
            any_ok = True
        fields[name] = f
    code = cur.get("weather_code")
    code = int(code) if isinstance(code, (int, float)) and not isinstance(code, bool) and float(code).is_integer() else None
    label, icon = _WMO.get(code, (None, None)) if code is not None else (None, None)
    deg = fields["wind_direction_deg"]["value"]
    obs = None
    if cur.get("time"):
        try:
            obs = datetime.fromisoformat(str(cur["time"])).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            obs = None
    out = {"kind": "current_model_analysis" if any_ok else "unavailable", "status": "available" if any_ok else "unavailable",
           "observation_time_utc": obs, "time_utc": cur.get("time"), "fields": fields,
           "condition": {"code": code, "label": label or "Not reported", "icon": icon or "unknown", "status": "available" if label else "unavailable"},
           "is_day": (bool(cur["is_day"]) if cur.get("is_day") in (0, 1) else None),
           "wind_direction_label": _COMPASS[int((deg % 360) / 22.5 + 0.5) % 16] if deg is not None else None}
    out.update({n: f["value"] for n, f in fields.items()})
    return out


def _fmt(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M")


def _valid(v) -> float | None:
    """Return a finite, non-negative float or None. Missing/invalid is None, never 0."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f >= 0 else None


class WeatherService:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.s = settings
        self.client = client or httpx.Client(timeout=settings.weather_timeout_s,
                                             headers={"User-Agent": "waterlogging-prediction-system/0.2"})
        self._cache: dict[tuple, tuple[float, object, datetime]] = {}
        self._inflight: dict[tuple, Future] = {}
        self._lock = threading.Lock()
        self.requests_made = 0   # counts real provider calls (used by tests and diagnostics)

    # ------------------------------------------------------------ credentials (never logged)
    def _auth(self, url: str) -> dict:
        key = self.s.open_meteo_api_key
        if not key:
            return {}
        host = urlparse(url).hostname or ""
        if host.endswith("customer-api.open-meteo.com") or host.startswith("customer-"):
            return {"apikey": key}
        log.warning("OPEN_METEO_API_KEY is set but %s is not an Open-Meteo customer endpoint; key NOT sent", host)
        return {}

    # ------------------------------------------------------------ http: timeout, bounded retry, cache, stale
    def _get(self, url: str, params: dict, key: tuple, fresh: bool = False):
        """fresh=True skips the cache read (user-triggered refresh). Identical concurrent requests share one provider call."""
        hit = self._cache.get(key)
        if not fresh and hit and time.monotonic() - hit[0] < self.s.weather_cache_ttl_s:
            return hit[1], False, hit[2]
        with self._lock:
            fut = self._inflight.get(key)
            leader = fut is None
            if leader:
                fut = self._inflight[key] = Future()
        if not leader:
            return fut.result()
        try:
            res = self._request(url, params, key, hit)
            fut.set_result(res)
            return res
        except BaseException as e:
            fut.set_exception(e)
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)

    def _request(self, url: str, params: dict, key: tuple, hit):
        now = time.monotonic()
        params = {**params, **self._auth(url)}
        last = None
        for attempt in range(self.s.weather_max_retries + 1):
            try:
                self.requests_made += 1
                r = self.client.get(url, params=params)
                if r.status_code == 429:
                    last = "rate limited by provider"
                    if attempt < self.s.weather_max_retries:
                        time.sleep(min(float(r.headers.get("retry-after", "1") or 1), 5.0))
                        continue
                elif r.status_code >= 500:
                    last = f"provider error {r.status_code}"
                    if attempt < self.s.weather_max_retries:
                        time.sleep(0.3 * 2 ** attempt)
                        continue
                elif r.status_code in (401, 403):
                    raise WeatherUnavailable("The weather provider rejected the credentials or access (check configuration).")
                elif r.status_code >= 400:
                    raise WeatherUnavailable(f"Weather provider rejected the request ({r.status_code}).")
                else:
                    data = r.json()
                    fetched = datetime.now(timezone.utc)
                    self._cache[key] = (now, data, fetched)
                    return data, False, fetched
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = type(e).__name__
                if attempt < self.s.weather_max_retries:
                    time.sleep(0.3 * 2 ** attempt)
                    continue
            except ValueError:
                raise WeatherUnavailable("Weather provider returned an unreadable response.")
            break
        if hit and now - hit[0] < self.s.weather_stale_max_s:
            log.warning("weather provider failing (%s); serving stale cache from %s", last, hit[2].isoformat())
            return hit[1], True, hit[2]
        log.warning("weather provider unavailable: %s", last)
        raise WeatherUnavailable(f"Weather service is unavailable ({last}). Try again later.")

    # ------------------------------------------------------------ hourly series
    def _hourly(self, url: str, params: dict, key: tuple, fresh: bool = False):
        data, stale, fetched = self._get(url, params, key, fresh)
        h = data.get("hourly") if isinstance(data, dict) else None
        if not h or "time" not in h or "precipitation" not in h or len(h["time"]) != len(h["precipitation"]):
            raise WeatherUnavailable("Weather provider returned an unexpected response format.")
        unit = (data.get("hourly_units") or {}).get("precipitation")
        if unit != "mm":
            raise WeatherUnavailable(f"Unexpected precipitation unit '{unit}' (expected mm).")
        off = data.get("utc_offset_seconds")
        if off not in (None, 0):
            raise WeatherUnavailable("Weather provider returned non-UTC timestamps; refusing to guess time zones.")
        series = {t: _valid(v) for t, v in zip(h["time"], h["precipitation"])}
        return series, data, stale, fetched

    @staticmethod
    def _windows(series: dict, hour: datetime, kind: str) -> dict:
        out = {}
        for name, n in WINDOWS.items():
            vals = [series.get(_fmt(hour - timedelta(hours=k))) for k in range(n)]
            present = sum(v is not None for v in vals)
            ok = present == n
            out[name] = {"value_mm": round(sum(vals), 3) if ok else None, "status": "available" if ok else "unavailable",
                         "kind": kind if ok else "unavailable", "hours_present": present, "hours_expected": n,
                         "window_end_utc": hour.isoformat()}
        return out

    @staticmethod
    def _stamp(fetched, stale):
        return {"last_updated_utc": fetched.isoformat(), "stale": stale,
                "data_age_s": max(0, int((datetime.now(timezone.utc) - fetched).total_seconds()))}

    # ------------------------------------------------------------ public API
    def forecast(self, lat: float, lon: float, now: datetime | None = None, fresh: bool = False) -> dict:
        """Current analysis + last 72 h (recent analysis) + next 24 h (forecast), each labelled by kind."""
        lat, lon = round(lat, 3), round(lon, 3)
        params = {"latitude": lat, "longitude": lon, "hourly": "precipitation", "past_days": 3, "forecast_days": 2,
                  "timezone": "UTC", "current": CURRENT_PARAM}
        series, data, stale, fetched = self._hourly(self.s.weather_forecast_url, params, ("forecast", lat, lon), fresh)
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        hour = now.replace(minute=0, second=0, microsecond=0)
        windows = self._windows(series, hour, "recent_model_analysis")
        nxt = []
        for k in range(1, 25):
            t = hour + timedelta(hours=k)
            v = series.get(_fmt(t))
            nxt.append({"time_utc": _fmt(t), "precipitation_mm": v, "status": "available" if v is not None else "unavailable",
                        "kind": "forecast" if v is not None else "unavailable"})
        tot_ok = all(x["precipitation_mm"] is not None for x in nxt)
        current = normalize_current(data.get("current"), data.get("current_units"))
        return {
            "provider": "open-meteo", "timezone": "UTC", **self._stamp(fetched, stale),
            "retrieved_at_utc": now.isoformat(), "reference_hour_utc": hour.isoformat(), "latitude": lat, "longitude": lon,
            "kind_labels": KIND_LABELS,
            "current": current,
            "windows": windows,
            "rainfall_windows_mm": {k: v["value_mm"] for k, v in windows.items()},   # kept for API compatibility
            "forecast_next_24h": nxt,
            "forecast_next_24h_total": {"value_mm": round(sum(x["precipitation_mm"] for x in nxt), 3) if tot_ok else None,
                                        "kind": "forecast" if tot_ok else "unavailable", "status": "available" if tot_ok else "unavailable"},
            "forecast_next_24h_total_mm": round(sum(x["precipitation_mm"] for x in nxt), 3) if tot_ok else None,
        }

    def windows_at(self, lat: float, lon: float, ts: datetime, now: datetime | None = None) -> dict:
        """Rainfall accumulation windows ending at `ts` (hour-floored UTC). Recent hours come from the forecast API's
        past data (model analysis); older hours from the Historical Weather API (reanalysis)."""
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        ts = ts.astimezone(timezone.utc)
        if ts > now + timedelta(hours=1):
            raise WeatherUnavailable("The timestamp is in the future; accumulated rainfall for it cannot be known.")
        hour = ts.replace(minute=0, second=0, microsecond=0)
        age = now - hour
        lat, lon = round(lat, 3), round(lon, 3)
        if age <= timedelta(days=85):
            past = min(92, int(math.ceil(age.total_seconds() / 86400)) + 4)
            params = {"latitude": lat, "longitude": lon, "hourly": "precipitation", "past_days": past, "forecast_days": 1, "timezone": "UTC"}
            series, _, stale, fetched = self._hourly(self.s.weather_forecast_url, params, ("fc-past", lat, lon, past))
            kind = "recent_model_analysis"
        else:
            start = (hour - timedelta(hours=71)).date().isoformat()
            params = {"latitude": lat, "longitude": lon, "hourly": "precipitation", "start_date": start,
                      "end_date": hour.date().isoformat(), "timezone": "UTC"}
            series, _, stale, fetched = self._hourly(self.s.weather_archive_url, params, ("archive", lat, lon, start, hour.date().isoformat()))
            kind = "historical_reanalysis"
        return {"windows": self._windows(series, hour, kind), "reference_hour_utc": hour.isoformat(), "kind": kind, **self._stamp(fetched, stale)}

    def elevation(self, lat: float, lon: float, fresh: bool = False) -> float:
        lat, lon = round(lat, 4), round(lon, 4)
        data, _, _ = self._get(self.s.weather_elevation_url, {"latitude": lat, "longitude": lon}, ("elev", lat, lon), fresh)
        try:
            v = float(data["elevation"][0])
        except (KeyError, IndexError, TypeError, ValueError):
            raise WeatherUnavailable("Elevation service returned an unexpected response.")
        if not math.isfinite(v):
            raise WeatherUnavailable("Elevation service returned an invalid value.")
        return v

    def slope(self, lat: float, lon: float, step_m: float = 120.0, fresh: bool = False) -> dict:
        """Approximate terrain slope (degrees) from five DEM elevation samples (centre + N/S/E/W at ~step_m) via central differences.
        Coarse by construction (the elevation DEM is ~90 m resolution): it is a DERIVED estimate and may differ from the slope
        used when a model was trained. Returns {value_deg, step_m, elevations}."""
        lat, lon = round(lat, 4), round(lon, 4)
        dlat = step_m / 111_320.0
        dlon = step_m / (111_320.0 * max(math.cos(math.radians(lat)), 0.01))
        pts = [(lat, lon), (lat + dlat, lon), (lat - dlat, lon), (lat, lon + dlon), (lat, lon - dlon)]
        params = {"latitude": ",".join(f"{p[0]:.6f}" for p in pts), "longitude": ",".join(f"{p[1]:.6f}" for p in pts)}
        data, _, _ = self._get(self.s.weather_elevation_url, params, ("slope", lat, lon, step_m), fresh)
        try:
            z = [float(v) for v in data["elevation"]]
        except (KeyError, TypeError, ValueError):
            raise WeatherUnavailable("Elevation service returned an unexpected response.")
        if len(z) != 5 or not all(math.isfinite(v) for v in z):
            raise WeatherUnavailable("Elevation service did not return the five samples needed to derive slope.")
        c, n, so, e, w = z
        dzdy = (n - so) / (2 * step_m)
        dzdx = (e - w) / (2 * step_m)
        return {"value_deg": round(math.degrees(math.atan(math.hypot(dzdx, dzdy))), 3), "step_m": step_m, "elevations_m": z}

    def geocode(self, name: str, count: int = 10) -> list[dict]:
        """Place search: country-filtered, geo-validated, deduplicated. Region ranking is done by the caller."""
        name = name.strip()[:100]
        params = {"name": name, "count": count, "language": "en", "format": "json"}
        if self.s.weather_geocoding_country:
            params["countryCode"] = self.s.weather_geocoding_country
        data, _, _ = self._get(self.s.weather_geocoding_url, params, ("geo", name.lower(), count, self.s.weather_geocoding_country))
        seen, out = set(), []
        for r in (data.get("results") or []) if isinstance(data, dict) else []:
            la, lo = _valid_coord(r.get("latitude"), -90, 90), _valid_coord(r.get("longitude"), -180, 180)
            if la is None or lo is None or not r.get("name"):
                continue
            key = (r["name"].strip().lower(), (r.get("admin1") or "").lower(), round(la, 2), round(lo, 2))
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": r["name"], "admin1": r.get("admin1"), "admin2": r.get("admin2"), "country": r.get("country"),
                        "latitude": la, "longitude": lo})
        return out


def _valid_coord(v, lo, hi):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and lo <= f <= hi else None
