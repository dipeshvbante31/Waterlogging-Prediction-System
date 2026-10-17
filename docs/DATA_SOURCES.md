# Data sources — verification status

**Build-sandbox limitation:** during development, outbound requests to `api.open-meteo.com`, `imdpune.gov.in`,
`indiawris.gov.in` and `nwdp.nwic.gov.in` all failed (connection could not be established; HTTP code 000).
**No dataset was downloaded, inspected or used.** Every entry below is a *lead*, with details written from general
knowledge and therefore **UNVERIFIED**. Verify each item yourself before relying on it. Verification status values:
`NOT VERIFIED` (no access), `VERIFIED` (inspected the real file/response — none yet).

## Waterlogging labels (the critical gap)
| Item | Status |
|---|---|
| A public dataset of verified street-level waterlogging events *and verified absences* for Mumbai | **Not found / not verified.** None is bundled. |
| Municipal (e.g. BMC) lists of chronic flooding spots, complaint logs, published research datasets | Leads only; **NOT VERIFIED**. A spot list gives locations, not dated event labels or negatives. |
| News reports | Can help identify events; incomplete and biased; must not be treated as a complete label set. |

Consequence: supervised training is **blocked until you supply labelled records**. The app validates, trains and
predicts the moment a compatible CSV exists, and marks any resulting model *experimental* unless every validation
gate passes. Label rule required by this project: `1` = verified occurrence; `0` = *verified absence* — never "no report".

## Historical rainfall
| Source | Details (unverified) | Status |
|---|---|---|
| IMD gridded daily rainfall (imdpune.gov.in) | Daily, ~0.25° grid, long record. **Daily** only — cannot supply hourly accumulation; licence/redistribution terms to be checked. | NOT VERIFIED |
| India-WRIS / NWDP (indiawris.gov.in, nwdp.nwic.gov.in) | Water-resources portals that may expose station/telemetry rainfall; formats, licence and access method unknown. | NOT VERIFIED |
| Open-Meteo Historical Weather API | Reanalysis-based hourly precipitation; free tier is for non-commercial use with attribution (check current terms). Reanalysis ≠ gauge observation. | NOT VERIFIED |

Manual import path: download rainfall yourself, join it to your labelled incidents (same location/time basis), compute
1/24/72-hour accumulations **from sub-daily data only**, and upload the canonical CSV (see `DATA_DICTIONARY.md`).
Never derive hourly rainfall by dividing daily totals.

## Live weather (inputs only, never labels)
Open-Meteo provides **gridded model output (analysis, reanalysis, forecast), not station observations.** The app labels every value:
`current_model_analysis`, `recent_model_analysis` (past hours from the forecast API's `past_days`), `historical_reanalysis`
(Historical Weather API, used for timestamps older than ~85 days), `forecast`, or `unavailable`. A 1/24/72 h window is a number only if
every hour in it is present and valid; otherwise it is `unavailable` — never 0.

| Endpoint (configurable) | Use | Status |
|---|---|---|
| `https://api.open-meteo.com/v1/forecast` | current, `past_days` hourly precipitation, forecast; `timezone=UTC` | **live call NOT TESTED** (unreachable from build sandbox). Parameters/units written from Open-Meteo's public docs as understood; unit (`mm`) and UTC offset are asserted at runtime. |
| `https://archive-api.open-meteo.com/v1/archive` | reanalysis for old timestamps | NOT TESTED live |
| `https://api.open-meteo.com/v1/elevation` | elevation_m (DEM) | NOT TESTED live |
| `https://geocoding-api.open-meteo.com/v1/search` | place search (`countryCode` filter) | NOT TESTED live |

What *was* verified: service logic against mocked responses (unit tests) and against a **local mock server over real HTTP sockets** (UI end-to-end
run). Neither proves the provider's current behaviour. Before relying on it: call each endpoint once from a networked machine and confirm field names,
units and timestamps; read Open-Meteo's current terms (free tier is for non-commercial use with attribution) and attribution requirements.
Credentials: none needed for the free endpoints. `OPEN_METEO_API_KEY` is sent only to `customer-api.open-meteo.com` hosts and never logged.
Slope is *derived* from five elevation samples (coarse; ~90 m DEM) and labelled as such; drainage capacity is not available from any public provider and must be user-supplied or excluded when training.


## Terrain / drainage
Elevation can come from the provider's DEM-based elevation endpoint. Slope can be derived from a DEM you download
(e.g. SRTM/Copernicus — verify licence). Drainage capacity needs municipal data; no public API is assumed.
