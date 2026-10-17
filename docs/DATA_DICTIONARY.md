# Data dictionary (canonical schema v1.0 — a *proposal*, configurable in `app/services/schema.py`)

| Column | Required | Unit | Meaning / rule |
|---|---|---|---|
| timestamp | yes | ISO 8601 | Reference time. Naive → city timezone (Asia/Kolkata for Mumbai), stored UTC. dd/mm/yyyy is **rejected**. |
| latitude | yes | deg WGS84 | −90…90. Alias: `lat` |
| longitude | yes | deg WGS84 | −180…180. Aliases: `lon`, `lng`, `long` |
| rainfall_1h_mm | optional | mm | Rain in the 1 h ending at timestamp. 0–400 (warn >150). Needs sub-daily data. |
| rainfall_24h_mm | optional | mm | 24 h accumulation. 0–1500 (warn >600). |
| rainfall_72h_mm | optional | mm | 72 h accumulation. 0–3000 (warn >1000). |
| elevation_m | optional | m a.m.s.l. | −100…9000. |
| slope_deg | optional | degrees | 0…90. |
| drainage_capacity | optional | dataset-defined | ≥ 0; document its meaning for your dataset. |
| waterlogging_occurred | training only | 0/1 (true/false) | 1 = verified occurrence; 0 = verified absence. Missing reports ≠ 0. |

Row rules: cumulative windows must be non-decreasing (1h ≤ 24h ≤ 72h); exact duplicates rejected; same
timestamp+location with conflicting labels → all rows rejected; non-numeric/out-of-range/missing-required rejected with a
reason in the downloadable rejected-rows file. Missing optional values are allowed, reported, and (at modelling time only)
imputed by the training-set median with a missing-indicator; prediction output lists `imputed_inputs`.

Derived features (computed identically at train and predict time): `rain_prior_23h_mm = rain24 − rain1`,
`rain_prior_48h_mm = rain72 − rain24` (clipped at 0). Coordinates are **not** model inputs by default (avoids memorising
locations); seasonal features are off by default.

Other cities: add a profile JSON (`CITY_CONFIG_PATH`) and upload compatible CSVs; the schema itself can be replaced via
`DatasetSchema.from_dict`.
