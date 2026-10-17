import pandas as pd
from app.services.data_validation import validate_dataframe
from app.services.schema import DEFAULT_SCHEMA as S


def base(**over):
    d = {"timestamp": ["2024-07-01 10:00", "2024-07-02 10:00", "2024-07-03 10:00"],
         "latitude": [19.0, 19.1, 19.2], "longitude": [72.8, 72.85, 72.9],
         "rainfall_1h_mm": [5, 10, 2], "rainfall_24h_mm": [50, 60, 20], "waterlogging_occurred": [0, 1, 0]}
    d.update(over)
    return pd.DataFrame(d)


def rv(df, mode="train", **kw):
    return validate_dataframe(df, S, mode, **kw)


def test_valid_train_ok(city):
    r = rv(base(), city=city)
    assert r.ok and r.n_rows_valid == 3 and r.class_balance["positive"] == 1
    assert str(r.clean["timestamp"].dtype) == "datetime64[ns, UTC]"


def test_missing_required_column():
    r = rv(base().drop(columns=["latitude"]))
    assert not r.ok and any("latitude" in e for e in r.fatal_errors)


def test_missing_target_only_for_train():
    df = base().drop(columns=["waterlogging_occurred"])
    assert not rv(df).ok
    assert rv(df, "predict").ok


def test_invalid_target_labels():
    r = rv(base(waterlogging_occurred=[0, 1, "maybe"]))
    assert r.rejection_counts["invalid_target"] == 1 and r.n_rows_valid == 2


def test_missing_report_not_negative():
    r = rv(base(waterlogging_occurred=[0, 1, None]))
    assert r.rejection_counts["missing:waterlogging_occurred"] == 1


def test_invalid_coordinates():
    r = rv(base(latitude=[19.0, 95.0, 19.2], longitude=[72.8, 72.8, 200]))
    assert r.rejection_counts["out_of_range:latitude"] == 1
    assert r.rejection_counts["out_of_range:longitude"] == 1


def test_non_numeric_and_negative_rain():
    r = rv(base(rainfall_1h_mm=["abc", -5, 2]))
    assert r.rejection_counts["non_numeric:rainfall_1h_mm"] == 1
    assert r.rejection_counts["out_of_range:rainfall_1h_mm"] == 1


def test_dayfirst_dates_rejected_not_swapped():
    r = rv(base(timestamp=["01/07/2024", "2024-07-02", "2024-07-03"]))
    assert r.rejection_counts["invalid_timestamp"] == 1


def test_naive_timestamp_is_city_timezone():
    r = rv(base())
    assert r.clean["timestamp"].iloc[0].isoformat() == "2024-07-01T04:30:00+00:00"  # 10:00 IST


def test_explicit_offset_respected():
    r = rv(base(timestamp=["2024-07-01T10:00:00+00:00", "2024-07-02", "2024-07-03"]))
    assert r.clean["timestamp"].iloc[0].isoformat() == "2024-07-01T10:00:00+00:00"


def test_duplicates_and_conflicts():
    df = pd.concat([base(), base().iloc[[0]], base().iloc[[1]].assign(waterlogging_occurred=0)], ignore_index=True)
    r = rv(df)
    assert r.rejection_counts["duplicate"] == 1
    assert r.rejection_counts["conflicting_label"] == 2  # both rows of the conflicting key


def test_inconsistent_rainfall_windows():
    r = rv(base(rainfall_1h_mm=[5, 100, 2]))  # 100 mm in 1h > 60 mm in 24h
    assert r.rejection_counts["inconsistent_rainfall_windows"] == 1


def test_single_class_fatal():
    r = rv(base(waterlogging_occurred=[0, 0, 0]))
    assert not r.ok and any("both classes" in e for e in r.fatal_errors)


def test_alias_and_explicit_mapping():
    df = base().rename(columns={"latitude": "Lat", "longitude": "LON", "rainfall_24h_mm": "rain24"})
    r = rv(df, mapping={"rain24": "rainfall_24h_mm"})
    assert r.ok and "rainfall_24h_mm" in r.clean.columns
    assert "rain24" in rv(df).ignored_columns


def test_bad_mapping_reported():
    r = rv(base(), mapping={"nope": "rainfall_24h_mm"})
    assert not r.ok


def test_unmapped_unrelated_column_never_guessed():
    df = base().rename(columns={"rainfall_24h_mm": "precip"})
    assert "rainfall_24h_mm" not in rv(df).clean.columns


def test_empty_and_too_many_rows():
    assert not rv(base().iloc[0:0]).ok
    assert not rv(base(), max_rows=2).ok


def test_out_of_region_warning(city):
    r = rv(base(latitude=[28.6, 19.1, 19.2], longitude=[77.2, 72.85, 72.9]), city=city)
    assert r.out_of_region_rows == 1


def test_missingness_reported():
    r = rv(base(rainfall_1h_mm=[5, None, 2]))
    assert r.missing_by_column["rainfall_1h_mm"]["count"] == 1


def test_rejected_report_has_reasons_and_row_ids():
    r = rv(base(waterlogging_occurred=[0, 1, "x"]))
    assert list(r.rejected["row_id"]) == [3] and "invalid_target" in r.rejected["rejection_reason"].iloc[0]
