'use strict';
const cfgP = JSON.parse($('#cfg').textContent), qs0 = new URLSearchParams(location.search);
let model = null, feat = null, contract = null;
tabs($('#pred-root'), { 'Single prediction': $('#single'), 'Batch prediction': $('#batch') });
const loc = initLocation((p, inside) => show('#coverage', coverageNote(inside, cfgP.region)));

(async () => {
  try { const s = await api('/api/status'); model = s.model; } catch (e) { return; }
  try {
    contract = await api('/api/contract');
    show('#contract', contract.for_active_model ? null : alertBox('info', 'No validated model is active, so this is the default schema, not a model contract.'),
      table([{ label: 'Input', render: f => h('span', { class: 'mono', text: f.name }) }, { label: 'Unit', key: 'unit' }, { label: 'Allowed range', render: f => (f.min != null || f.max != null) ? `${f.min ?? '−∞'} – ${f.max ?? '∞'}` : '—' }, { label: 'Where the value comes from', key: 'source_note' }], contract.features, { wrap: true, stack: true }),
      h('p', { class: 'hint', text: 'Batch CSV columns: ' + contract.csv_columns.join(', ') + '. Single and batch prediction use exactly this contract.' }));
  } catch (e) { show('#contract', alertBox('err', e.message)); }
  if (!model || model.state !== 'validated') {
    const msg = 'Predictions are unavailable: no validated model is active. You can still retrieve features and check the weather.';
    show('#single-gate', alertBox('warn', msg), h('p', {}, h('a', { class: 'btn sm', href: '/model-training', text: 'Go to Model Training' }))); show('#batch-gate', alertBox('warn', msg)); $('#brun').disabled = true;
  }
  loadBatchList();
})();

$('#fetch').addEventListener('click', async () => {
  const p = loc.point(); if (!loc.valid(p)) return show('#features', alertBox('err', 'Enter a valid location first.'));
  loading('#features', 'Retrieving features…'); $('#predict').disabled = true; show('#result'); show('#expl');
  try {
    const r = await api('/api/features', { json: { latitude: p.lat, longitude: p.lon, timestamp: $('#ts').value.trim() || null } });
    feat = r; const names = r.needed_features.filter(n => n !== 'latitude' && n !== 'longitude'), spec = Object.fromEntries((contract ? contract.features : []).map(f => [f.name, f]));
    feat.rows = names.map(n => { const f = r.features[n], ok = f.status === 'available', input = h('input', { id: 'feat-' + n, inputmode: 'decimal', value: ok ? f.value : '', readonly: ok ? 'readonly' : null, 'aria-label': n + (spec[n] ? ' (' + spec[n].unit + ')' : ''), placeholder: ok ? '' : 'enter value' });
      const row = { n, input, f }; row.override = () => { input.readOnly = false; row.f = { ...f, status: 'unavailable', overridden: true }; input.focus(); }; return row; });
    show('#features', r.model ? null : alertBox('info', 'No validated model is active; showing the default feature set.'), r.stale ? alertBox('warn', 'Provider unreachable: cached data last updated ' + fmtTime(r.last_updated_utc) + '.') : null, r.notes.map(n => alertBox('warn', n)),
      table([{ label: 'Input', render: x => h('span', { class: 'mono', text: x.n }) }, { label: 'Value', render: x => x.input }, { label: 'Unit', render: x => (spec[x.n] || {}).unit || '' },
        { label: 'Source', render: x => x.f.status === 'available' ? sourceBadge(x.f) : badge('unavailable', 'unavailable · enter manually') },
        { label: '', render: x => { if (x.f.status !== 'available') return ''; const b = h('button', { class: 'btn sm', type: 'button', text: 'Override' }); b.addEventListener('click', () => { x.override(); b.remove(); }); return b; } }], feat.rows),
      feat.rows.filter(x => x.f.note).map(x => h('p', { class: 'hint', text: `${x.n}: ${x.f.note}` })),
      h('p', { class: 'hint', text: `Reference time (UTC): ${r.timestamp_utc}. Provider values are model analysis, not gauge data. Missing values are never filled with 0: enter them from a trustworthy source or the prediction is refused.` }));
    if (!r.coverage.eligible) show('#coverage', coverageNote(false, cfgP.region));
    $('#predict').disabled = !(model && model.state === 'validated');
  } catch (e) { show('#features', alertBox('err', e.message)); }
});

