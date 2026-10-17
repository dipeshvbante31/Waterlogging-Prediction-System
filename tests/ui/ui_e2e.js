/* Browser-like end-to-end test of the REAL page scripts using jsdom against a RUNNING server.
   Plotly is stubbed (jsdom cannot render charts) and XMLHttpRequest is shimmed onto Node fetch for uploads (progress events are simulated);
   everything else (fetch, DOM, application JavaScript, server, database, ML) is real. Weather comes from a LOCAL MOCK provider.
   Usage: BASE=http://127.0.0.1:8800 ADMIN=token TRAIN_CSV=... PRED_CSV=... MOCK=http://127.0.0.1:9100 node ui_e2e.js [main|failures] */
const { JSDOM, ResourceLoader, VirtualConsole } = require(process.env.JSDOM_PATH || 'jsdom');
const fs = require('fs');
const BASE = process.env.BASE, ADMIN = process.env.ADMIN, MOCK = process.env.MOCK, PHASE = process.argv[2] || 'main';
const results = [], errors = [];
process.on('uncaughtException', e => errors.push('uncaught: ' + (e && e.message)));
const ok = (name, cond, detail) => results.push({ name, pass: !!cond, detail: cond ? '' : (detail || '') });
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function waitFor(fn, ms = 20000) { const t0 = Date.now(); for (;;) { try { const v = fn(); if (v) return v; } catch (e) {} if (Date.now() - t0 > ms) return null; await sleep(80); } }
class Loader extends ResourceLoader { fetch(url, o) { return url.includes('/static/vendor/') ? Promise.resolve(Buffer.from('')) : super.fetch(url, o); } }

async function load(path) {
  const vc = new VirtualConsole(); vc.on('jsdomError', e => errors.push(path + ': ' + e.message)); vc.on('error', e => errors.push(path + ' console.error: ' + e));
  const dom = await JSDOM.fromURL(BASE + path, { runScripts: 'dangerously', resources: new Loader(), pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.fetch = (u, o) => fetch(new URL(u, BASE).toString(), o); w.FormData = FormData; w.confirm = () => true;
      w.Plotly = { react: el => { el.dataset.plotted = '1'; } };
      w.XMLHttpRequest = class { constructor() { this.upload = {}; } open(m, u) { this.m = m; this.u = u; }
        send(fd) { fetch(new URL(this.u, BASE), { method: this.m, body: fd }).then(async r => { this.status = r.status; this.response = await r.json(); if (this.upload.onprogress) this.upload.onprogress({ lengthComputable: true, loaded: 1, total: 1 }); this.onload(); }).catch(() => this.onerror && this.onerror()); } };
    } });
  const w = dom.window; await new Promise(r => (w.document.readyState === 'complete' ? r() : w.addEventListener('load', r))); return w;
}
const $ = (w, s) => w.document.querySelector(s), $$ = (w, s) => [...w.document.querySelectorAll(s)];
const txt = (w, s) => ($(w, s) ? $(w, s).textContent : '');
const click = (w, s) => $(w, s).dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
function setVal(w, s, v, ev = 'input') { const e = $(w, s); e.value = v; e.dispatchEvent(new w.Event(ev, { bubbles: true })); }
function setFile(w, s, file) { Object.defineProperty($(w, s), 'files', { value: [file], configurable: true }); $(w, s).dispatchEvent(new w.Event('change', { bubbles: true })); }
const csvFile = (path, name) => new File([fs.readFileSync(path)], name, { type: 'text/csv' });
const api = async (p, o = {}) => (await fetch(BASE + p, { ...o, headers: { 'X-Admin-Token': ADMIN, ...(o.headers || {}) } })).json();
const count = async () => (await fetch(MOCK + '/__count')).json();
const PAGES = [['/', 'Dashboard'], ['/data-explorer', 'Data Explorer'], ['/upload', 'Upload CSV'], ['/predictions', 'Predictions'], ['/weather', 'Live Weather'], ['/model-training', 'Model Training'], ['/explainability', 'Explainability'], ['/history', 'History'], ['/about', 'About']];

