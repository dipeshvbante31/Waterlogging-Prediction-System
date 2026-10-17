"""Versioned model storage on top of the ArtifactStore. Integrity-checked loading."""
from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass

import joblib
import pandas as pd

from app.services.training_service import TrainingResult
from app.storage import ArtifactStore, StorageError

_RANK = {"experimental": 1, "validated": 2}


class RegistryError(Exception):
    pass


@dataclass
class ModelBundle:
    version: str
    model: object
    metadata: dict

    @property
    def status(self) -> str:
        return self.metadata["status"]


class ModelRegistry:
    def __init__(self, store: ArtifactStore):
        self.store = store

    # keys are generated here, never taken from user input
    @staticmethod
    def _k(version, name): return f"models/{version}/{name}"

    def active_version(self) -> str | None:
        try:
            return json.loads(self.store.get_bytes("models/ACTIVE.json"))["version"]
        except (StorageError, KeyError, ValueError):
            return None

    def save(self, result: TrainingResult, activate: str = "auto") -> str:
        if result.model is None:
            raise RegistryError("There is no model to save (no eligible model).")
        md = dict(result.metadata)
        stamp = pd.Timestamp.now(tz="UTC").strftime("%Y%m%dT%H%M%S%fZ")
        version = f"v{stamp}-{md['data']['sha256'][:8]}"
        buf = io.BytesIO(); joblib.dump(result.model, buf)
        blob = buf.getvalue()
        md.update(version=version, artifact_sha256=hashlib.sha256(blob).hexdigest())
        self.store.put_bytes(self._k(version, "model.joblib"), blob)
        self.store.put_bytes(self._k(version, "metadata.json"), json.dumps(md, indent=2).encode())
        # Activation policy: only VALIDATED models can become active, and a new validated model never silently
        # replaces an existing active one (explicit activation through the registry is required).
        if activate in ("auto", "always") and md["status"] == "validated" and (activate == "always" or self.active_version() is None):
            self.set_active(version)
        return version

    def get_metadata(self, version: str) -> dict:
        try:
            return json.loads(self.store.get_bytes(self._k(version, "metadata.json")))
        except StorageError as e:
            raise RegistryError(f"Unknown model version '{version}'.") from e

    def list_versions(self) -> list[dict]:
        out = []
        for k in self.store.list("models/"):
            if k.endswith("/metadata.json"):
                md = json.loads(self.store.get_bytes(k))
                tm = md.get("test_metrics") or {}
                out.append({"version": md["version"], "status": md["status"], "created_at": md["created_at"],
                            "selected_model": md["selected_model"], "active": md["version"] == self.active_version(),
                            "dataset_sha256": md["data"]["sha256"], "n_rows": md["data"]["n_rows"],
                            "split_strategy": md["split"]["strategy"], "test_pr_auc": tm.get("pr_auc"),
                            "test_recall": tm.get("recall"), "test_precision": tm.get("precision"),
                            "failed_gates": [g["name"] for g in md.get("gates", []) if not g["passed"]]})
        return sorted(out, key=lambda d: d["created_at"], reverse=True)

    def set_active(self, version: str) -> None:
        md = self.get_metadata(version)  # must exist
        if md["status"] != "validated":
            raise RegistryError("Only validated models can be activated; this version did not pass all validation gates.")
        self.store.put_bytes("models/ACTIVE.json", json.dumps({"version": version}).encode())

    def load(self, version: str | None = None) -> ModelBundle | None:
        version = version or self.active_version()
        if version is None:
            return None
        md = self.get_metadata(version)
        blob = self.store.get_bytes(self._k(version, "model.joblib"))
        if hashlib.sha256(blob).hexdigest() != md["artifact_sha256"]:
            raise RegistryError("Model artifact failed its integrity check; refusing to load.")
        return ModelBundle(version, joblib.load(io.BytesIO(blob)), md)
