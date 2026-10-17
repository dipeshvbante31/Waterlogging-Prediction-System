'use strict';
const drop = $('#drop'), input = $('#file'), MAX = Number(drop.dataset.maxMb) * 1024 * 1024; let chosen = null;
(async () => { try { const s = await api('/api/schema');
  show('#schema', table([{ label: 'Field', render: f => h('span', { class: 'mono', text: f.name }) }, { label: 'Unit', key: 'unit' }, { label: 'Required', render: f => f.required ? 'yes' : 'optional' }], s.features.concat([{ name: s.target, unit: '0 / 1 (training only)', required: false }]), { wrap: true }));
} catch (e) { show('#schema', alertBox('err', e.message)); } })();
function pick(f) {
  chosen = null; $('#go').disabled = true; show('#result');
  if (!f) { $('#picked').textContent = ''; return; }
  if (!/\.csv$/i.test(f.name)) { $('#picked').textContent = ''; return show('#result', alertBox('err', 'Only .csv files are accepted.')); }
  if (f.size > MAX) { $('#picked').textContent = ''; return show('#result', alertBox('err', `This file is ${(f.size / 1048576).toFixed(1)} MB; the limit is ${drop.dataset.maxMb} MB.`)); }
  if (f.size === 0) return show('#result', alertBox('err', 'The file is empty.'));
  chosen = f; $('#picked').textContent = `Selected: ${f.name} (${(f.size / 1024).toFixed(0)} KB)`; $('#go').disabled = false;
}
input.addEventListener('change', () => pick(input.files && input.files[0]));
['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('drag'); }));
['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('drag'); }));
drop.addEventListener('drop', e => pick(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]));
$('#go').addEventListener('click', async () => {
  if (!chosen) return; const kind = document.querySelector('input[name=kind]:checked').value; $('#go').disabled = true; $('#prog').hidden = false; $('#bar').style.width = '0%'; $('#ptext').textContent = 'Uploading…'; show('#result');
  try {
    const d = await xhrUpload('/api/datasets?kind=' + kind, chosen, f => { const p = Math.round(100 * f); $('#bar').style.width = p + '%'; $('#ptext').textContent = p < 100 ? `Uploading… ${p}%` : 'Validating…'; $('#prog .progress').setAttribute('aria-valuenow', p); });
    $('#bar').style.width = '100%'; $('#ptext').textContent = 'Done.'; const r = d.report, parts = [];
    parts.push(alertBox(r.ok ? 'ok' : 'err', r.ok ? `Upload accepted. ${r.n_rows_valid} of ${r.n_rows_input} rows are valid.` : 'The file was received but cannot be used yet.'));
    parts.push(h('dl', { class: 'kv' }, h('dt', { text: 'Dataset ID' }), h('dd', { class: 'mono', text: d.dataset_id }), h('dt', { text: 'Rows' }), h('dd', { text: `${r.n_rows_input} uploaded · ${r.n_rows_valid} valid · ${r.n_rows_rejected} rejected` }), h('dt', { text: 'Expires' }), h('dd', { text: fmtTime(d.expires_at) })));
    r.fatal_errors.forEach(x => parts.push(alertBox('err', x))); r.warnings.slice(0, 6).forEach(x => parts.push(alertBox('warn', x)));
    const missing = r.fatal_errors.filter(x => /Missing required column/.test(x)); if (missing.length) parts.push(alertBox('info', 'Add the missing columns (see “Expected columns”) or map unusual names to them before re-uploading. Columns are never guessed.'));
    const rc = Object.entries(r.rejection_counts || {}); if (rc.length) parts.push(table([{ label: 'Rejected because', key: 'k' }, { label: 'Rows', key: 'n' }], rc.map(([k, n]) => ({ k, n }))), h('div', { class: 'actions' }, h('a', { class: 'btn sm', href: `/api/datasets/${d.dataset_id}/rejected.csv`, text: 'Download rejected rows' })));
    const act = h('div', { class: 'actions' }, h('a', { class: 'btn', href: '/data-explorer?dataset=' + d.dataset_id, text: 'Explore this dataset' }));
    if (r.ok) act.append(kind === 'train' ? h('a', { class: 'btn primary', href: '/model-training?dataset=' + d.dataset_id, text: 'Train a model' }) : h('a', { class: 'btn primary', href: '/predictions?dataset=' + d.dataset_id, text: 'Run batch prediction' }));
    parts.push(act); show('#result', parts);
  } catch (e) { $('#ptext').textContent = 'Upload failed.'; show('#result', alertBox('err', e.message)); } finally { $('#go').disabled = !chosen; }
});
