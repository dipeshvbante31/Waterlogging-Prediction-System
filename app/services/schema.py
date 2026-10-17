"""Single authoritative definition of the dataset schema and validation rules.

The default schema is a PROPOSAL (not a claim that any public dataset contains these
columns). It is configurable via ``DatasetSchema.from_dict`` so a verified dataset
for another city can be mapped without code changes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    unit: str
    description: str
    kind: str = "feature"            # "core" (time/location) or "feature" (model input)
    required: bool = False           # must exist as a column in uploaded files
    hard_min: Optional[float] = None  # outside [hard_min, hard_max] => row rejected
    hard_max: Optional[float] = None
    soft_max: Optional[float] = None  # above => warning only
    window_hours: Optional[int] = None  # cumulative-rainfall window, enables consistency checks
    source_note: str = ""
    missing_policy: str = "Allowed. Reported; imputed with the training-set median inside the model pipeline (plus a missing-indicator). Never silently invented."
    aliases: tuple = ()


@dataclass(frozen=True)
class DatasetSchema:
    name: str
    version: str
    target: str
    features: tuple
    target_description: str = ""

    def spec(self, name: str) -> FeatureSpec:
        for f in self.features:
            if f.name == name:
                return f
        raise KeyError(name)

    @property
    def names(self) -> list[str]:
        return [f.name for f in self.features]

    @property
    def core_names(self) -> list[str]:
        return [f.name for f in self.features if f.kind == "core"]

    @property
    def feature_names(self) -> list[str]:
        return [f.name for f in self.features if f.kind == "feature"]

    def required_columns(self, mode: str) -> list[str]:
        cols = [f.name for f in self.features if f.required]
        if mode == "train":
            cols.append(self.target)
        return cols

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DatasetSchema":
        feats = tuple(
            FeatureSpec(**{**f, "aliases": tuple(f.get("aliases", ()))}) for f in d["features"]
        )
        return cls(name=d["name"], version=d["version"], target=d["target"], features=feats,
                   target_description=d.get("target_description", ""))


def normalize_name(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", str(name).strip().lower())


DEFAULT_SCHEMA = DatasetSchema(
    name="waterlogging-canonical",
    version="1.0",
    target="waterlogging_occurred",
    target_description=(
        "1 = waterlogging VERIFIED to have occurred at the location/time; 0 = VERIFIED absence "
        "for the observation period. Missing reports must NOT be encoded as 0."
    ),
    features=(
        FeatureSpec("timestamp", "ISO 8601 (YYYY-MM-DD[ HH:MM[:SS]][offset])",
                    "Reference time of the observation. Naive timestamps are interpreted in the "
                    "configured city timezone and stored as UTC. Day-first formats (dd/mm/yyyy) "
                    "are rejected to avoid silent day/month swaps.",
                    kind="core", required=True),
        FeatureSpec("latitude", "degrees (WGS84)", "Decimal latitude.", kind="core", required=True,
                    hard_min=-90, hard_max=90, aliases=("lat",)),
        FeatureSpec("longitude", "degrees (WGS84)", "Decimal longitude.", kind="core", required=True,
                    hard_min=-180, hard_max=180, aliases=("lon", "lng", "long")),
        FeatureSpec("rainfall_1h_mm", "mm", "Rainfall accumulated in the 1 h ending at `timestamp`.",
                    hard_min=0, soft_max=150, hard_max=400, window_hours=1,
                    source_note="Needs sub-daily observations; never derive from daily totals."),
        FeatureSpec("rainfall_24h_mm", "mm", "Rainfall accumulated in the 24 h ending at `timestamp`.",
                    hard_min=0, soft_max=600, hard_max=1500, window_hours=24),
        FeatureSpec("rainfall_72h_mm", "mm", "Rainfall accumulated in the 72 h ending at `timestamp`.",
                    hard_min=0, soft_max=1000, hard_max=3000, window_hours=72),
        FeatureSpec("elevation_m", "metres above mean sea level", "Ground elevation at the point.",
                    hard_min=-100, hard_max=9000, source_note="e.g. Open-Meteo Elevation API (DEM based)."),
        FeatureSpec("slope_deg", "degrees", "Local terrain slope.", hard_min=0, hard_max=90),
        FeatureSpec("drainage_capacity", "dataset-defined (document per dataset)",
                    "Drainage capacity/quality indicator. No public API is assumed to provide it.",
                    hard_min=0),
    ),
)
