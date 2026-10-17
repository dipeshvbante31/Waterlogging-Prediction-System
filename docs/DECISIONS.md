# Decisions (Tree-of-Thoughts summaries)
| Decision | Alternatives considered | Chosen | Why |
|---|---|---|---|
| Evaluation split | random stratified; chronological; location-grouped; both | chronological default, spatial optional, random never "validated" | correlated events leak under random splits |
| Job execution | Celery+Redis; thread pool + DB status; sync | thread pool + persisted status + atomic claim | no extra infrastructure/cost; honest limits documented |
| Storage | raw filesystem calls; DB blobs; storage interface | interface + local backend | survives swapping to S3-compatible without touching callers |
| Frontend libs | CDN; vendored | vendored (npm) | strict CSP, no runtime third-party script dependency |
| Calibration | none; isotonic; sigmoid on validation | sigmoid, validation only | small positive counts; keeps test untouched |
| Timestamps | permissive parsing; ISO-only | ISO-only | dd/mm vs mm/dd silent swaps corrupt time splits |
| Gunicorn worker | uvicorn.workers (deprecated); uvicorn-worker | uvicorn-worker | supported package |
