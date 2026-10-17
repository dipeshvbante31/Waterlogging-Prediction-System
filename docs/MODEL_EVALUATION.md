# Model evaluation

**No real evaluation has been performed** because no verified labelled dataset exists. Any numbers produced by the test
suite come from clearly labelled *synthetic* fixtures that only exercise code paths; they say nothing about waterlogging.

Method (implemented, tested on synthetic data): chronological split by calendar day (or location-grouped ~1 km cells;
random split allowed but can never be "validated") → preprocessing fit on train only → LogisticRegression / RandomForest /
HistGradientBoosting compared on validation PR-AUC vs a prevalence baseline → best eligible model chosen (ties → simpler) →
sigmoid calibration fitted on validation only → decision threshold maximises validation F1 → single evaluation on the test
split (precision, recall, F1, PR-AUC, ROC-AUC, confusion matrix, Brier, calibration bins) → permutation importance on test.

Validation gates for `validated`: labels attested + provenance text; time/space-aware split; ≥ N distinct location cells;
test PR-AUC ≥ prevalence + margin; sufficient positives per split. **The provenance attestation is a user claim the software
cannot verify** — "validated" means "passed these gates on the supplied data", nothing more. Risk categories (Low/Medium/High)
are statistical operating points derived from validation recall/precision, shown only for validated models.
