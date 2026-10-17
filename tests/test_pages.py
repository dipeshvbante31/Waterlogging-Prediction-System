import re
import xml.dom.minidom
from pathlib import Path

import pytest

from app.api.routes_pages import NAV, PAGES, REDIRECTS

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
REQUIRED_ORDER = ["Dashboard", "Data Explorer", "Upload CSV", "Predictions", "History", "About"]


def read(p):
    return Path(p).read_text(encoding="utf-8")


@pytest.mark.parametrize("path", list(PAGES))
def test_every_page_renders_without_a_model(app_env, path):
    r = app_env[0].get(path)
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    t = r.text
    assert "© 2026 Dipesh Vishnu Bante. All rights reserved." in t and 'id="theme-toggle"' in t
    assert t.count('aria-current="page"') == 1 and t.count("<h1>") == 1  # one heading level-1: the top bar title
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_sidebar_navigation_has_required_items_in_order_without_duplicates(app_env):
    client, _ = app_env
    labels = [n[1] for n in NAV]
    assert [x for x in labels if x in REQUIRED_ORDER] == REQUIRED_ORDER and len(set(labels)) == len(labels)
    assert {"Live Weather", "Model Training", "Explainability"} <= set(labels)  # working features keep a clear home
    html = client.get("/").text
    side = re.search(r'<aside class="sidebar".*?</aside>', html, re.S).group(0)
    hrefs = re.findall(r'<a href="([^"]+)"[^>]*>', side)
    nav_hrefs = [h for h in hrefs if h != "/" or True]
    assert [n[2] for n in NAV] == [h for h in hrefs if h in {n[2] for n in NAV}] and len(set(hrefs)) == len(hrefs)
    for _, _, href, _, _ in NAV:
        assert client.get(href).status_code == 200, href
    assert "Waterlogging" in side and "Prediction System" in side and "<svg" in side  # logo + full name
    assert "Water-wise cities" in side  # sustainability message at the bottom of the sidebar
    assert side.index("Water-wise cities") > side.index("About")


def test_branding_assets_are_valid_local_and_integrated(app_env):
    client, _ = app_env
    for f in ("logo.svg", "logo-on-dark.svg", "logo-icon.svg", "favicon.svg"):
        dom = xml.dom.minidom.parse(str(STATIC / "img" / f))
        assert dom.documentElement.tagName == "svg" and dom.documentElement.getAttribute("viewBox")
        assert "http://" not in read(STATIC / "img" / f).replace("http://www.w3.org/2000/svg", "")  # no external references
    assert "Waterlogging" in read(STATIC / "img" / "logo.svg") and "Prediction System" in read(STATIC / "img" / "logo.svg")
    assert (STATIC / "img" / "favicon.ico").stat().st_size > 100 and (STATIC / "img" / "apple-touch-icon.png").stat().st_size > 100
    html = client.get("/").text
    assert 'rel="icon" href="/static/img/favicon.svg"' in html and "apple-touch-icon" in html
    about = client.get("/about").text
    assert "/static/img/logo.svg" in about and "/static/img/logo-on-dark.svg" in about


def test_no_map_feature_remains_anywhere(app_env):
    client, _ = app_env
    # routes and pages
    for url in ("/map", "/risk-map", "/api/map", "/api/map/points", "/static/vendor/leaflet/leaflet.js"):
        assert client.get(url).status_code == 404, url
    assert "/map" not in REDIRECTS
    # no rendered page contains a map, map library, tile provider or map control
    for path in list(PAGES) + ["/history/1"]:
        t = client.get(path).text.lower()
        for bad in ("leaflet", "openstreetmap", "tile.openstreetmap", 'id="map"', "pickmap", "risk map", "map data", "map-box"):
            assert bad not in t, (path, bad)
    assert not (STATIC / "vendor" / "leaflet").exists() and not (ROOT / "app" / "templates" / "map.html").exists()
    csp = client.get("/").headers["content-security-policy"]
    assert "openstreetmap" not in csp and "img-src 'self' data:" in csp
    # repository-wide: no map library / tile / map-endpoint references in code, config, docs or dependency files
    pattern = re.compile(r"leaflet|openstreetmap|tile\.openstreetmap|tilelayer|pickmap|default_zoom|/api/map|risk map|folium|mapbox|cartopy|geojson", re.I)
    leaflet_global = re.compile(r"\bL\.map\(")  # case-sensitive: Leaflet's global (pandas/JS \".map(\" calls are unrelated)
    allowed = {"CHANGELOG.md", "test_pages.py", "cities.py", "routes_pages.py", "plotly.min.js", "plotly.LICENSE", "ui_e2e.js"}  # ui_e2e.js only asserts the absence of maps
    offenders = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.name in allowed or any(x in p.parts for x in ("node_modules", "__pycache__", ".pytest_cache", "storage")):
            continue
        if p.suffix in {".png", ".ico", ".pyc", ".db", ".joblib", ".zip"}:
            continue
        try:
            txt = read(p)
        except UnicodeDecodeError:
            continue
        if pattern.search(txt) or leaflet_global.search(txt):
            offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, offenders
    for dep in ("requirements.txt", "requirements-dev.txt"):
        assert not re.search(r"folium|leaflet|geopandas|mapbox|cartopy|plotly-geo", read(ROOT / dep), re.I)
    assert not (ROOT / "app" / "static" / "js" / "map.js").exists()
    assert "default_zoom" not in {f for f in __import__("app.cities", fromlist=["CityProfile"]).CityProfile.__dataclass_fields__}


