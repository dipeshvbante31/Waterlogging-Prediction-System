'use strict';
(async () => {
  try {
    const v = await api('/api/verification');
    if (!v.recorded) return show('#verification', alertBox('info', 'No verification run has been recorded for this build.'));
    show('#verification', h('p', { class: 'muted small', text: 'Recorded by the maintainer on ' + v.date + '. Only checks listed here were actually executed.' }),
      table([{ label: 'Check', key: 'check' }, { label: 'Status', render: r => badge(r.status === 'PASS' ? 'ok' : (r.status === 'FAIL' ? 'err' : 'warn'), r.status) }, { label: 'Detail', key: 'detail' }], v.checks, { wrap: true, stack: true }));
  } catch (e) { show('#verification', alertBox('err', e.message)); }
})();
