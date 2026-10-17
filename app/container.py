"""Application container: one place that wires settings, DB, storage, registry, executors."""
from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from app.cities import CityProfile, get_city
from app.config import Settings
from app.database.db import make_engine, make_session_factory
from app.database.models import Base
from app.services.model_registry import ModelRegistry
from app.services.weather_service import WeatherService
from app.storage import ArtifactStore, get_store


class Container:
    def __init__(self, settings: Settings, store: ArtifactStore | None = None, run_jobs_inline: bool = False):
        self.settings = settings
        self.engine = make_engine(settings.database_url)
        self.Session = make_session_factory(self.engine)
        self.store = store or get_store(settings)
        self.registry = ModelRegistry(self.store)
        self.city: CityProfile = get_city(settings.city, settings.city_config_path)
        if settings.city_bbox:
            a, b, c_, d = (float(x) for x in settings.city_bbox.split(","))
            if not (-90 <= a < c_ <= 90 and -180 <= b < d <= 180):
                raise ValueError("CITY_BBOX must be min_lat,min_lon,max_lat,max_lon with min < max.")
            self.city = replace(self.city, min_lat=a, min_lon=b, max_lat=c_, max_lon=d)
        self.run_jobs_inline = run_jobs_inline
        self.weather = WeatherService(settings)
        self.train_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="train")
        self.batch_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="batch")

    def init_schema(self):
        """create_all is for SQLite/dev/tests. Production uses `alembic upgrade head`."""
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self):
        s = self.Session()
        try:
            yield s
            s.commit()
        except BaseException:
            s.rollback()
            raise
        finally:
            s.close()

    def shutdown(self):
        self.train_pool.shutdown(wait=False, cancel_futures=True)
        self.batch_pool.shutdown(wait=False, cancel_futures=True)
        self.engine.dispose()