$('#predict').addEventListener('click', async () => {
  if (!feat) return; const p = loc.point(), features = {}, sources = {}, problems = [];
  const spec = Object.fromEntries((contract ? contract.features : []).map(f => [f.name, f]));
  feat.rows.forEach(({ n, input, f }) => {
    const t = input.value.trim(), s = spec[n] || {};
    if (t === '' || !isFinite(Number(t))) { problems.push(`${n}: needs a number${s.unit ? ' in ' + s.unit : ''}. ` + (f.note || '')); return; }
    const v = Number(t); if ((s.min != null && v < s.min) || (s.max != null && v > s.max)) { problems.push(`${n}: ${v} is outside the allowed range ${s.min ?? '−∞'}–${s.max ?? '∞'} ${s.unit || ''}.`); return; }
    features[n] = v; sources[n] = f.status === 'available' && !f.overridden && v === f.value ? `${f.source} (${f.kind})` : 'user-supplied';
  });
  if (problems.length) return show('#result', alertBox('err', 'The model cannot run yet:'), h('ul', { class: 'small', style: 'margin:0 0 var(--s3);padding-left:1.2rem' }, problems.map(x => h('li', { text: x }))),
    h('p', { class: 'small muted', text: 'Nothing is guessed or set to zero. You can enter a value from your own data, or retrain the model without a feature that cannot be supplied (Model Training → “Leave out features”).' }));
  loading('#result', 'Predicting…'); show('#expl');
  try {
    const r = await api('/api/predict', { json: { timestamp: feat.timestamp_utc, latitude: p.lat, longitude: p.lon, features, feature_sources: sources, location_name: p.name, save_history: $('#save').checked } }), x = r.result;
    show('#result', alertBox(r.model.status === 'validated' ? 'ok' : 'warn', r.model.description), h('p', { style: 'font-size:1.3rem;font-weight:800;margin-bottom:var(--s2)', text: x.predicted_class ? 'Waterlogging estimated' : 'No waterlogging estimated' }),
      h('p', {}, 'Model probability: ', h('strong', { text: pct(x.probability) }), ' · risk category: ', riskBadge(x.risk_level)), r.risk_note ? h('p', { class: 'small muted', text: r.risk_note }) : null,
      r.warnings.map(w => alertBox('warn', w)), x.imputed_inputs ? alertBox('warn', 'Imputed with training medians: ' + x.imputed_inputs) : null,
      h('p', { class: 'small muted', text: `Model ${x.model_version} (${x.model_status}) · ${fmtTime(x.prediction_timestamp)}` }), r.history_id ? h('p', {}, h('a', { href: '/history/' + r.history_id, text: 'View saved record' })) : null, h('p', { class: 'small', text: r.disclaimer }));
    const items = r.explanation.items; if (items.length) { const el = h('div', { class: 'chart' }); show('#expl', h('h3', { class: 'small', text: 'Why this result (model association, not causation)' }), el);
      plot(el, [{ type: 'bar', orientation: 'h', y: items.map(i => i.feature).reverse(), x: items.map(i => i.delta_probability).reverse(), marker: { color: items.map(i => i.delta_probability >= 0 ? '#c0312b' : '#17804f').reverse() } }], { xaxis: { title: 'Change in probability vs a typical value' } }); }
  } catch (e) { show('#result', alertBox('err', e.message)); }
});

