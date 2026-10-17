'use strict';
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) { if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v; else if (v !== false && v != null) e.setAttribute(k, v); }
  for (const k of kids.flat()) if (k != null) e.append(k.nodeType ? k : document.createTextNode(String(k)));
  return e;
}
async function api(path, opts = {}) {
  const o = { headers: {}, ...opts };
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers['Content-Type'] = 'application/json'; o.method = o.method || 'POST'; delete o.json; }
  const tok = sessionStorage.getItem('adminToken'); if (tok && o.admin) o.headers['X-Admin-Token'] = tok;
  let r; try { r = await fetch(path, o); } catch (e) { throw new Error('Network error: the server could not be reached.'); }
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) { const err = new Error((data && data.detail) || ('Request failed (' + r.status + ')')); err.status = r.status; err.code = data && data.code; throw err; }
  return data;
}
function alertBox(kind, text) { return h('div', { class: 'alert a-' + kind, role: kind === 'err' ? 'alert' : 'status', text }); }
function show(target, ...nodes) { const t = typeof target === 'string' ? $(target) : target; t.replaceChildren(...nodes.flat().filter(Boolean)); }
function loading(target, msg = 'Loading…') { show(target, h('p', { class: 'muted' }, h('span', { class: 'spin', 'aria-hidden': 'true' }), msg)); }
function empty(msg, link) { return h('div', { class: 'empty' }, msg, link ? [' ', link] : null); }
const pct = x => x == null ? '—' : (100 * x).toFixed(1) + '%';
const num = (x, d = 3) => x == null ? '—' : Number(x).toFixed(d);
function table(cols, rows, opt = {}) {
  /* opt.wrap: allow cell text to wrap; opt.stack: on phones render each row as a labelled card (no sideways scrolling) */
  return h('div', { class: 'table-wrap' }, h('table', { class: [opt.wrap ? 'wrap' : '', opt.stack ? 'stack' : ''].join(' ').trim() || null },
    h('thead', {}, h('tr', {}, cols.map(c => h('th', { scope: 'col', text: c.label })))),
    h('tbody', {}, rows.map(r => h('tr', {}, cols.map(c => h('td', { 'data-label': c.label || null }, c.render ? c.render(r) : (r[c.key] ?? '—'))))))));
}
function badge(state, label) { return h('span', { class: 'badge b-' + state, text: label || String(state).replace('_', ' ') }); }
function plotTheme() {
  const cs = getComputedStyle(document.documentElement), v = n => cs.getPropertyValue(n).trim();
  return { paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: { color: v('--text'), family: 'system-ui, sans-serif' },
    xaxis: { gridcolor: v('--border'), zerolinecolor: v('--border') }, yaxis: { gridcolor: v('--border'), zerolinecolor: v('--border') }, margin: { t: 30, r: 10, b: 50, l: 60 } };
}
const plots = [];
function plot(el, data, layout = {}) {
  const t = plotTheme(); const draw = () => { const tt = plotTheme(); Plotly.react(el, data, { ...tt, ...layout, xaxis: { ...tt.xaxis, ...(layout.xaxis || {}) }, yaxis: { ...tt.yaxis, ...(layout.yaxis || {}) } }, { responsive: true, displaylogo: false }); };
  draw(); plots.push(draw);
}
function setTheme(t) { document.documentElement.setAttribute('data-theme', t); try { localStorage.setItem('theme', t); } catch (e) {} syncToggle(); plots.forEach(f => f()); document.dispatchEvent(new Event('themechange')); }
function syncToggle() { const b = $('#theme-toggle'); if (!b) return; const dark = document.documentElement.getAttribute('data-theme') === 'dark'; b.setAttribute('aria-pressed', String(dark)); b.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode'); $('#icon-sun').style.display = dark ? '' : 'none'; $('#icon-moon').style.display = dark ? 'none' : ''; }
document.addEventListener('DOMContentLoaded', () => {
  const b = $('#theme-toggle'); if (b) b.addEventListener('click', () => setTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark')); syncToggle();
  const m = $('#menu-btn'), sb = $('#sidebar'), sc = $('#scrim');
  const closeNav = () => { if (!sb) return; sb.classList.remove('open'); sc.classList.remove('open'); if (m) { m.setAttribute('aria-expanded', 'false'); m.setAttribute('aria-label', 'Open navigation'); } };
  if (m) m.addEventListener('click', () => { const o = sb.classList.toggle('open'); sc.classList.toggle('open', o); m.setAttribute('aria-expanded', String(o)); m.setAttribute('aria-label', o ? 'Close navigation' : 'Open navigation'); });
  if (sc) sc.addEventListener('click', closeNav); document.addEventListener('keydown', e => { if (e.key === 'Escape') closeNav(); });
  loadBanner();
});
async function loadBanner() {
  const t = $('#model-banner'); if (!t) return;
  try {
    const s = await api('/api/status'); const m = s.model; window.__status = s;
    const kind = { not_trained: 'warn', experimental: 'warn', validated: 'ok' }[m.state];
    const label = { not_trained: 'No validated model is active', experimental: 'Experimental model (not validated)', validated: 'Validated model active' }[m.state];
    t.replaceChildren(h('div', { class: 'alert a-' + kind }, h('strong', { text: label + ': ' }), m.description, m.version ? h('span', { class: 'muted small', text: '  (' + m.version + ')' }) : null));
    document.dispatchEvent(new CustomEvent('status', { detail: s }));
  } catch (e) { t.replaceChildren(alertBox('err', 'Could not load model status: ' + e.message)); }
}
async function pollJob(id, onTick) {
  for (;;) { const j = await api('/api/jobs/' + id); if (onTick) onTick(j); if (['succeeded', 'failed', 'abandoned'].includes(j.status)) return j; await new Promise(r => setTimeout(r, 1200)); }
}
function adminTokenField() {
  const i = h('input', { type: 'password', id: 'admin-token', autocomplete: 'off', placeholder: 'Admin token (kept in this tab only)' });
  i.value = sessionStorage.getItem('adminToken') || ''; i.addEventListener('input', () => sessionStorage.setItem('adminToken', i.value));
  return h('div', {}, h('label', { for: 'admin-token', text: 'Admin token' }), i, h('p', { class: 'hint', text: 'Required for training, model activation and deleting history when the server has ADMIN_TOKEN set.' }));
}

const fmtTime = t => t ? new Date(t).toLocaleString() : '—';
function sourceBadge(f) {
  if (!f || f.status !== 'available') return badge('unavailable', 'unavailable');
  const k = { recent_model_analysis: 'model analysis', historical_reanalysis: 'reanalysis', forecast: 'forecast', dem_elevation: 'DEM elevation', user_supplied: 'user-supplied', current_model_analysis: 'current (model)' }[f.kind] || f.kind;
  return badge(f.kind === 'user_supplied' ? 'info' : 'available', (f.source || '') + ' · ' + k);
}
function tabs(root, panels) {
  const bar = h('div', { class: 'tabs', role: 'tablist' }); const btns = {};
  const select = key => { Object.entries(btns).forEach(([k, b]) => b.setAttribute('aria-selected', String(k === key))); Object.entries(panels).forEach(([k, p]) => { p.hidden = k !== key; }); };
  Object.keys(panels).forEach(k => { const b = h('button', { type: 'button', role: 'tab', 'aria-selected': 'false', text: k }); b.addEventListener('click', () => select(k)); btns[k] = b; bar.append(b); });
  root.prepend(bar); select(Object.keys(panels)[0]); return select;
}

const riskLabel = l => l === 'pending_validation' ? 'No category' : (l || '—');
function riskBadge(l) { return h('span', { class: 'badge risk risk-' + (l || 'pending_validation'), text: riskLabel(l) }); }
const RISK_COLOR = { Low: '#17804f', Medium: '#d98a0b', High: '#c0312b', pending_validation: '#8aa0bb' };
function placeLabel(r) { return r.location_name || `${Number(r.latitude).toFixed(4)}, ${Number(r.longitude).toFixed(4)}`; }
/* Inline weather icons (local, no external assets). Only fixed strings are ever parsed as markup. */
const WX_ICONS = {
  clear: '<circle cx="12" cy="12" r="4.5"/><path d="M12 2.5v2.5M12 19v2.5M4.2 4.2l1.8 1.8M18 18l1.8 1.8M2.5 12H5M19 12h2.5M4.2 19.8L6 18M18 6l1.8-1.8"/>',
  partly: '<circle cx="8.5" cy="8.5" r="3"/><path d="M8.5 2.5v1.5M2.5 8.5H4M4.3 4.3l1 1M12.7 4.3l-1 1"/><path d="M8 20a3.5 3.5 0 0 1-.4-7A5 5 0 0 1 17.2 13 3.5 3.5 0 0 1 17 20z"/>',
  cloud: '<path d="M7 19a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 11.5 4 4 0 0 1 17.5 19z"/>',
  fog: '<path d="M7 14a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 6.5 4 4 0 0 1 17.5 14z"/><path d="M4 17.5h16M6 21h12"/>',
  drizzle: '<path d="M7 14a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 6.5 4 4 0 0 1 17.5 14z"/><path d="M8 17.5v1.5M12 17.5v1.5M16 17.5v1.5"/>',
  rain: '<path d="M7 14a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 6.5 4 4 0 0 1 17.5 14z"/><path d="M8 17l-1 3M12 17l-1 3M16 17l-1 3"/>',
  snow: '<path d="M7 14a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 6.5 4 4 0 0 1 17.5 14z"/><path d="M8 18h.01M12 20h.01M16 18h.01M10 21h.01M14 21h.01"/>',
  thunder: '<path d="M7 14a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 6.5 4 4 0 0 1 17.5 14z"/><path d="M12.5 14l-2.5 4h3l-2 4"/>',
  unknown: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7M12 17h.01"/>' };
function weatherIcon(key) { const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('class', 'wx-icon'); s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor');
  s.setAttribute('stroke-width', '1.6'); s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round'); s.setAttribute('aria-hidden', 'true'); s.innerHTML = WX_ICONS[key] || WX_ICONS.unknown; return s; }
function xhrUpload(url, file, onProgress) {
  return new Promise((resolve, reject) => { const x = new XMLHttpRequest(); x.open('POST', url); x.responseType = 'json';
    x.upload.onprogress = e => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    x.onload = () => { const d = x.response; if (x.status >= 200 && x.status < 300) resolve(d); else { const err = new Error((d && d.detail) || 'Upload failed (' + x.status + ')'); err.status = x.status; reject(err); } };
    x.onerror = () => reject(new Error('Network error: the server could not be reached.'));
    const fd = new FormData(); fd.append('file', file); x.send(fd); });
}
