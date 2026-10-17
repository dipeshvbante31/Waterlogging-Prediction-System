from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.deps import get_c
from app.container import Container

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
router = APIRouter()

# (key, label, href, icon, sidebar group)
NAV = [("dashboard", "Dashboard", "/", "dashboard", "Overview"),
       ("explorer", "Data Explorer", "/data-explorer", "table", "Data"), ("upload", "Upload CSV", "/upload", "upload", "Data"),
       ("predictions", "Predictions", "/predictions", "predict", "Analysis"), ("weather", "Live Weather", "/weather", "cloud", "Analysis"),
       ("training", "Model Training", "/model-training", "model", "Model"), ("explain", "Explainability", "/explainability", "bulb", "Model"),
       ("history", "History", "/history", "clock", "Records"), ("about", "About", "/about", "info", "Records")]

PAGES = {  # route -> (template, nav key, title)
    "/": ("dashboard.html", "dashboard", "Dashboard"), "/data-explorer": ("data_explorer.html", "explorer", "Data Explorer"),
    "/upload": ("upload.html", "upload", "Upload CSV"), "/predictions": ("predictions.html", "predictions", "Predictions"),
    "/weather": ("weather.html", "weather", "Live Weather"), "/model-training": ("model_training.html", "training", "Model Training"),
    "/explainability": ("explain.html", "explain", "Explainability"), "/history": ("history.html", "history", "History"),
    "/about": ("about.html", "about", "About"),
}
# Retired URLs of merged/renamed pages keep working. The Risk Map and every other map feature were REMOVED and are not redirected.
REDIRECTS = {"/data": "/data-explorer", "/data-models": "/data-explorer", "/train": "/model-training", "/performance": "/model-training",
             "/predict": "/predictions", "/batch": "/predictions"}


def _make(tpl, key, title):
    def view(request: Request, c: Container = Depends(get_c)):
        return TEMPLATES.TemplateResponse(request, tpl, {"nav": NAV, "active": key, "title": title, "city": c.city,
                                                         "settings": c.settings})
    return view


for path, (tpl, key, title) in PAGES.items():
    router.add_api_route(path, _make(tpl, key, title), methods=["GET"], include_in_schema=False, name=f"page_{key}")


def _redirect(target):
    def go(request: Request):
        qs = request.url.query
        return RedirectResponse(target + (f"?{qs}" if qs else ""), status_code=308)
    return go


for old, new in REDIRECTS.items():
    router.add_api_route(old, _redirect(new), methods=["GET"], include_in_schema=False, name=f"redirect_{old.strip('/')}")


@router.get("/history/{record_id}", include_in_schema=False, name="page_history_detail")
def history_detail_page(record_id: int, request: Request, c: Container = Depends(get_c)):
    return TEMPLATES.TemplateResponse(request, "prediction_detail.html", {"nav": NAV, "active": "history", "title": "Prediction details",
                                                                          "city": c.city, "settings": c.settings, "record_id": record_id})
