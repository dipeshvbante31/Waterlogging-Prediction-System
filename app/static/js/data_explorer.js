'use strict';
const STEP = 25; let current = null, offset = 0;
(async () => {
  const sel = $('#ds'); let items = [];
  try { items = (await api('/api/datasets?limit=50')).items; } catch (e) { return show('#explorer', alertBox('err', e.message)); }
  sel.replaceChildren(h('option', { value: '', text: items.length ? 'Select a dataset…' : 'No datasets uploaded yet' }), ...items.map(x => h('option', { value: x.dataset_id, text: `${x.name} · ${x.kind} · ${x.n_rows} rows · ${x.status}` })));
  if (!items.length) return show('#explorer', empty('No datasets stored. Datasets expire after the retention period.', h('a', { href: '/upload', text: 'Upload a CSV' })));
  sel.addEventListener('change', () => { offset = 0; if (sel.value) load(sel.value); });
  const q = new URLSearchParams(location.search).get('dataset'); if (q && items.some(i => i.dataset_id === q)) { sel.value = q; load(q); }
})();
function tile(label, value) { return h('div', { class: 'tile' }, h('div', { class: 'label', text: label }), h('div', { class: 'value', text: String(value) })); }
async function load(id) {
  current = id; loading('#explorer', 'Analysing dataset…');
  try {
    const d = await api(`/api/datasets/${id}/explore?offset=${offset}&limit=${STEP}`), v = d.validation, parts = [];
    parts.push(h('section', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: d.name }), badge(d.status === 'validated' ? 'validated' : 'failed', d.status === 'validated' ? 'passes validation' : 'does not pass validation')),
      h('div', { class: 'wx-tiles' }, tile('Rows', d.n_rows), tile('Columns', d.n_columns), tile('Type', d.kind === 'train' ? 'Training (labelled)' : 'Prediction (unlabelled)'), tile('Valid rows', v.n_rows_valid), tile('Rejected rows', v.n_rows_rejected)),
      v.time_range ? h('p', { class: 'small muted', style: 'margin-top:.75rem', text: 'Time range (UTC): ' + v.time_range.join(' → ') }) : null));
    v.fatal_errors.forEach(x => parts.push(alertBox('err', x))); v.warnings.slice(0, 8).forEach(x => parts.push(alertBox('warn', x)));
    if (v.class_balance) parts.push(alertBox(v.class_balance.positive_rate < .05 ? 'warn' : 'info', `Label balance: ${v.class_balance.positive} positive / ${v.class_balance.negative} negative (${pct(v.class_balance.positive_rate)} positive).` + (v.class_balance.positive_rate < .05 ? ' Strong imbalance: accuracy would be misleading; PR-AUC is used.' : '')));
    parts.push(h('div', { class: 'grid cols-2' },
      h('section', { class: 'card' }, h('h2', { style: 'margin-bottom:var(--s3)', text: 'Required columns' }), table([{ label: 'Column', key: 'name' }, { label: 'Present', render: r => badge(r.present ? 'available' : 'failed', r.present ? 'yes' : 'missing') }], d.required_columns)),
      h('section', { class: 'card' }, h('h2', { style: 'margin-bottom:var(--s3)', text: 'Rejected rows by reason' }), Object.keys(v.rejection_counts).length ? table([{ label: 'Reason', key: 'k' }, { label: 'Rows', key: 'n' }], Object.entries(v.rejection_counts).map(([k, n]) => ({ k, n }))) : h('p', { class: 'muted', text: 'No rows were rejected.' }),
        v.n_rows_rejected ? h('div', { class: 'actions' }, h('a', { class: 'btn sm', href: `/api/datasets/${id}/rejected.csv`, text: 'Download rejected rows' })) : null)));
    parts.push(h('section', { class: 'card' }, h('h2', { style: 'margin-bottom:var(--s3)', text: 'Column quality' }), table([{ label: 'Column', key: 'name' }, { label: 'Recognised as', render: c => c.recognised_as || h('span', { class: 'muted', text: 'ignored' }) }, { label: 'Type', key: 'type' },
      { label: 'Missing', render: c => `${c.missing} (${c.missing_pct}%)` }, { label: 'Invalid rows', key: 'invalid_rows' }], d.columns)));
    parts.push(h('section', { class: 'card' }, h('h2', { style: 'margin-bottom:var(--s3)', text: 'Descriptive statistics (valid rows)' }), d.statistics.length ? table([{ label: 'Feature', key: 'feature' }, { label: 'Unit', key: 'unit' }, { label: 'Count', key: 'count' }, ...['mean', 'std', 'min', '25%', '50%', '75%', 'max'].map(k => ({ label: k, render: r => num(r[k], 3) }))], d.statistics) : empty('No valid numeric rows to summarise.')));
    const p = d.preview;
    parts.push(h('section', { class: 'card' }, h('h2', { style: 'margin-bottom:var(--s3)', text: 'Preview (as uploaded)' }), p.rows.length ? table(p.columns.map((c, i) => ({ label: c, render: r => String(r[i] ?? '') })), p.rows) : empty('No rows in this range.'),
      h('div', { class: 'actions' }, (() => { const b = h('button', { class: 'btn sm', type: 'button', text: 'Previous', disabled: offset === 0 ? 'disabled' : null }); b.addEventListener('click', () => { offset = Math.max(0, offset - STEP); load(id); }); return b; })(),
        (() => { const b = h('button', { class: 'btn sm', type: 'button', text: 'Next', disabled: offset + STEP >= d.n_rows ? 'disabled' : null }); b.addEventListener('click', () => { offset += STEP; load(id); }); return b; })(),
        h('span', { class: 'muted small', text: `Rows ${offset + 1}–${Math.min(offset + STEP, d.n_rows)} of ${d.n_rows}` }))));
    const next = h('div', { class: 'actions' }); if (d.status === 'validated') next.append(d.kind === 'train' ? h('a', { class: 'btn primary', href: '/model-training?dataset=' + id, text: 'Continue to Model Training' }) : h('a', { class: 'btn primary', href: '/predictions?dataset=' + id, text: 'Run batch prediction' }));
    parts.push(next); show('#explorer', h('div', { class: 'stack' }, parts));
  } catch (e) { show('#explorer', alertBox('err', e.message)); }
}