async function main() {
  // ---------- shell: every page boots, sidebar + logo + title, theme, mobile menu
  for (const [p, title] of PAGES) { const w = await load(p); await sleep(500);
    ok(`page ${p} boots with sidebar, logo and title "${title}"`, $(w, '.sidebar') && $$(w, '.nav a').length === 9 && $(w, '.brand svg') && txt(w, '.topbar h1') === title && /Prediction System/.test(txt(w, '.brand')), txt(w, '.topbar h1'));
    ok(`page ${p} shows model banner`, await waitFor(() => /model/i.test(txt(w, '#model-banner')), 8000), txt(w, '#model-banner'));
    ok(`page ${p} has no map element`, !$(w, '#map') && !$(w, '#pickmap') && !$(w, '.leaflet-container') && !/leaflet|openstreetmap/i.test(w.document.documentElement.innerHTML)); }
  let w = await load('/');
  const before = w.document.documentElement.getAttribute('data-theme'); click(w, '#theme-toggle'); const after = w.document.documentElement.getAttribute('data-theme');
  ok('theme toggle switches theme and persists', before !== after && w.localStorage.getItem('theme') === after);
  click(w, '#menu-btn'); ok('mobile menu opens sidebar + scrim, aria-expanded true', $(w, '#sidebar').classList.contains('open') && $(w, '#scrim').classList.contains('open') && $(w, '#menu-btn').getAttribute('aria-expanded') === 'true');
  w.document.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape' })); ok('Escape closes the mobile menu', !$(w, '#sidebar').classList.contains('open'));
  click(w, '#menu-btn'); click(w, '#scrim'); ok('scrim click closes the mobile menu', !$(w, '#sidebar').classList.contains('open'));

  // ---------- dashboard empty state
  w = await load('/'); await waitFor(() => txt(w, '#s-total') === '0', 8000);
  ok('dashboard empty states (no fabricated data)', txt(w, '#s-total') === '0' && /No predictions yet/.test(txt(w, '#d-recent')) && /No predictions to chart/.test(txt(w, '#d-dist')) && /None yet/.test(txt(w, '#s-latest')) && txt(w, '#s-avg') === '—', txt(w, '#stats'));
  ok('dashboard weather status reachable (mock)', await waitFor(() => /reachable/.test(txt(w, '#wx-status')), 8000), txt(w, '#wx-status'));
  ok('dashboard shows metric definitions', /Prediction records saved to History/.test(txt(w, '#defs')));

  // ---------- Upload CSV: validation of file type/size, upload with progress, result
  w = await load('/upload');
  ok('upload: schema panel lists expected columns', await waitFor(() => /rainfall_24h_mm/.test(txt(w, '#schema')), 8000));
  setFile(w, '#file', new File(['x'], 'notes.txt')); ok('upload: non-CSV rejected client-side', /Only .csv files/.test(txt(w, '#result')) && $(w, '#go').disabled);
  setFile(w, '#file', new File([Buffer.alloc(21 * 1024 * 1024)], 'big.csv')); ok('upload: oversize file rejected before upload with the 20 MB limit', /limit is 20 MB/.test(txt(w, '#result')) && $(w, '#go').disabled, txt(w, '#result'));
  setFile(w, '#file', csvFile(process.env.TRAIN_CSV, 'train.csv')); ok('upload: valid file enables Upload', !$(w, '#go').disabled && /train\.csv/.test(txt(w, '#picked')));
  click(w, '#go'); await waitFor(() => /Upload accepted/.test(txt(w, '#result')), 30000);
  ok('upload: success message, counts (2 conflicting-label rows rejected), progress done, next-step links', /Upload accepted. 2998 of 3000 rows are valid/.test(txt(w, '#result')) && $(w, '#bar').style.width === '100%' && $(w, '#result a[href^="/data-explorer?dataset="]') && $(w, '#result a[href^="/model-training?dataset="]'), txt(w, '#result'));
  const trainId = (await api('/api/datasets?kind=train')).items[0].dataset_id;
  setFile(w, '#file', csvFile('/tmp/ui/bad.csv', 'bad.csv')); click(w, '#go'); await waitFor(() => /cannot be used yet/.test(txt(w, '#result')), 20000);
  ok('upload: invalid file explains missing columns and never guesses', /Missing required column/.test(txt(w, '#result')) && /never guessed/.test(txt(w, '#result')), txt(w, '#result').slice(0, 300));

  // ---------- Data Explorer: separate inspection workflow
  w = await load('/data-explorer?dataset=' + trainId); await waitFor(() => /Column quality/.test(txt(w, '#explorer')), 20000);
  const ex = txt(w, '#explorer');
  ok('explorer: counts, required columns, quality, statistics, preview', /Rows\s*3000/.test(ex) && /Columns\s*10/.test(ex) && /Required columns/.test(ex) && /Column quality/.test(ex) && /Descriptive statistics/.test(ex) && /rainfall_24h_mm/.test(ex) && /Preview \(as uploaded\)/.test(ex) && /Rows 1–25 of 3000/.test(ex), ex.slice(0, 200));
  ok('explorer has no upload form (separate workflow)', !$(w, '#drop') && !$(w, 'input[type=file]'));
  const nextBtn = $$(w, '#explorer button').find(b => b.textContent === 'Next'); nextBtn.click(); await waitFor(() => /Rows 26–50 of 3000/.test(txt(w, '#explorer')), 10000);
  ok('explorer: preview pagination', /Rows 26–50 of 3000/.test(txt(w, '#explorer')));
  const w2 = await load('/data-explorer'); await waitFor(() => $(w2, '#ds option[value]:not([value=""])'), 8000);
  ok('explorer: lists stored datasets without selection', $$(w2, '#ds option').length >= 3 && /No dataset selected/.test(txt(w2, '#explorer')));

  // ---------- Model Training: provenance required; unvalidated cannot be activated; feature exclusion; registry
  w = await load('/model-training?dataset=' + trainId); await waitFor(() => $(w, '#dataset').value === trainId, 8000);
  ok('training: dataset preselected and enabled', $(w, '#dataset').value === trainId && !$(w, '#train').disabled);
  ok('training: no upload form here', !$(w, 'input[type=file]'));
  click(w, '#train'); ok('training requires provenance text', /provenance is required/i.test(txt(w, '#train-out')));
  setVal(w, '#admin-token', ADMIN); setVal(w, '#prov', 'SYNTHETIC TEST FIXTURE (ui e2e)');
  $(w, 'input[name=origin][value=synthetic]').checked = true; click(w, '#train'); await waitFor(() => /Experimental|Validated/.test(txt(w, '#train-out')), 120000);
  ok('synthetic-origin model is experimental and cannot be activated', /Experimental \(cannot be activated\)/.test(txt(w, '#train-out')), txt(w, '#train-out').slice(0, 200));
  await waitFor(() => $(w, '#registry table'), 8000);
  ok('registry: Activate disabled for experimental', $$(w, '#registry button').filter(b => b.textContent === 'Activate').every(b => b.disabled));
  $(w, 'input[name=origin][value=verified]').checked = true; click(w, '#train'); await waitFor(() => /Validated:/.test(txt(w, '#train-out')), 120000);
  ok('verified-origin model (all features) validated and active as first validated model', /Validated:/.test(txt(w, '#train-out')) && /now the active model/.test(txt(w, '#train-out')), txt(w, '#train-out').slice(0, 300));
  const verA = (txt(w, '#train-out').match(/Saved (v[\w-]+)/) || [])[1];
  await waitFor(() => /Validated model active/.test(txt(w, '#model-banner')), 8000); ok('banner shows validated model active', /Validated model active/.test(txt(w, '#model-banner')));
  $$(w, '#registry button').find(b => b.textContent === 'Details').click(); await waitFor(() => /Validation gates/.test(txt(w, '#model-detail')), 8000);
  ok('model detail: metrics, inputs, gates, charts', /Test metric/.test(txt(w, '#model-detail')) && /drainage_capacity/.test(txt(w, '#model-detail')) && $$(w, '#model-detail [data-plotted]').length >= 2);

  // ---------- Predictions: the reported input problem (drainage_capacity) and its resolution
  w = await load('/predictions'); await waitFor(() => /Validated model active/.test(txt(w, '#model-banner')), 8000); await waitFor(() => /drainage_capacity/.test(txt(w, '#contract')), 8000);
  ok('predictions: contract lists inputs, units, ranges and sources; CSV columns', /slope_deg/.test(txt(w, '#contract')) && /No public source/.test(txt(w, '#contract')) && /Batch CSV columns: timestamp, latitude, longitude/.test(txt(w, '#contract')), txt(w, '#contract').slice(0, 300));
  ok('predictions: no map and no upload form (batch uses uploaded datasets)', !$(w, '#map') && !$(w, '#pickmap') && !$(w, 'input[type=file]'));
  setVal(w, '#lat', '19.11', 'change'); setVal(w, '#lon', '72.87', 'change'); ok('predictions: coordinates shown as text card with coverage', /19\.11000, 72\.87000/.test(txt(w, '#loc')) && /inside model region/.test(txt(w, '#coverage')));
  setVal(w, '#lat', '28.6', 'change'); setVal(w, '#lon', '77.2', 'change'); ok('predictions: out-of-region point warned (weather separate from coverage)', /outside the configured model region/.test(txt(w, '#coverage')));
  setVal(w, '#lat', '95', 'change'); ok('predictions: invalid latitude rejected with message', /between −90 and 90/.test(txt(w, '#loc')));
  setVal(w, '#q', 'Andheri'); click(w, '#find'); await waitFor(() => $$(w, '#hits li').length, 8000); const hits = $$(w, '#hits li');
  ok('place search: deduplicated, in-region ranked first', hits.length === 2 && /in model region/.test(hits[0].textContent) && /outside model region/.test(hits[1].textContent), hits.map(h => h.textContent).join('|'));
  hits[0].querySelector('button').click(); ok('selecting a result shows the place name as text', /Andheri, Maharashtra, India/.test(txt(w, '#loc')) && $(w, '#lat').value.startsWith('19.11'));
  click(w, '#fetch'); await waitFor(() => $(w, '#features table'), 10000); const ft = txt(w, '#features');
  ok('features: provider/derived sources labelled; drainage unavailable', /DEM-derived/.test(ft) && /open-meteo/.test(ft) && /unavailable · enter manually/.test(ft) && $$(w, '#features button').filter(b => b.textContent === 'Override').length >= 3, ft.slice(0, 400));
  ok('features: slope derived (not zero), rainfall provider-read-only', Number($(w, '#feat-slope_deg').value) > 0 && $(w, '#feat-rainfall_24h_mm').readOnly && !$(w, '#feat-drainage_capacity').readOnly, $(w, '#feat-slope_deg').value);
  click(w, '#predict'); ok('REPORTED CASE: missing drainage_capacity explained, nothing guessed, no prediction shown', /cannot run yet/.test(txt(w, '#result')) && /drainage_capacity: needs a number/.test(txt(w, '#result')) && /retrain/i.test(txt(w, '#result')) && !/Model probability/.test(txt(w, '#result')), txt(w, '#result'));
  setVal(w, '#feat-drainage_capacity', '5'); setVal(w, '#feat-slope_deg', '120'); click(w, '#predict'); ok('out-of-range manual values are explained client-side', /outside the allowed range/.test(txt(w, '#result')) && /slope_deg/.test(txt(w, '#result')), txt(w, '#result'));
  setVal(w, '#feat-slope_deg', '1.4'); setVal(w, '#feat-drainage_capacity', '0.4'); $(w, '#save').checked = true; click(w, '#predict'); await waitFor(() => /Model probability/.test(txt(w, '#result')), 15000);
  ok('prediction succeeds with provenance, disclaimer, saved-record link', /Model probability/.test(txt(w, '#result')) && /statistical estimate/.test(txt(w, '#result')) && $(w, '#result a[href^="/history/"]'), txt(w, '#result').slice(0, 300));
  ok('explanation chart drawn', $(w, '#expl [data-plotted]'));
  const rec = (await api('/api/history?limit=1')).items[0], det = await api('/api/history/' + rec.id);
  ok('history record keeps location name, model version and per-feature sources (slope was overridden by the user)', rec.location_name === 'Andheri, Maharashtra, India' && det.record.inputs._sources.drainage_capacity === 'user-supplied' && det.record.inputs._sources.slope_deg === 'user-supplied' && /open-meteo elevation/.test(det.record.inputs._sources.elevation_m) && /open-meteo/.test(det.record.inputs._sources.rainfall_24h_mm) && rec.model_version === verA, JSON.stringify(det.record.inputs));

  // ---------- resolve the mismatch properly: retrain WITHOUT drainage_capacity, activate explicitly
  w = await load('/model-training?dataset=' + trainId); await waitFor(() => $(w, '#dataset').value === trainId, 8000);
  setVal(w, '#admin-token', ADMIN); setVal(w, '#prov', 'SYNTHETIC TEST FIXTURE (ui e2e, no drainage)'); $(w, '.excl[value=drainage_capacity]').checked = true; click(w, '#train');
  await waitFor(() => /Validated:/.test(txt(w, '#train-out')), 120000); const verB = (txt(w, '#train-out').match(/Saved (v[\w-]+)/) || [])[1];
  ok('retrain without drainage_capacity: validated but NOT auto-activated over the active model', /Validated:/.test(txt(w, '#train-out')) && /Activate it in the registry/.test(txt(w, '#train-out')) && verB && verB !== verA, txt(w, '#train-out').slice(0, 300));
  await waitFor(() => $$(w, '#registry tr').length >= 4, 8000); const rowB = $$(w, '#registry tr').find(r => r.textContent.includes(verB));
  rowB.querySelectorAll('button')[1].click(); await waitFor(() => /active/.test($$(w, '#registry tr').find(r => r.textContent.includes(verB)).textContent), 8000);
  ok('explicit activation switches the active model', (await api('/api/models')).active === verB);
  w = await load('/predictions'); await waitFor(() => /Validated model active/.test(txt(w, '#model-banner')), 8000); await waitFor(() => /slope_deg/.test(txt(w, '#contract')), 8000);
  ok('contract no longer asks for drainage_capacity', !/drainage_capacity/.test(txt(w, '#contract')) && !/drainage_capacity/.test(txt(w, '#contract')));
  click(w, '#fetch'); await waitFor(() => $(w, '#features table'), 10000); ok('all inputs available from provider/derived: predict works with no manual entry', !/enter manually/.test(txt(w, '#features')) && !$(w, '#predict').disabled);
  click(w, '#predict'); await waitFor(() => /Model probability/.test(txt(w, '#result')), 15000); ok('prediction without manual inputs succeeds', /Model probability/.test(txt(w, '#result')), txt(w, '#result'));
  ok('contract is enforced for single and batch alike', JSON.stringify((await api('/api/contract')).csv_columns) === JSON.stringify(['timestamp', 'latitude', 'longitude', 'rainfall_1h_mm', 'rainfall_24h_mm', 'rainfall_72h_mm', 'elevation_m', 'slope_deg']));

  // ---------- batch: dataset uploaded on Upload CSV, run on Predictions
  const wu = await load('/upload'); $(wu, 'input[name=kind][value=predict]').checked = true; setFile(wu, '#file', csvFile(process.env.PRED_CSV, 'batch.csv')); click(wu, '#go'); await waitFor(() => /Upload accepted/.test(txt(wu, '#result')), 20000);
  const predId = (await api('/api/datasets?kind=predict')).items[0].dataset_id;
  w = await load('/predictions?dataset=' + predId); await waitFor(() => /Validated model active/.test(txt(w, '#model-banner')), 8000); await waitFor(() => $(w, '#bds').value === predId, 8000);
  ok('batch tab opens with the dataset preselected and a validation summary', $$(w, '.tabs button')[1].getAttribute('aria-selected') === 'true' && /valid 60/.test(txt(w, '#bval')) && !$(w, '#brun').disabled, txt(w, '#bval'));
  $(w, '#bhist').checked = true; click(w, '#brun'); await waitFor(() => /Predicted \d+ of/.test(txt(w, '#bjob')), 30000);
  ok('batch job completes with downloads and a result table', /Predicted 60 of 60/.test(txt(w, '#bjob')) && $(w, '#bjob a[href$="/download/predictions"]'), txt(w, '#bjob'));
  await waitFor(() => $(w, '#bres table'), 8000); ok('batch results paginated with risk badges', $(w, '#bres table') && /Page 1/.test(txt(w, '#bpg')));
  const csvText = await (await fetch(BASE + $(w, '#bjob a[href$="/download/predictions"]').getAttribute('href'))).text(); ok('batch CSV download is real and matches the contract', csvText.split('\n')[0].startsWith('row_id,timestamp') && csvText.trim().split('\n').length === 61);

  // ---------- Live Weather: fresh request on every click, full current block, not-reported handling
  w = await load('/weather'); ok('weather page has no map; location is a text card', !$(w, '#map') && !$(w, '#pickmap') && /Selected coordinates/.test(txt(w, '#loc')));
  const c0 = await count(); click(w, '#get'); await waitFor(() => $(w, '#w .wx-hero'), 10000); const c1 = await count();
  const t = txt(w, '#w');
  ok('weather: temperature, condition, feels-like, humidity, wind, precipitation, observation + retrieval times', /29\.5°C/.test(t) && /Slight rain/.test(t) && /Feels like 33\.1°C/.test(t) && /78 %/.test(t) && /12\.4 km\/h SW \(225°\)/.test(t) && /Precipitation now\s*0\.2 mm/.test(t) && /Observation time/.test(t) && /Retrieved/.test(t) && /not a station observation/.test(t), t.slice(0, 600));
  ok('weather: field omitted by provider shows "Not reported", not 0', /Rain now\s*Not reported/.test(t), t);
  ok('weather: icon drawn locally; live status badge', $(w, '#w svg.wx-icon') && /live/.test(txt(w, '#status')));
  ok('weather: first click made a new provider request', c1.forecast === c0.forecast + 1, JSON.stringify([c0, c1]));
  await waitFor(() => !$(w, '#get').disabled, 5000); click(w, '#get'); await waitFor(() => !$(w, '#get').disabled, 10000); await sleep(300); const c2 = await count();
  ok('weather: clicking again issues ANOTHER provider request (server cache TTL is 600 s)', c2.forecast === c1.forecast + 1, JSON.stringify([c1, c2]));
  ok('weather: rainfall windows (model analysis) and forecast chart', /Last 24 h\s*12 mm/.test(txt(w, '#windows')) && /recent model analysis/.test(txt(w, '#windows')) && $(w, '#chart [data-plotted]'));
  ok('weather: coverage reported separately', /inside model region/.test(t));
  setVal(w, '#lat', '28.6', 'change'); setVal(w, '#lon', '77.2', 'change'); click(w, '#get'); await waitFor(() => /outside the configured model region/.test(txt(w, '#w')), 10000);
  ok('weather available outside model coverage, with a clear coverage explanation', /Slight rain/.test(txt(w, '#w')) && /outside the configured model region/.test(txt(w, '#w')));
  ok('"Use in Predictions" carries the coordinates', /lat=28\.6/.test($(w, '#to-pred').getAttribute('href')));
  setVal(w, '#q', 'Andheri'); click(w, '#find'); await waitFor(() => $$(w, '#hits li').length, 8000); $$(w, '#hits li')[0].querySelector('button').click();
  ok('weather: place search selects a named location', /Andheri, Maharashtra, India/.test(txt(w, '#loc')) && /name=Andheri/.test($(w, '#to-pred').getAttribute('href')));
  click(w, '#get'); await waitFor(() => /Andheri, Maharashtra, India/.test(txt(w, '#w')), 10000); ok('weather heading uses the selected place name', /Andheri, Maharashtra, India/.test(txt(w, '#w')));
  setVal(w, '#lat', 'abc', 'change'); click(w, '#get'); ok('weather: invalid coordinates rejected before any request', /valid location/.test(txt(w, '#w')) || /between −90 and 90/.test(txt(w, '#loc')));

  // ---------- History: filters, details; Dashboard with real data; Explainability
  w = await load('/history'); await waitFor(() => $(w, '#p-body table'), 8000);
  ok('history: saved single + batch predictions with Details links', $$(w, '#p-body a').some(a => /\/history\/\d+/.test(a.getAttribute('href'))) && /record\(s\)/.test(txt(w, '#p-pg')));
  setVal(w, '#hq', 'andheri'); await waitFor(() => /Andheri/.test(txt(w, '#p-body')) && /1 record/.test(txt(w, '#p-pg')), 8000);
  ok('history: location search filter', /Andheri, Maharashtra, India/.test(txt(w, '#p-body')) && /1 record\(s\)/.test(txt(w, '#p-pg')), txt(w, '#p-pg'));
  setVal(w, '#hq', 'zzzz'); await waitFor(() => /No saved predictions match/.test(txt(w, '#p-body')), 8000); ok('history: empty filter result message', /No saved predictions match/.test(txt(w, '#p-body')));
  setVal(w, '#hq', ''); setVal(w, '#hsrc', 'batch', 'change'); await waitFor(() => /60 record/.test(txt(w, '#p-pg')), 8000); ok('history: source filter', /60 record\(s\)/.test(txt(w, '#p-pg')), txt(w, '#p-pg'));
  await waitFor(() => $(w, '#j-body table'), 8000); ok('history: Jobs tab lists training and batch jobs', /Training/.test(txt(w, '#j-body')) && /Batch prediction/.test(txt(w, '#j-body')));
  const dw = await load('/history/' + rec.id); await waitFor(() => /Model inputs and where they came from/.test(txt(dw, '#detail')), 8000);
  ok('prediction details page: place, probability, model, input sources', /Andheri, Maharashtra, India/.test(txt(dw, '#detail')) && /user-supplied/.test(txt(dw, '#detail')) && /open-meteo elevation/.test(txt(dw, '#detail')) && /statistical estimate/.test(txt(dw, '#detail')), txt(dw, '#detail').slice(0, 300));
  const mw = await load('/history/999999'); await waitFor(() => /no longer exists/.test(txt(mw, '#detail')), 8000); ok('details page: missing record handled', /no longer exists/.test(txt(mw, '#detail')));
  const hv = await api('/api/history?limit=1'); const dash = await api('/api/dashboard');
  w = await load('/'); await waitFor(() => txt(w, '#s-total') !== '…' && txt(w, '#s-total') !== '0', 8000);
  ok('dashboard shows real totals that match the API', txt(w, '#s-total') === String(hv.total) && dash.total_records === hv.total && Number(txt(w, '#s-total')) >= 61, `${txt(w, '#s-total')} vs ${hv.total}`);
  ok('dashboard: recent predictions with View details; donut + legend with denominator; insights; status', $$(w, '#d-recent a').some(a => /\/history\/\d+/.test(a.getAttribute('href'))) && $(w, '#d-dist [data-plotted]') && /saved prediction\(s\) in History/.test(txt(w, '#d-dist')) && $$(w, '#d-insights li').length >= 1 && /validated/.test(txt(w, '#d-status')), txt(w, '#d-dist').slice(0, 200));
  ok('dashboard quick actions present', ['/data-explorer', '/upload', '/predictions', '/history', '/weather'].every(h => $(w, `.qa[href="${h}"]`)));
  w = await load('/explainability'); await waitFor(() => $(w, '#root [data-plotted]'), 8000); ok('explainability: version selector, importance chart, association caveat', $$(w, '#ver option').length >= 3 && $(w, '#root [data-plotted]') && /not causal|not proof|association/i.test(txt(w, '#root') + txt(w, '.lead')));
  w = await load('/about'); await waitFor(() => !/Loading/.test(txt(w, '#verification')), 8000); ok('about: logo, sections and recorded verification', $(w, 'img[src="/static/img/logo.svg"]') && /Project overview/.test(txt(w, 'main')) && $(w, '#verification').textContent.length > 10);
  const dv = await api('/api/history?limit=1'); ok('API confirms persisted history', dv.total >= 61);
}

