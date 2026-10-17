'use strict';
const loc = initLocation((p) => { $('#to-pred').href = `/predictions?lat=${p.lat.toFixed(5)}&lon=${p.lon.toFixed(5)}` + (p.name ? '&name=' + encodeURIComponent(p.name) : ''); });
const cfgW = JSON.parse($('#cfg').textContent);
const KIND = { recent_model_analysis: 'recent model analysis', historical_reanalysis: 'reanalysis', forecast: 'forecast', current_model_analysis: 'current (model analysis)', unavailable: 'unavailable' };
let busy = false;
function tile(label, f, fmt) { const ok = f && f.status === 'available'; return h('div', { class: 'tile' + (ok ? '' : ' na') }, h('div', { class: 'label', text: label }), h('div', { class: 'value', text: ok ? fmt(f.value) : 'Not reported' })); }
async function getWeather() {
  if (busy) return; const p = loc.point(); if (!loc.valid(p)) return show('#w', alertBox('err', 'Enter a valid location (a place from search, or latitude −90…90 and longitude −180…180).'));
  busy = true; $('#get').disabled = true; show('#status', h('span', {}, h('span', { class: 'spin' }), 'Requesting…')); const t0 = new Date();
  try {
    // fresh=true: every click is a new provider request (the server bypasses its cache); we never reuse a previous response silently.
    const r = await api(`/api/weather?lat=${p.lat}&lon=${p.lon}&fresh=true` + (p.name ? '&name=' + encodeURIComponent(p.name) : '')), w = r.weather, c = w.current, f = c.fields;
    show('#status', w.stale ? badge('stale', 'stale data') : badge('available', 'live'), ' ', h('span', { class: 'small muted', text: 'retrieved ' + t0.toLocaleTimeString() }));
    const hero = h('div', { class: 'wx-hero' }, weatherIcon(c.condition.icon), h('div', {}, h('div', { class: 'wx-temp', text: f.temperature_c.status === 'available' ? Math.round(f.temperature_c.value * 10) / 10 + '°C' : 'Not reported' }),
      h('div', { style: 'font-weight:700;font-size:1.1rem', text: c.condition.label }), h('div', { class: 'muted', text: f.apparent_temperature_c.status === 'available' ? `Feels like ${f.apparent_temperature_c.value}°C` : 'Feels-like temperature not reported' })));
    const loc2 = r.location, name = loc2.name || 'Selected coordinates';
    show('#w', w.stale ? alertBox('warn', `The weather provider could not be reached. Showing the last successful result, retrieved ${fmtTime(w.last_updated_utc)}. It may be out of date.`) : null,
      h('p', {}, h('strong', { text: name }), h('span', { class: 'muted mono', text: `  ${loc2.latitude.toFixed(4)}, ${loc2.longitude.toFixed(4)}` })), hero,
      h('div', { class: 'wx-tiles' }, tile('Relative humidity', f.relative_humidity_pct, v => v + ' %'),
        h('div', { class: 'tile' + (f.wind_speed_kmh.status === 'available' ? '' : ' na') }, h('div', { class: 'label', text: 'Wind' }), h('div', { class: 'value', text: f.wind_speed_kmh.status === 'available' ? `${f.wind_speed_kmh.value} km/h` + (c.wind_direction_label ? ` ${c.wind_direction_label} (${f.wind_direction_deg.value}°)` : '') : 'Not reported' })),
        tile('Precipitation now', f.precipitation_mm, v => v + ' mm'), tile('Rain now', f.rain_mm, v => v + ' mm'),
        h('div', { class: 'tile' + (r.elevation.status === 'available' ? '' : ' na') }, h('div', { class: 'label', text: 'Elevation' }), h('div', { class: 'value', text: r.elevation.status === 'available' ? r.elevation.value_m + ' m' : 'Not reported' }))),
      h('dl', { class: 'kv small', style: 'margin-top:var(--s4)' }, h('dt', { text: 'Observation time' }), h('dd', { text: c.observation_time_utc ? `${fmtTime(c.observation_time_utc)} (provider timestamp ${c.observation_time_utc})` : 'not provided' }),
        h('dt', { text: 'Retrieved' }), h('dd', { text: `${fmtTime(w.last_updated_utc)} (this request started ${t0.toLocaleTimeString()})` }), h('dt', { text: 'Kind' }), h('dd', { text: 'Open-Meteo current model analysis; not a station observation' }),
        h('dt', { text: 'Model coverage' }), h('dd', {}, coverageNote(r.coverage.eligible, r.coverage.region))),
      r.elevation.error ? h('p', { class: 'small muted', text: 'Elevation: ' + r.elevation.error }) : null);
    const win = w.windows;
    show('#windows', table([{ label: 'Window', key: 'q' }, { label: 'Rainfall', key: 'v' }, { label: 'Kind', render: x => badge(x.k === 'unavailable' ? 'unavailable' : 'info', KIND[x.k] || x.k) }],
      [['Last 1 h', 'rainfall_1h_mm'], ['Last 24 h', 'rainfall_24h_mm'], ['Last 72 h', 'rainfall_72h_mm']].map(([q, k]) => ({ q, v: win[k].status === 'available' ? win[k].value_mm + ' mm' : 'unavailable', k: win[k].kind })).concat([{ q: 'Next 24 h (forecast)', v: w.forecast_next_24h_total.status === 'available' ? w.forecast_next_24h_total.value_mm + ' mm' : 'unavailable', k: w.forecast_next_24h_total.kind }]), { wrap: true }));
    const el = h('div', { class: 'chart' }); show('#chart', el); const fc = w.forecast_next_24h;
    plot(el, [{ type: 'bar', x: fc.map(x => x.time_utc.slice(11)), y: fc.map(x => x.precipitation_mm), marker: { color: '#1d6fe0' } }], { yaxis: { title: 'mm per hour (forecast)' }, xaxis: { title: 'Hour (UTC)' } });
  } catch (e) {
    show('#status', badge('failed', 'failed'));
    const retry = h('button', { class: 'btn sm', type: 'button', text: 'Retry' }); retry.addEventListener('click', getWeather);
    show('#w', alertBox('err', e.message + (e.code === 'weather_unavailable' || e.status === 503 ? ' No weather values are shown; nothing has been assumed.' : '')), h('div', { class: 'actions' }, retry)); show('#windows', empty('Not loaded.')); show('#chart', empty('Not loaded.'));
  } finally { busy = false; $('#get').disabled = false; }
}
$('#get').addEventListener('click', getWeather);
