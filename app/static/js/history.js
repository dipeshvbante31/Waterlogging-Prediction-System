'use strict';
show('#tok', adminTokenField());
tabs($('#hist-root'), { Predictions: $('#p-panel'), Jobs: $('#j-panel') });
const SZ = 25; let pp = 0, jp = 0, timer = null;
async function loadP() {
  loading('#p-body'); const q = new URLSearchParams({ limit: SZ, offset: pp * SZ }); if ($('#risk').value) q.set('risk_level', $('#risk').value); if ($('#hsrc').value) q.set('source', $('#hsrc').value); if ($('#hq').value.trim()) q.set('q', $('#hq').value.trim());
  try { const d = await api('/api/history?' + q);
    show('#p-body', d.items.length ? table([{ label: 'When', render: x => fmtTime(x.created_at) }, { label: 'Location', render: x => placeLabel(x) }, { label: 'Source', key: 'source' }, { label: 'Class', key: 'predicted_class' }, { label: 'Probability', render: x => pct(x.probability) }, { label: 'Risk', render: x => riskBadge(x.risk_level) },
      { label: 'Model', render: x => `${x.model_status} · ${x.model_version.slice(0, 20)}` }, { label: '', render: x => h('a', { href: '/history/' + x.id, text: 'Details' }) }], d.items, { wrap: true, stack: true }) : empty('No saved predictions match. Tick “Save to history” when predicting.'));
    $('#p-pg').textContent = `${d.total} record(s) · page ${pp + 1}`; $('#p-prev').disabled = pp === 0; $('#p-next').disabled = (pp + 1) * SZ >= d.total;
  } catch (e) { show('#p-body', alertBox('err', e.message)); }
}
async function loadJ() {
  loading('#j-body'); const q = new URLSearchParams({ limit: SZ, offset: jp * SZ }); if ($('#jtype').value) q.set('type', $('#jtype').value);
  try { const d = await api('/api/activity?' + q);
    show('#j-body', d.items.length ? table([{ label: 'Started', render: x => fmtTime(x.created_at) }, { label: 'Type', render: x => x.type === 'train' ? 'Training' : 'Batch prediction' }, { label: 'Status', render: x => badge(x.status) }, { label: 'Finished', render: x => fmtTime(x.finished_at) },
      { label: 'Outcome', render: x => x.error ? x.error.slice(0, 80) : (x.result ? (x.result.outcome ? `${x.result.outcome} (${x.result.status || ''})` : `${x.result.n_predicted} predicted, ${x.result.n_rejected} rejected`) : '—') }, { label: 'Files', render: x => x.downloads.length ? h('a', { href: `/api/jobs/${x.id}/download/predictions`, text: 'predictions.csv' }) : '—' }], d.items, { wrap: true, stack: true }) : empty('No jobs yet.'));
    $('#j-pg').textContent = `${d.total} job(s) · page ${jp + 1}`; $('#j-prev').disabled = jp === 0; $('#j-next').disabled = (jp + 1) * SZ >= d.total;
  } catch (e) { show('#j-body', alertBox('err', e.message)); }
}
$('#p-prev').addEventListener('click', () => { pp--; loadP(); }); $('#p-next').addEventListener('click', () => { pp++; loadP(); });
$('#j-prev').addEventListener('click', () => { jp--; loadJ(); }); $('#j-next').addEventListener('click', () => { jp++; loadJ(); });
['#risk', '#hsrc'].forEach(s => $(s).addEventListener('change', () => { pp = 0; loadP(); })); $('#jtype').addEventListener('change', () => { jp = 0; loadJ(); });
$('#hq').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { pp = 0; loadP(); }, 300); });
$('#clear').addEventListener('click', async () => { if (!confirm('Delete ALL stored prediction history? This cannot be undone.')) return;
  try { const r = await api('/api/history', { method: 'DELETE', admin: true }); pp = 0; await loadP(); show('#clear-msg', alertBox('ok', `Deleted ${r.deleted} record(s).`)); } catch (e) { show('#clear-msg', alertBox('err', e.message)); } });
loadP(); loadJ();
