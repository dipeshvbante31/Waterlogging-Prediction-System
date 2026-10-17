'use strict';
(async () => {
  let list; try { list = await api('/api/models'); } catch (e) { return show('#root', alertBox('err', e.message)); }
  if (!list.versions.length) { $('#ver').parentElement.hidden = true; return show('#root', empty('No model versions exist, so there is nothing to explain.', h('a', { href: '/data-models', text: 'Train a model' }))); }
  const sel = $('#ver'); list.versions.forEach(v => sel.append(h('option', { value: v.version, text: `${v.version.slice(0, 24)} · ${v.status}${v.active ? ' · active' : ''}` })));
  sel.value = list.active || list.versions[0].version;
  async function render() {
    loading('#root');
    try {
      const m = await api('/api/models/' + sel.value), imp = (m.permutation_importance || []).slice().reverse(), el = h('div', { class: 'chart' });
      show('#root', m.status === 'validated' ? null : alertBox('warn', 'This version is not validated; treat the ranking as exploratory.'),
        h('section', { class: 'card' }, h('h2', { text: 'Global importance: what the model relies on' }), el,
          h('p', { class: 'small muted', text: 'Permutation importance on the held-out test split: the drop in PR-AUC when one feature’s values are shuffled (mean ± std over repeats). Correlated features share importance. It measures reliance by this model, not causal influence.' })),
        h('div', { class: 'grid cols-2 section' }, h('section', { class: 'card' }, h('h2', { text: 'Individual predictions' }), h('p', { class: 'small', text: 'Each single prediction shows how replacing every input with its training median would change that prediction’s probability. It explains one result; it does not describe the model as a whole and is not a causal effect.' })),
          h('section', { class: 'card' }, h('h2', { text: 'Features used' }), h('p', { class: 'small', text: m.feature_columns.join(', ') }))));
      plot(el, [{ type: 'bar', orientation: 'h', y: imp.map(i => i.feature), x: imp.map(i => i.mean), error_x: { type: 'data', array: imp.map(i => i.std) }, marker: { color: '#2a8fa5' } }], { xaxis: { title: 'Decrease in PR-AUC' }, margin: { l: 150, t: 20, r: 10, b: 50 } });
    } catch (e) { show('#root', alertBox('err', e.message)); }
  }
  sel.addEventListener('change', render); render();
})();
