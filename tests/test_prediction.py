import json
import pandas as pd
import pytest

from app.services.data_validation import validate_dataframe
from app.services.model_registry import ModelRegistry, RegistryError
from app.services.prediction_service import (IncompatibleInputError, NoModelError, predict_dataframe,
                                              predict_records)
from app.services.schema import DEFAULT_SCHEMA as S
from app.services.training_service import train_models
from app.storage import LocalArtifactStore, StorageError
from app.utils.csv_safety import to_safe_csv_bytes
from tests.conftest import make_synthetic, small_cfg


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    from app.cities import get_city
    city = get_city("mumbai")
    df = make_synthetic()
    clean = validate_dataframe(df, S, "train", city=city).clean
    res = train_models(clean, S, small_cfg(), city)
    reg = ModelRegistry(LocalArtifactStore(tmp_path_factory.mktemp("s")))
    v = reg.save(res)
    return reg, v, res, df


def test_no_model_never_fakes_prediction(synth):
    with pytest.raises(NoModelError):
        predict_dataframe(synth.drop(columns=["waterlogging_occurred"]), None)


def test_registry_roundtrip_and_active(trained):
    reg, v, res, _ = trained
    assert reg.active_version() == v
    b = reg.load()
    assert b.version == v and b.metadata["feature_columns"] == res.metadata["feature_columns"]
    assert reg.list_versions()[0]["active"] is True


def test_artifact_tamper_detected(trained):
    reg, v, _, _ = trained
    blob = reg.store.get_bytes(f"models/{v}/model.joblib")
    reg.store.put_bytes(f"models/{v}/model.joblib", blob + b"x")
    with pytest.raises(RegistryError, match="integrity"):
        reg.load(v)
    reg.store.put_bytes(f"models/{v}/model.joblib", blob)
    assert reg.load(v)


def test_batch_prediction_end_to_end(trained, city):
    reg, v, _, df = trained
    new = df.drop(columns=["waterlogging_occurred"]).head(50).copy()
    new.loc[3, "rainfall_1h_mm"] = -5  # rejected row
    out = predict_dataframe(new, reg.load(), city=city)
    r = out.results
    assert len(r) == 49 and len(out.rejected) == 1
    assert r["probability"].between(0, 1).all() and set(r["predicted_class"]) <= {0, 1}
    assert {"row_id", "model_version", "model_status", "prediction_timestamp", "risk_level"} <= set(r.columns)
    assert list(out.rejected["row_id"]) == [4]
    # exported rows match generated predictions
    back = pd.read_csv(pd.io.common.BytesIO(to_safe_csv_bytes(r)))
    assert back["probability"].round(6).tolist() == r["probability"].round(6).tolist()
    assert back["row_id"].tolist() == r["row_id"].tolist()


def test_risk_pending_when_not_validated(trained, city):
    reg, v, res, df = trained
    out = predict_dataframe(df.drop(columns=["waterlogging_occurred"]).head(5), reg.load(), city=city)
    if res.status != "validated":
        assert set(out.results["risk_level"]) == {"pending_validation"}
    else:
        assert set(out.results["risk_level"]) <= {"Low", "Medium", "High", "pending_validation"}


def test_incompatible_columns_refused(trained, city):
    reg, *_ = trained
    bad = pd.DataFrame({"timestamp": ["2024-07-01"], "latitude": [19.0], "longitude": [72.8], "rainfall_24h_mm": [50]})
    with pytest.raises(IncompatibleInputError, match="lacks columns"):
        predict_dataframe(bad, reg.load(), city=city)


def test_single_record_and_target_ignored(trained, city):
    reg, v, _, df = trained
    rec = df.drop(columns=["waterlogging_occurred"]).iloc[0].to_dict()
    rec["waterlogging_occurred"] = 1  # must be ignored at prediction time
    out = predict_records([rec], reg.load(), city=city)
    assert len(out.results) == 1 and any("Target column" in w for w in out.report.warnings)


def test_missing_optional_value_is_reported_not_hidden(trained, city):
    reg, *_ = trained
    rec = trained[3].drop(columns=["waterlogging_occurred"]).head(3).copy()
    rec.loc[0, "slope_deg"] = None
    out = predict_dataframe(rec, reg.load(), city=city)
    assert out.results.loc[0, "imputed_inputs"] == "slope_deg"


def test_out_of_region_flag(trained, city):
    reg, *_ = trained
    rec = trained[3].drop(columns=["waterlogging_occurred"]).head(2).copy()
    rec.loc[0, ["latitude", "longitude"]] = [28.6, 77.2]
    out = predict_dataframe(rec, reg.load(), city=city)
    assert out.results["out_of_region"].tolist() == [True, False]


def test_no_model_when_no_eligible_model_cannot_be_saved(tmp_path):
    from app.services.training_service import TrainingResult
    reg = ModelRegistry(LocalArtifactStore(tmp_path))
    with pytest.raises(RegistryError):
        reg.save(TrainingResult("no_eligible_model", "x", {}, None))


def test_validated_model_not_replaced_by_experimental(tmp_path, city):
    df = make_synthetic()
    clean = validate_dataframe(df, S, "train", city=city).clean
    good = train_models(clean, S, small_cfg(), city)
    weak = train_models(clean, S, small_cfg(provenance_attested=False), city)
    reg = ModelRegistry(LocalArtifactStore(tmp_path))
    v1 = reg.save(good)
    reg.save(weak)
    if good.status == "validated":
        assert reg.active_version() == v1


def test_storage_blocks_path_traversal(tmp_path):
    st = LocalArtifactStore(tmp_path)
    for k in ("../x", "/etc/passwd", "a/../../b", "a b", ""):
        with pytest.raises(StorageError):
            st.put_bytes(k, b"x")
    st.put_bytes("ok/file.bin", b"1")
    assert st.get_bytes("ok/file.bin") == b"1" and st.list("ok/") == ["ok/file.bin"]


def test_csv_formula_injection_neutralised():
    df = pd.DataFrame({"note": ["=SUM(A1)", "+1", "-1+2", "@x", "safe"], "num": [-1, 2, 3, 4, 5]})
    out = pd.read_csv(pd.io.common.BytesIO(to_safe_csv_bytes(df)))
    assert out["note"].tolist() == ["'=SUM(A1)", "'+1", "'-1+2", "'@x", "safe"]
    assert out["num"].tolist() == [-1, 2, 3, 4, 5]


def test_csv_header_cells_neutralised_too():
    out = to_safe_csv_bytes(pd.DataFrame({"=cmd()": [1]})).decode().splitlines()[0]
    assert out.startswith("'=")


def test_non_default_index_keeps_row_ids_stable():
    df = make_synthetic(n=20, seed=9).iloc[::-1]  # reversed, non-range index
    rep = validate_dataframe(df.drop(columns=["waterlogging_occurred"]), S, "predict")
    assert sorted(rep.clean["row_id"]) == list(range(1, 21))
