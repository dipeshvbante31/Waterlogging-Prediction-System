'use strict';
(async () => {
  let d;
  try { d = await api('/api/dashboard'); } catch (e) { return show('#stats', alertBox('err', 'Could not load dashboard data: ' + e.message)); }
  const none = '—';
  $('#s-total').textContent = d.total_records; $('#s-total-sub').textContent = d.total_records ? 'Saved predictions in History' : 'No predictions saved yet';
  $('#s-high').textContent = d.high_risk; $('#s-high-sub').textContent = d.high_risk_denominator ? `of ${d.high_risk_denominator} with a risk category` : 'No categorised predictions yet';
  $('#s-avg').textContent = d.average_probability == null ? none : pct(d.average_probability);
  show('#s-avg-sub', d.average_probability == null ? 'Needs predictions from a validated model' : `Mean of ${d.average_probability_n} · overall `, d.overall_level ? riskBadge(d.overall_level) : (d.average_probability == null ? null : 'level n/a'));
  if (d.latest) { $('#s-latest').textContent = placeLabel(d.latest); show('#s-latest-sub', fmtTime(d.latest.created_at) + ' · ', riskBadge(d.latest.risk_level)); $('#updated').textContent = 'Last prediction: ' + fmtTime(d.latest.created_at) + '.'; }
  else { $('#s-latest').textContent = 'None yet'; $('#s-latest-sub').textContent = 'Make your first prediction'; }

  const short = t => new Date(t).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
  show('#d-recent', d.recent.length ? table([{ label: 'Location', render: r => h('span', {}, h('strong', { text: placeLabel(r) }), h('br'), h('span', { class: 'small muted', text: short(r.created_at) })) }, { label: 'Risk', render: r => riskBadge(r.risk_level) },
    { label: 'Status', render: r => badge(r.model_status === 'validated' ? 'validated' : 'experimental', r.model_status) }, { label: 'Details', render: r => h('a', { href: '/history/' + r.id, text: 'View details' }) }], d.recent, { wrap: true, stack: true })
    : empty('No predictions yet.', h('a', { href: '/predictions', text: 'Make your first prediction' })));

  const keys = Object.keys(d.distribution);
  if (!keys.length) show('#d-dist', empty('No predictions to chart yet. Saved predictions appear here.'));
  else {
    const el = h('div', { class: 'chart', style: 'min-height:220px' }), den = d.distribution_denominator;
    show('#d-dist', h('div', { class: 'donut' }, el, h('div', {}, keys.map(k => h('div', { class: 'legend-row' }, h('span', {}, h('i', { class: 'sw', style: 'background:' + RISK_COLOR[k] }), riskLabel(k)), h('strong', { text: `${d.distribution[k]} (${(100 * d.distribution[k] / den).toFixed(0)}%)` })))
      , h('p', { class: 'hint', text: `Share of ${den} saved prediction(s) in History.` }))));
    plot(el, [{ type: 'pie', hole: .62, labels: keys.map(riskLabel), values: keys.map(k => d.distribution[k]), marker: { colors: keys.map(k => RISK_COLOR[k]) }, textinfo: 'none', sort: false, hovertemplate: '%{label}: %{value}<extra></extra>' }],
      { showlegend: false, margin: { t: 5, r: 5, b: 5, l: 5 }, height: 220 });
  }
  show('#d-insights', d.insights.length ? d.insights.map(t => h('li', {}, h('span', { text: '•' }), h('span', { text: t }))) : h('li', { class: 'muted' }, 'Insights appear once predictions are saved or a model is trained.'));
  const m = d.active_model;
  show('#d-status', h('dl', { class: 'kv small' }, h('dt', { text: 'Active model' }), h('dd', {}, m ? [badge(m.status), ' ', h('span', { class: 'mono', text: m.version.slice(0, 22) })] : badge('not_trained', 'none validated')),
    h('dt', { text: 'Datasets' }), h('dd', { text: d.datasets_available + ' stored (temporary)' }), h('dt', { text: 'Weather' }), h('dd', { id: 'wx-status' }, h('span', { class: 'spin' }), 'checking…')));
  $('#defs').textContent = 'Definitions: ' + Object.values(d.definitions).join(' ');
  try {
    const w = await api('/api/weather/status');
    show('#wx-status', w.ok ? [badge(w.stale ? 'stale' : 'available', w.stale ? 'stale cache' : 'reachable'), ' last success ' + fmtTime(w.last_updated_utc)] : [badge('unavailable', 'unavailable'), ' ' + w.error]);
  } catch (e) { show('#wx-status', badge('unavailable', 'unknown')); }
})();
