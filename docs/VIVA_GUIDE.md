# Viva guide
* *What does it predict?* A model-estimated probability that waterlogging was recorded, learned from user-supplied verified labels — never a measurement.
* *Why not accuracy?* Rare events: a model predicting "never" is 99% accurate. PR-AUC vs prevalence baseline, recall, precision, calibration instead.
* *Leakage?* Chronological/location-grouped splits, preprocessing fit on train only, calibrator on validation only, test touched once, no target-derived features, no future weather.
* *Why PR-AUC to select?* Imbalanced, positive class matters; ROC-AUC can look good with many easy negatives.
* *Importance ≠ causation?* Permutation importance shows reliance of this model; correlated features share credit.
* *Why no accuracy numbers today?* No verified labelled data exists in the repository; inventing it would be fabrication.
* *Live weather limits?* Model-derived precipitation, different source than training, no slope/drainage → live prediction refused when inputs are missing.
* *Algorithms:* Logistic regression (interpretable baseline), Random Forest (non-linear, robust), HistGradientBoosting (strong tabular, handles missing).
