'use strict';
(async () => {
  const id = $('#detail').dataset.id;
  try {
    const d = await api('/api/history/' + id), r = d.record, src = (r.inputs && r.inputs._sources) || {}, inputs = Object.entries(r.inputs || {}).filter(([k]) => k !== '_sources');
    show('#detail', h('section', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: placeLabel(r) }), riskBadge(r.risk_level)),
      h('dl', { class: 'kv' }, h('dt', { text: 'Predicted class' }), h('dd', { text: r.predicted_class ? '1 (waterlogging estimated)' : '0 (not estimated)' }), h('dt', { text: 'Model probability' }), h('dd', { text: pct(r.probability) }),
        h('dt', { text: 'Coordinates' }), h('dd', { class: 'mono', text: `${r.latitude.toFixed(5)}, ${r.longitude.toFixed(5)}` }), h('dt', { text: 'Observation time' }), h('dd', { text: r.ts ? fmtTime(r.ts) : '—' }),
        h('dt', { text: 'Saved' }), h('dd', { text: fmtTime(r.created_at) + ' · source: ' + r.source }), h('dt', { text: 'Model' }), h('dd', {}, badge(r.model_status === 'validated' ? 'validated' : 'experimental', r.model_status), ' ', h('span', { class: 'mono', text: r.model_version }))),
      r.risk_level === 'pending_validation' ? alertBox('info', 'No risk category: the model had no validated thresholds when this prediction was made.') : null),
      h('section', { class: 'card', style: 'margin-top:var(--s4)' }, h('h2', { style: 'margin-bottom:var(--s3)', text: 'Model inputs and where they came from' }),
        inputs.length ? table([{ label: 'Input', key: 'k' }, { label: 'Value', render: x => x.v == null ? 'missing (imputed)' : String(x.v) }, { label: 'Source', render: x => src[x.k] || (r.source === 'batch' ? 'uploaded file' : 'not recorded') }], inputs.map(([k, v]) => ({ k, v }))) : empty('No inputs were stored.')),
      h('p', { class: 'small muted', style: 'margin-top:var(--s4)', text: d.disclaimer }));
  } catch (e) { show('#detail', alertBox('err', e.status === 404 ? 'This prediction record no longer exists (it may have been deleted or expired).' : e.message)); }
})();
