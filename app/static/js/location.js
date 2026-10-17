'use strict';
/* Shared location input for Predictions and Live Weather: place search + validated coordinates. Text only; no maps. */
const PIN = '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s7-6.2 7-11a7 7 0 1 0-14 0c0 4.8 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/></svg>';
function coverageNote(inside, regionName) {
  return inside ? h('p', { class: 'small' }, badge('available', 'inside model region'), ' This point is within the configured coverage area.')
    : alertBox('warn', 'This point is outside the configured model region. Weather lookup still works, but a model trained for ' + (regionName || 'the configured region') + ' must not be applied here.');
}
function initLocation(onChange) {
  const cfg = JSON.parse($('#cfg').textContent), q = new URLSearchParams(location.search), b = cfg.bbox; let name = q.get('name') || null;
  const inside = (la, lo) => la >= b[0] && la <= b[2] && lo >= b[1] && lo <= b[3];
  const point = () => ({ lat: Number($('#lat').value), lon: Number($('#lon').value), name });
  const valid = p => isFinite(p.lat) && isFinite(p.lon) && Math.abs(p.lat) <= 90 && Math.abs(p.lon) <= 180 && $('#lat').value.trim() !== '' && $('#lon').value.trim() !== '';
  function render() {
    const p = point(); if (!valid(p)) return show('#loc', alertBox('err', 'Enter a latitude between −90 and 90 and a longitude between −180 and 180.'));
    const card = h('div', { class: 'loc-card' }); card.insertAdjacentHTML('afterbegin', PIN);
    card.append(h('div', {}, h('strong', { text: name || 'Selected coordinates' }), h('div', { class: 'small muted mono', text: `${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}` })));
    show('#loc', card); if (onChange) onChange(p, inside(p.lat, p.lon));
  }
  function set(la, lo, nm) { $('#lat').value = (+la).toFixed(5); $('#lon').value = (+lo).toFixed(5); name = nm || null; render(); }
  ['#lat', '#lon'].forEach(s => $(s).addEventListener('change', () => { name = null; render(); }));
  $('#find').addEventListener('click', async () => {
    const term = $('#q').value.trim(); if (term.length < 2) return show('#hits', alertBox('warn', 'Type at least 2 characters.'));
    loading('#hits', 'Searching…');
    try {
      const r = await api('/api/geocode?q=' + encodeURIComponent(term));
      if (!r.results.length) return show('#hits', empty('No matching places. Try a different spelling or enter coordinates.'));
      show('#hits', h('ul', { class: 'results-list' }, r.results.map(x => { const label = [x.name, x.admin1, x.country].filter(Boolean).join(', '); const bt = h('button', { class: 'btn sm', type: 'button', text: label });
        bt.addEventListener('click', () => { set(x.latitude, x.longitude, label); show('#hits'); }); return h('li', {}, bt, x.coverage_eligible ? badge('available', 'in model region') : badge('warn', 'outside model region')); })));
    } catch (e) { show('#hits', alertBox('err', e.message)); }
  });
  $('#q').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); $('#find').click(); } });
  if (q.get('lat') && q.get('lon') && isFinite(+q.get('lat'))) { $('#lat').value = (+q.get('lat')).toFixed(5); $('#lon').value = (+q.get('lon')).toFixed(5); }
  render();
  return { set, point, valid, inside };
}