async function failures() {  // provider is DOWN in this phase (mock killed)
  let w = await load('/weather'); setVal(w, '#lat', '18.95', 'change'); setVal(w, '#lon', '72.85', 'change'); click(w, '#get');
  await waitFor(() => $(w, '#w .alert'), 15000);
  ok('outage: clear error, no values assumed, Retry offered', /Weather service is unavailable/.test(txt(w, '#w')) && /nothing has been assumed/.test(txt(w, '#w')) && !$(w, '#w .wx-hero') && $$(w, '#w button').some(b => b.textContent === 'Retry') && /failed/.test(txt(w, '#status')), txt(w, '#w'));
  $$(w, '#w button').find(b => b.textContent === 'Retry').click(); await waitFor(() => /failed/.test(txt(w, '#status')) && !$(w, '#get').disabled, 15000); ok('outage: Retry sends another request and fails honestly', /Weather service is unavailable/.test(txt(w, '#w')));
  setVal(w, '#lat', '19.076', 'change'); setVal(w, '#lon', '72.8777', 'change'); click(w, '#get'); await waitFor(() => $(w, '#w .wx-hero'), 15000);
  ok('outage: previously fetched point shown as STALE with last-success time, never as live', /last successful result, retrieved/.test(txt(w, '#w')) && /stale data/.test(txt(w, '#status')) && !/live/.test(txt(w, '#status')), txt(w, '#w').slice(0, 300));
  w = await load('/'); await waitFor(() => /stale|unavailable/.test(txt(w, '#wx-status')), 10000); ok('dashboard: weather degraded, other cards still render', /stale|unavailable/.test(txt(w, '#wx-status')) && $(w, '#s-total'));
  w = await load('/predictions'); await waitFor(() => /Validated model active/.test(txt(w, '#model-banner')), 8000); setVal(w, '#lat', '18.95', 'change'); setVal(w, '#lon', '72.85', 'change'); click(w, '#fetch'); await waitFor(() => $(w, '#features .alert') || $(w, '#features table'), 12000);
  click(w, '#predict'); ok('predictions survive the outage: missing inputs are asked for, nothing guessed', /unavailable/.test(txt(w, '#features')) && /cannot run yet/.test(txt(w, '#result')) && !/Model probability/.test(txt(w, '#result')), txt(w, '#result'));
  w = await load('/'); ok('unrelated pages are unaffected by the outage', $(w, '.sidebar') && $(w, '#stats'));
}

(async () => { try { await (PHASE === 'main' ? main() : failures()); } catch (e) { ok('script completed', false, e.stack); }
  ok('no JavaScript errors across pages', errors.length === 0, errors.slice(0, 5).join(' || '));
  const failed = results.filter(r => !r.pass); results.forEach(r => console.log((r.pass ? 'PASS ' : 'FAIL ') + r.name + (r.detail ? '  -> ' + r.detail : '')));
  console.log(`\n${results.length - failed.length}/${results.length} UI checks passed`); process.exit(failed.length ? 1 : 0); })();
