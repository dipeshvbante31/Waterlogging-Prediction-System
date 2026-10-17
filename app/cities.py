"""City profiles: the region a model/app is configured for.

The Mumbai box is an APPROXIMATE rectangle covering the Mumbai Metropolitan Region (it was previously a
city-only box that excluded Navi Mumbai, Vasai-Virar and Panvel). It is not an administrative boundary and can be
overridden with CITY_BBOX or a city JSON file. Other cities
are added through a JSON file (see ``CITY_CONFIG_PATH``) without code changes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass(frozen=True)
class CityProfile:
    key: str
    name: str
    timezone: str
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float
    center_lat: float
    center_lon: float

    def contains(self, lat, lon) -> bool:
        return self.min_lat <= lat <= self.max_lat and self.min_lon <= lon <= self.max_lon

    def coverage(self, lat, lon) -> dict:
        """Model-coverage eligibility. Independent of whether weather data exists for the point."""
        ok = self.contains(lat, lon)
        return {"eligible": ok, "region": self.name,
                "bbox": {"min_lat": self.min_lat, "min_lon": self.min_lon, "max_lat": self.max_lat, "max_lon": self.max_lon},
                "reason": None if ok else (f"This point is outside the configured model coverage area for {self.name} "
                                           f"(latitude {self.min_lat}–{self.max_lat}, longitude {self.min_lon}–{self.max_lon}). "
                                           "Weather can still be looked up here, but a model trained for this region must not be applied.")}

    def to_dict(self) -> dict:
        return asdict(self)


BUILTIN_CITIES: dict[str, CityProfile] = {
    "mumbai": CityProfile(
        key="mumbai", name="Mumbai Metropolitan Region, Maharashtra, India", timezone="Asia/Kolkata",
        min_lat=18.80, min_lon=72.70, max_lat=19.55, max_lon=73.30,
        center_lat=19.0760, center_lon=72.8777,
    )
}


def load_cities(config_path: str | None = None) -> dict[str, CityProfile]:
    cities = dict(BUILTIN_CITIES)
    if config_path and Path(config_path).is_file():
        raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
        for item in raw:
            item = {k: v for k, v in item.items() if k != "default_zoom"}  # legacy key from the retired map feature
            c = CityProfile(**item)
            cities[c.key] = c
    return cities


def get_city(key: str, config_path: str | None = None) -> CityProfile:
    cities = load_cities(config_path)
    if key not in cities:
        raise KeyError(f"Unknown city '{key}'. Known: {sorted(cities)}")
    return cities[key]
