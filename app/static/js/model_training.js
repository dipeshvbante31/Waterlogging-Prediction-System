'use strict';
show('#tok', adminTokenField()); let selected = null;
async function loadDatasets() {
  try {
    const d = await api('/api/datasets?kind=train&status=validated'), sel = $('#dataset');
    sel.replaceChildren(h('option', { value: '', text: d.items.length ? 'Select a training dataset…' : 'No validated training datasets uploaded' }), ...d.items.map(x => h('option', { value: x.dataset_id, text: `${x.name} · ${x.n_rows} rows · ${x.dataset_id.slice(0, 8)}` })));
    sel.addEventListener('change', gate); const q = new URLSearchParams(location.search).get('dataset'); if (q && d.items.some(i => i.dataset_id === q)) sel.value = q; selected = d.items; gate();
  } catch (e) { show('#train-gate', alertBox('err', e.message)); }
}
function gate() {
  const ok = !!$('#dataset').value; $('#train').disabled = !ok;
  show('#train-gate', ok ? null : alertBox('warn', selected && selected.length ? 'Select a validated training dataset to enable training.' : 'There is no validated training dataset. Upload one first.'), ok ? null : h('p', {}, h('a', { class: 'btn sm', href: '/upload', text: 'Upload CSV' })));
}
$('#train').addEventListener('click', async () => {
  const prov = $('#prov').value.trim(); if (!prov) return show('#train-out', alertBox('err', 'Label provenance is required: say where the 0/1 labels come from.'));
  const verified = document.querySelector('input[name=origin]:checked').value === 'verified', excl = [...document.querySelectorAll('.excl:checked')].map(x => x.value);
  $('#train').disabled = true; loading('#train-out', 'Submitting…');
  try {
    const j = await api('/api/train', { admin: true, json: { dataset_id: $('#dataset').value, split_strategy: $('#split').value, label_provenance: prov, provenance_attested: verified, exclude_features: excl } });
    const done = await pollJob(j.id, x => loading('#train-out', 'Training job ' + x.status + '…'));
    if (done.status !== 'succeeded') show('#train-out', alertBox('err', done.error || 'Training did not complete.'));
    else if (done.result.outcome === 'no_eligible_model') show('#train-out', alertBox('warn', 'No model saved: ' + done.result.reason));
    else { const r = done.result; show('#train-out', alertBox(r.status === 'validated' ? 'ok' : 'warn', `${r.status === 'validated' ? 'Validated' : 'Experimental (cannot be activated)'}: ${r.reason}`), h('p', { class: 'small', text: `Saved ${r.version}. ${r.active ? 'It is now the active model.' : (r.status === 'validated' ? 'Activate it in the registry below to use it.' : '')}` })); }
    await Promise.all([loadRegistry(), loadBanner()]);
  } catch (e) { show('#train-out', alertBox('err', e.message)); } finally { gate(); }
});
async function loadRegistry() {
  try {
    const d = await api('/api/models');
    show('#registry', d.versions.length ? table([{ label: 'Version', render: v => h('span', { class: 'mono', text: v.version }) }, { label: 'Status', render: v => badge(v.status) }, { label: 'Active', render: v => v.active ? badge('ok', 'active') : '—' },
      { label: 'Algorithm', key: 'selected_model' }, { label: 'Rows', key: 'n_rows' }, { label: 'Split', key: 'split_strategy' }, { label: 'Test PR-AUC', render: v => num(v.test_pr_auc) }, { label: 'Created', render: v => new Date(v.created_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) },
      { label: '', render: v => h('span', { class: 'actions', style: 'margin:0' }, (() => { const b = h('button', { class: 'btn sm', type: 'button', text: 'Details' }); b.addEventListener('click', () => detail(v.version)); return b; })(),
        (() => { const b = h('button', { class: 'btn sm primary', type: 'button', text: 'Activate', disabled: (v.status !== 'validated' || v.active) ? 'disabled' : null, title: v.status !== 'validated' ? 'Failed gates: ' + v.failed_gates.join(', ') : null });
          b.addEventListener('click', async () => { try { await api('/api/models/' + v.version + '/activate', { method: 'POST', admin: true }); await Promise.all([loadRegistry(), loadBanner()]); } catch (e) { show('#model-detail', alertBox('err', e.message)); } }); return b; })()) }], d.versions, { wrap: true, stack: true })
      : empty('No model versions yet. Train a model above.'));
  } catch (e) { show('#registry', alertBox('err', e.message)); }
}
async function detail(version) {
  loading('#model-detail');
  try {
    const m = await api('/api/models/' + version), t = m.test_metrics, cm = t.confusion_matrix, c1 = h('div', { class: 'chart' }), c2 = h('div', { class: 'chart' }), c3 = h('div', { class: 'chart' });
    show('#model-detail', h('h3', { text: 'Evaluation · ' + m.version }), alertBox(m.status === 'validated' ? 'ok' : 'warn', m.status_reason),
      h('dl', { class: 'kv small' }, h('dt', { text: 'Training data' }), h('dd', { text: `${m.data.n_rows} rows · sha256 ${m.data.sha256.slice(0, 12)}…` }), h('dt', { text: 'Inputs used' }), h('dd', { text: m.input_columns.join(', ') }), h('dt', { text: 'Split strategy' }), h('dd', { text: m.split.strategy }),
        h('dt', { text: 'Evaluated' }), h('dd', { text: fmtTime(m.created_at) + ' (held-out test split)' }), h('dt', { text: 'Label provenance' }), h('dd', { text: m.label_provenance || 'not provided' })),
      h('div', { class: 'grid cols-2', style: 'margin-top:var(--s4)' }, h('div', {}, table([{ label: 'Test metric', key: 'k' }, { label: 'Value', key: 'v' }], [{ k: 'PR-AUC', v: num(t.pr_auc) }, { k: 'Baseline PR-AUC (prevalence)', v: num(t.prevalence) }, { k: 'ROC-AUC', v: num(t.roc_auc) }, { k: 'Precision', v: num(t.precision) }, { k: 'Recall', v: num(t.recall) }, { k: 'F1', v: num(t.f1) }, { k: 'Brier (calibrated / raw)', v: num(t.brier) + ' / ' + num(m.test_brier_uncalibrated) }, { k: 'Test rows / positives', v: t.n + ' / ' + t.n_positive }]),
        h('p', { class: 'hint', text: 'Metrics come from the test split only. Accuracy is deliberately not reported.' })), c1),
      h('div', { class: 'grid cols-2 section' }, c2, c3), h('h3', { class: 'small', text: 'Validation gates' }), table([{ label: 'Gate', key: 'name' }, { label: 'Passed', render: g => badge(g.passed ? 'ok' : 'err', g.passed ? 'yes' : 'no') }, { label: 'Detail', key: 'detail' }], m.gates), m.caveats.map(c => h('p', { class: 'small muted', text: c })));
    plot(c1, [{ type: 'heatmap', z: [[cm.tn, cm.fp], [cm.fn, cm.tp]], x: ['Pred 0', 'Pred 1'], y: ['True 0', 'True 1'], colorscale: 'Blues', showscale: false, text: [[cm.tn, cm.fp], [cm.fn, cm.tp]], texttemplate: '%{text}' }], { title: { text: 'Confusion matrix (test)', font: { size: 13 } }, yaxis: { autorange: 'reversed' } });
    plot(c2, [{ type: 'bar', x: m.candidates.map(c => c.model), y: m.candidates.map(c => c.val_pr_auc), marker: { color: m.candidates.map(c => c.model === m.selected_model ? '#1d6fe0' : '#8aa0bb') } }], { title: { text: 'Candidates: validation PR-AUC (baseline ' + num(m.split.validation.n_positive / m.split.validation.n) + ')', font: { size: 13 } } });
    const cal = m.calibration; if (cal.n_bins) plot(c3, [{ type: 'scatter', mode: 'lines', x: [0, 1], y: [0, 1], line: { dash: 'dot' }, name: 'perfect' }, { type: 'scatter', mode: 'lines+markers', x: cal.mean_predicted, y: cal.fraction_positive, name: 'model' }], { title: { text: 'Calibration (test)', font: { size: 13 } }, xaxis: { title: 'Predicted', range: [0, 1] }, yaxis: { title: 'Observed', range: [0, 1] } }); else show(c3, empty('Too few positives for a calibration curve.'));
  } catch (e) { show('#model-detail', alertBox('err', e.message)); }
}
loadDatasets(); loadRegistry();
