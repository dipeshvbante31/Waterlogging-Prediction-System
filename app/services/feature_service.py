"""Builds model input features for a point+time and records WHERE each value came from.
Used by single prediction and live-weather prediction so both share one code path."""
from __future__ import annotations

from datetime import datetime

from app.services.weather_service import WINDOWS, WeatherService, WeatherUnavailable

# Features no public provider supplies; the user must provide them (or the model cannot run).
USER_SUPPLIED = {"drainage_capacity"}  # slope_deg is DEM-derived when the elevation service answers; otherwise user-supplied


def resolve_features(ws: WeatherService, lat: float, lon: float, ts: datetime, need: list[str],
                     manual: dict | None = None, now: datetime | None = None) -> dict:
    """Return {features: {name: {value, source, kind, status, note}}, missing: [...], reference_hour_utc, stale, ...}.
    Provider values are used when available; otherwise a user-supplied value may fill the gap (clearly labelled).
    Nothing is ever defaulted to 0."""
    manual = manual or {}
    feats, missing, meta = {}, [], {"stale": False, "last_updated_utc": None, "reference_hour_utc": None, "notes": []}
    wins = None
    if any(n in WINDOWS for n in need):
        try:
            w = ws.windows_at(lat, lon, ts, now)
            wins = w["windows"]
            meta.update(stale=w["stale"], last_updated_utc=w["last_updated_utc"], reference_hour_utc=w["reference_hour_utc"])
        except WeatherUnavailable as e:
            meta["notes"].append(str(e))
    for name in need:
        entry = None
        if name in ("latitude", "longitude"):
            feats[name] = {"value": lat if name == "latitude" else lon, "source": "selected location", "kind": "user_supplied", "status": "available"}
            continue
        if name in WINDOWS and wins and wins[name]["status"] == "available":
            entry = {"value": wins[name]["value_mm"], "source": "open-meteo", "kind": wins[name]["kind"], "status": "available"}
        elif name == "elevation_m":
            try:
                entry = {"value": ws.elevation(lat, lon), "source": "open-meteo elevation", "kind": "dem_elevation", "status": "available"}
            except WeatherUnavailable as e:
                meta["notes"].append(f"elevation: {e}")
        elif name == "slope_deg":
            try:
                sl = ws.slope(lat, lon)
                entry = {"value": sl["value_deg"], "source": "open-meteo elevation (DEM-derived)", "kind": "dem_derived_slope", "status": "available",
                         "note": f"Approximate slope from 5 DEM samples ~{int(sl['step_m'])} m apart (coarse, ~90 m DEM); may differ from the slope used in training."}
            except WeatherUnavailable as e:
                meta["notes"].append(f"slope: {e}")
        if entry is None and manual.get(name) is not None:
            entry = {"value": float(manual[name]), "source": "user-supplied", "kind": "user_supplied", "status": "available"}
        if entry is None:
            entry = {"value": None, "source": None, "kind": "unavailable", "status": "unavailable",
                     "note": ("No public data source supplies this; enter it from your own data (same unit as your training data) or retrain the model without it."
                              if name in USER_SUPPLIED else "The provider did not return a complete, valid value; enter it manually if you have a trustworthy source.")}
            missing.append(name)
        feats[name] = entry
    return {"features": feats, "missing": missing, **meta}