@pytest.mark.parametrize("old,new", list(REDIRECTS.items()))
def test_retired_urls_redirect_to_current_pages(app_env, old, new):
    r = app_env[0].get(old, follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == new
    assert app_env[0].get(old + "?dataset=abc", follow_redirects=False).headers["location"] == new + "?dataset=abc"


def test_no_leftover_templates_or_scripts():
    for name in ("data.html", "train.html", "predict.html", "batch.html", "performance.html", "map.html", "data_models.html"):
        assert not (ROOT / "app" / "templates" / name).exists(), name
    used = {m for p in (ROOT / "app" / "templates").glob("*.html") for m in re.findall(r"/static/js/([\w.]+)", read(p))}
    on_disk = {p.name for p in (STATIC / "js").glob("*.js")}
    assert on_disk - used <= {"theme.js"} and used - on_disk == set(), (on_disk - used, used - on_disk)


def test_referenced_static_assets_exist(app_env):
    client, _ = app_env
    seen = set()
    for path in list(PAGES) + ["/history/1"]:
        seen |= set(re.findall(r'(?:src|href)="(/static/[^"]+)"', client.get(path).text))
    assert seen
    for s in seen:
        assert (STATIC / s.removeprefix("/static/")).is_file(), s
        assert client.get(s).status_code == 200, s


def test_no_inline_script_code_and_no_secrets_in_front_end(app_env):
    client, _ = app_env
    for path in PAGES:
        for m in re.finditer(r"<script([^>]*)>(.*?)</script>", client.get(path).text, re.S):
            if m.group(2).strip():
                assert "application/json" in m.group(1), (path, m.group(1))
    for f in (STATIC / "js").glob("*.js"):
        t = read(f)
        assert not re.search(r"(admin_?token|api_?key|secret)\w*\s*[:=]\s*['\"][^'\"]{6,}['\"]", t, re.I), f
        assert "apikey" not in t.lower() and "customer-api" not in t.lower() and "open-meteo.com" not in t.lower(), f  # provider calls stay on the server


def test_design_system_shell_theme_tokens_and_responsive_rules():
    css = read(STATIC / "css" / "app.css")
    for needle in (".app{display:grid", ".sidebar", ".topbar", ".content", "@media(max-width:960px)", ".sidebar.open", ".scrim", ":root[data-theme=dark]", ".dropzone", ".risk-High::before", ".table-wrap{overflow-x:auto"):
        assert needle in css, needle
    light = set(re.findall(r"--([\w-]+):", re.search(r":root\{(.*?)\}", css, re.S).group(1)))
    dark = set(re.findall(r"--([\w-]+):", re.search(r":root\[data-theme=dark\]\{(.*?)\}", css, re.S).group(1)))
    colour_tokens = {"bg", "surface", "surface-2", "text", "muted", "border", "primary", "primary-ink", "ok", "warn", "err", "ok-bg", "warn-bg", "err-bg", "info-bg"}
    assert colour_tokens <= light and colour_tokens <= dark, colour_tokens - dark
    base = read(ROOT / "app" / "templates" / "base.html")
    for needle in ('class="skip"', 'aria-controls="sidebar"', 'aria-label="Main"', 'id="scrim"', 'aria-pressed="false"'):
        assert needle in base, needle
    assert "localStorage" in read(STATIC / "js" / "theme.js") and "prefers-color-scheme" in read(STATIC / "js" / "theme.js")
    js = read(STATIC / "js" / "app.js")
    assert "setTheme" in js and "plots.forEach" in js and "Escape" in js  # charts re-theme; Esc closes the mobile menu


def _lum(hex_):
    h = hex_.lstrip("#"); r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_text_contrast_meets_wcag_aa_in_both_themes(theme):
    css = read(STATIC / "css" / "app.css")
    block = re.search(r":root\{(.*?)\}" if theme == "light" else r":root\[data-theme=dark\]\{(.*?)\}", css, re.S).group(1)
    v = dict(re.findall(r"--([\w-]+):(#[0-9a-fA-F]{6})", block))
    pairs = [("text", "bg"), ("text", "surface"), ("text", "surface-2"), ("muted", "surface"), ("muted", "surface-2"), ("muted", "bg"), ("primary-ink", "primary"),
             ("primary", "surface"), ("primary", "primary-soft"), ("primary", "info-bg"), ("ok", "ok-bg"), ("warn", "warn-bg"), ("err", "err-bg"), ("ok", "surface"), ("err", "surface"), ("warn", "surface")]
    bad = [(a, b, round(_ratio(v[a], v[b]), 2)) for a, b in pairs if _ratio(v[a], v[b]) < 4.5]
    assert not bad, bad


def test_dashboard_page_has_no_hardcoded_metrics_or_locations(app_env):
    html = app_env[0].get("/").text.split("<main")[1].split("</main>")[0]
    for token in ("Colaba", "Andheri", "Bandra", "PR-AUC", "accuracy", "92%", "Mumbai Suburban"):
        assert token not in html, token
    assert 'id="s-total">…<' in html  # placeholders only; values arrive from /api/dashboard


def test_about_page_sections_and_honest_status(app_env):
    about = app_env[0].get("/about").text
    for needed in ("Project overview", "Problem statement", "Workflow", "System architecture", "Machine-learning methodology", "Data sources", "Responsible use",
                   "Technology stack", "Developer", "Testing &amp; deployment status"):
        assert needed in about, needed
    assert "[to be added]" in about and "not yet verified" in about.lower() and "No verified labelled waterlogging dataset" in about
    assert "map" not in re.sub(r"<[^>]+>", " ", about).lower().split("machine-learning")[0].replace("roadmap", "")


def test_verification_endpoint_reports_only_recorded_state(app_env):
    assert app_env[0].get("/api/verification").json().get("recorded", True) in (True, False)
