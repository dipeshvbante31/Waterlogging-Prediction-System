"""SYNTHETIC TEST FIXTURES ONLY.

Everything generated here is randomly simulated to exercise code paths. It is NOT
real waterlogging data and metrics computed from it say nothing about real-world
performance.
"""
import numpy as np
import pandas as pd
import pytest

from app.cities import get_city
from app.services.feature_engineering import FeatureConfig
from app.services.schema import DEFAULT_SCHEMA
from app.services.training_service import TrainConfig
from app.storage import LocalArtifactStore


def make_synthetic(n=1500, seed=0, signal=True, n_days=300, n_cells=40, bias=-4):
    rng = np.random.RandomState(seed)
    city = get_city("mumbai")
    cells = rng.rand(n_cells, 2)
    ci = rng.randint(0, n_cells, n)
    lat = city.min_lat + 0.01 + cells[ci, 0] * (city.max_lat - city.min_lat - 0.02)
    lon = city.min_lon + 0.01 + cells[ci, 1] * (city.max_lon - city.min_lon - 0.02)
    day = rng.randint(0, n_days, n)
    ts = pd.Timestamp("2022-01-01") + pd.to_timedelta(day, unit="D") + pd.to_timedelta(rng.randint(0, 24, n), unit="h")
    r1 = rng.gamma(0.6, 6, n)
    r24 = r1 + rng.gamma(0.8, 25, n)
    r72 = r24 + rng.gamma(0.8, 30, n)
    elev = 2 + cells[ci, 0] * 40 + rng.randn(n)
    slope = np.abs(rng.randn(n)) * 2
    drain = rng.rand(n)
    z = bias + 0.05 * r1 + 0.02 * r24 - 0.08 * elev + (-1.0 * drain) if signal else np.full(n, -1.5)
    y = (rng.rand(n) < 1 / (1 + np.exp(-z))).astype(int)
    df = pd.DataFrame({"timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"), "latitude": lat, "longitude": lon,
                       "rainfall_1h_mm": r1, "rainfall_24h_mm": r24, "rainfall_72h_mm": r72,
                       "elevation_m": elev, "slope_deg": slope, "drainage_capacity": drain,
                       "waterlogging_occurred": y})
    return df


def small_cfg(**kw):
    base = dict(min_rows=100, min_pos_train=5, min_pos_val=3, min_pos_test=3, min_locations=3,
                n_permutation_repeats=3, label_provenance="SYNTHETIC TEST FIXTURE", provenance_attested=True)
    base.update(kw)
    return TrainConfig(**base)


@pytest.fixture(scope="session")
def synth():
    return make_synthetic()


@pytest.fixture
def city():
    return get_city("mumbai")


@pytest.fixture
def store(tmp_path):
    return LocalArtifactStore(tmp_path / "store")


# ----------------------------------------------------------------- API fixtures
from datetime import timedelta  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.container import Container  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
def app_env(tmp_path):
    s = Settings(database_url=f"sqlite:///{tmp_path/'t.db'}", storage_path=str(tmp_path / "store"),
                 rate_limit_per_min=0, admin_token="tok", app_env="test", max_upload_mb=2, _env_file=None)
    c = Container(s, run_jobs_inline=True)
    app = create_app(s, c)
    with TestClient(app) as client:
        yield client, c


@pytest.fixture
def admin():
    return {"X-Admin-Token": "tok"}


def csv_bytes(df):
    return df.to_csv(index=False).encode()


def api_df():
    """Larger synthetic fixture so the default (stricter) minimum-positives gates are satisfied."""
    return make_synthetic(n=3000, seed=11, bias=-2.6)