/* ---------------- batch: runs on a dataset uploaded via Upload CSV ---------------- */
let dsId = null, jobId = null, page = 1;
async function loadBatchList() {
  try {
    const d = await api('/api/datasets?kind=predict&status=validated'), sel = $('#bds');
    sel.replaceChildren(h('option', { value: '', text: d.items.length ? 'Select a prediction dataset…' : 'No validated prediction datasets uploaded' }), ...d.items.map(x => h('option', { value: x.dataset_id, text: `${x.name} · ${x.n_rows} rows` })));
    sel.addEventListener('change', () => pickBatch(sel.value)); const q = qs0.get('dataset'); if (q && d.items.some(i => i.dataset_id === q)) { sel.value = q; pickBatch(q); }
  } catch (e) { show('#bval', alertBox('err', e.message)); }
}
async function pickBatch(id) {
  dsId = id || null; $('#brun').disabled = true; if (!id) return show('#bval');
  try { const d = await api('/api/datasets/' + id), r = d.report;
    show('#bval', h('p', { text: `Rows ${r.n_rows_input} · valid ${r.n_rows_valid} · rejected ${r.n_rows_rejected}` }), r.warnings.slice(0, 4).map(x => alertBox('warn', x)), r.n_rows_rejected ? h('a', { class: 'btn sm', href: `/api/datasets/${id}/rejected.csv`, text: 'Download rejected rows' }) : null);
    $('#brun').disabled = !(model && model.state === 'validated');
  } catch (e) { show('#bval', alertBox('err', e.message)); }
}
$('#brun').addEventListener('click', async () => {
  $('#brun').disabled = true; loading('#bjob', 'Submitting…');
  try {
    const j = await api('/api/predict/batch', { json: { dataset_id: dsId, save_history: $('#bhist').checked } });
    const d = await pollJob(j.id, x => loading('#bjob', 'Job ' + x.status + '…'));
    if (d.status !== 'succeeded') return show('#bjob', alertBox('err', d.error || 'Job failed.'));
    jobId = j.id; const r = d.result;
    show('#bjob', alertBox('ok', `Predicted ${r.n_predicted} of ${r.n_input} rows with ${r.model_version} (${r.model_status}). Rejected: ${r.n_rejected}. History saved: ${r.history_saved}.`),
      h('div', { class: 'actions' }, h('a', { class: 'btn primary', href: `/api/jobs/${jobId}/download/predictions`, text: 'Download predictions CSV' }), h('a', { class: 'btn', href: `/api/jobs/${jobId}/download/rejected`, text: 'Download rejected rows' })));
    page = 1; loadPage();
  } catch (e) { show('#bjob', alertBox('err', e.message)); } finally { $('#brun').disabled = !(dsId && model && model.state === 'validated'); }
});
async function loadPage() {
  if (!jobId) return; const q = new URLSearchParams({ page, page_size: 25 }); if ($('#f-risk').value) q.set('risk_level', $('#f-risk').value); if ($('#f-class').value) q.set('predicted_class', $('#f-class').value);
  try {
    const d = await api(`/api/jobs/${jobId}/preview?` + q), keep = ['row_id', 'timestamp', 'latitude', 'longitude', 'predicted_class', 'probability', 'risk_level', 'imputed_inputs', 'out_of_region'].filter(c => d.columns.includes(c)), idx = keep.map(c => d.columns.indexOf(c));
    show('#bres', d.rows.length ? table(keep.map((c, i) => ({ label: c, render: r => c === 'risk_level' ? riskBadge(r[idx[i]]) : String(r[idx[i]] ?? '—') })), d.rows) : empty('No rows match the filters.'));
    $('#bpg').textContent = `Page ${d.page} · ${d.total} rows`; $('#bprev').disabled = page <= 1; $('#bnext').disabled = page * d.page_size >= d.total;
  } catch (e) { show('#bres', alertBox('err', e.message)); }
}
$('#bprev').addEventListener('click', () => { page--; loadPage(); }); $('#bnext').addEventListener('click', () => { page++; loadPage(); });
['#f-risk', '#f-class'].forEach(s => $(s).addEventListener('change', () => { page = 1; loadPage(); }));
if (qs0.get('dataset')) setTimeout(() => { const t = document.querySelectorAll('.tabs button')[1]; if (t) t.click(); }, 0);
