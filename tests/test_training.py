import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import average_precision_score

from app.services.data_validation import validate_dataframe
from app.services.feature_engineering import FeatureConfig, engineer_features, final_feature_columns
from app.services.schema import DEFAULT_SCHEMA as S
from app.services.training_service import TrainingError, train_models, _cells
from tests.conftest import make_synthetic, small_cfg


def clean_of(df, city):
    r = validate_dataframe(df, S, "train", city=city)
    assert r.ok, r.fatal_errors
    return r.clean


def test_training_success_chronological(synth, city):
    res = train_models(clean_of(synth, city), S, small_cfg(), city)
    md = res.metadata
    assert res.model is not None and res.status in ("validated", "experimental")
    tm = md["test_metrics"]
    assert 0 <= tm["pr_auc"] <= 1 and 0 <= tm["roc_auc"] <= 1
    cm = tm["confusion_matrix"]
    assert cm["tn"] + cm["fp"] + cm["fn"] + cm["tp"] == tm["n"]
    assert md["permutation_importance"] and md["selected_model"] in md["config"]["models"]
    assert set(md["split"]) >= {"train", "validation", "test"}


def test_chronological_split_has_no_overlap_and_is_ordered(synth, city):
    c = clean_of(synth, city)
    res = train_models(c, S, small_cfg(), city)
    s = res.splits
    assert not set(s["train"]) & set(s["validation"]) and not set(s["train"]) & set(s["test"]) \
        and not set(s["validation"]) & set(s["test"])
    ts = c.set_index("row_id")["timestamp"]
    assert ts.loc[s["train"]].max() < ts.loc[s["validation"]].min()
    assert ts.loc[s["validation"]].max() < ts.loc[s["test"]].min()
    # same calendar day never straddles two splits
    days = lambda ids: set(ts.loc[ids].dt.strftime("%Y-%m-%d"))
    assert not days(s["train"]) & days(s["test"])


def test_spatial_split_has_no_shared_cells(synth, city):
    cfg = small_cfg(split_strategy="spatial_group")
    c = clean_of(synth, city)
    res = train_models(c, S, cfg, city)
    cells = _cells(c, cfg).set_axis(c["row_id"])
    a, b, d = (set(cells.loc[res.splits[k]]) for k in ("train", "validation", "test"))
    assert not a & b and not a & d and not b & d


def test_preprocessing_fitted_on_train_only(synth, city):
    c = clean_of(synth, city)
    res = train_models(c, S, small_cfg(models=("logistic_regression",)), city)
    base = res.model.estimator.estimator if hasattr(res.model.estimator, "estimator") else res.model.estimator
    pipe = base
    scaler = pipe.named_steps["prep"].named_transformers_["num"].named_steps["scale"]
    feat = res.metadata["feature_columns"]
    tr = engineer_features(c, FeatureConfig()).set_index("row_id").loc[res.splits["train"], feat]
    np.testing.assert_allclose(scaler.mean_[: len(feat)], tr.mean().values, rtol=1e-6)


def test_reported_test_metric_matches_independent_recalculation(synth, city):
    res = train_models(clean_of(synth, city), S, small_cfg(), city)
    tp = res.test_predictions
    assert res.metadata["test_metrics"]["pr_auc"] == pytest.approx(average_precision_score(tp.y_true, tp.proba))


def test_insufficient_rows(synth, city):
    small = make_synthetic(n=60, seed=5)
    small.loc[small.index[:5], "waterlogging_occurred"] = 1  # ensure both classes so only row count fails
    small.loc[small.index[5:10], "waterlogging_occurred"] = 0
    c = clean_of(small, city)
    with pytest.raises(TrainingError, match="Insufficient data"):
        train_models(c, S, small_cfg(), city)


def test_single_class_raises(synth, city):
    df = synth.copy(); 
    c = clean_of(df, city).copy(); c["waterlogging_occurred"] = 0
    with pytest.raises(TrainingError, match="both classes"):
        train_models(c, S, small_cfg(), city)


def test_too_few_positives_per_split(city):
    df = make_synthetic(n=400, seed=1)
    df["waterlogging_occurred"] = 0
    df.loc[df.index[:3], "waterlogging_occurred"] = 1
    with pytest.raises(TrainingError, match="Insufficient data in"):
        train_models(clean_of(df, city), S, small_cfg(), city)


def test_not_validated_without_provenance_attestation(synth, city):
    res = train_models(clean_of(synth, city), S, small_cfg(provenance_attested=False, label_provenance=""), city)
    assert res.status == "experimental"
    assert not next(g for g in res.metadata["gates"] if g["name"].startswith("labels_attested"))["passed"]


def test_random_split_can_never_be_validated(synth, city):
    res = train_models(clean_of(synth, city), S, small_cfg(split_strategy="random_stratified"), city)
    assert res.status == "experimental" and any("optimistic" in c for c in res.metadata["caveats"])


def test_unknown_strategy(synth, city):
    with pytest.raises(TrainingError):
        train_models(clean_of(synth, city), S, small_cfg(split_strategy="nope"), city)


def test_antecedent_features_units_and_missing():
    df = pd.DataFrame({"rainfall_1h_mm": [5.0, np.nan, 2.0], "rainfall_24h_mm": [50.0, 60.0, 1.0],
                       "rainfall_72h_mm": [80.0, 70.0, 1.0]})
    out = engineer_features(df, FeatureConfig())
    assert out["rain_prior_23h_mm"].tolist()[0] == 45.0 and np.isnan(out["rain_prior_23h_mm"][1])
    assert out["rain_prior_48h_mm"].tolist() == [30.0, 10.0, 0.0]  # boundary: equal windows -> 0
    assert "rain_prior_23h_mm" in final_feature_columns(["rainfall_1h_mm", "rainfall_24h_mm"], FeatureConfig())
    assert "rain_prior_48h_mm" not in final_feature_columns(["rainfall_1h_mm", "rainfall_24h_mm"], FeatureConfig())


def test_noise_labels_do_not_produce_validated_model(city):
    df = make_synthetic(n=1500, seed=3, signal=False)
    res = train_models(clean_of(df, city), S, small_cfg(), city)
    assert res.status != "validated"
