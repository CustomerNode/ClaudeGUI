/* workforce.js — workforce grid view mode */

function getSessionStatus(id) {
  if (!runningIds.has(id)) {
    // Sessions opened in GUI panel are considered idle even if no OS process detected
    if (guiOpenSessions.has(id)) return 'idle';
    return 'sleeping';
  }
  return sessionKinds[id] || 'working';
}

function setViewMode(mode) {
  viewMode = mode;
  localStorage.setItem('viewMode', mode);
  const listEl = document.getElementById('session-list');
  const gridEl = document.getElementById('workforce-grid');
  const btnList = document.getElementById('btn-view-list');
  const btnWf   = document.getElementById('btn-view-workforce');
  if (mode === 'workforce') {
    listEl.style.display = 'none';
    gridEl.classList.add('visible');
    if (btnList) btnList.classList.remove('active');
    if (btnWf)   btnWf.classList.add('active');
  } else {
    listEl.style.display = '';
    gridEl.classList.remove('visible');
    if (btnList) btnList.classList.add('active');
    if (btnWf)   btnWf.classList.remove('active');
  }
  filterSessions();
}

function setWfSort(sort) {
  wfSort = sort;
  localStorage.setItem('wfSort', sort);
  ['status','recent','name'].forEach(s => {
    const btn = document.getElementById('wf-btn-' + s);
    if (btn) btn.classList.toggle('active', s === sort);
  });
  filterSessions();
}

function wfSortedSessions(sessions) {
  const copy = [...sessions];
  const statusOrder = {question:0, working:1, idle:2, sleeping:3};
  if (wfSort === 'status') {
    copy.sort((a, b) => {
      const sa = statusOrder[getSessionStatus(a.id)] ?? 3;
      const sb = statusOrder[getSessionStatus(b.id)] ?? 3;
      if (sa !== sb) return sa - sb;
      return (b.last_activity_ts||b.sort_ts||0) - (a.last_activity_ts||a.sort_ts||0);
    });
  } else if (wfSort === 'name') {
    copy.sort((a, b) => (a.display_title||'').localeCompare(b.display_title||''));
  } else {
    // recent
    copy.sort((a, b) => (b.last_activity_ts||b.sort_ts||0) - (a.last_activity_ts||a.sort_ts||0));
  }
  return copy;
}

function renderWorkforce(sessions) {
  const grid = document.getElementById('workforce-grid');
  if (!sessions.length) {
    grid.innerHTML = '<div style="padding:20px;color:#444;font-size:12px;">No sessions found</div>';
    return;
  }
  const statusSvg = {
    question: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#ff9500" stroke-width="1.5" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><circle cx="12" cy="17" r=".5" fill="#ff9500"/></svg>',
    working: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#7c7cff" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 3.5L20.5 9.5"/><path d="M20.5 9.5L18 12L12 6L14.5 3.5"/><path d="M12 6L4 14L3 21L10 20L18 12"/><path d="M6.5 14.5L9.5 17.5"/></svg>',
    idle: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#44aa66" stroke-width="1.5" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg>',
    sleeping: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" style="opacity:0.3"><circle cx="12" cy="12" r="10"/><line x1="8" y1="12" x2="16" y2="12"/></svg>',
  };
  const statusLabel = {question:'Question', working:'Working', idle:'Idle', sleeping:'Sleeping'};
  grid.innerHTML = sessions.map(s => {
    const st = getSessionStatus(s.id);
    const emoji = statusSvg[st] || statusSvg.sleeping;
    const label = statusLabel[st] || 'Sleeping';
    const selClass = s.id === activeId ? ' wf-selected' : '';
    const name = escHtml((s.display_title||s.id).slice(0,22) + ((s.display_title||'').length>22?'\u2026':''));
    const date = (s.last_activity||'').split('  ')[0] || '';
    return `<div class="wf-card wf-${st}${selClass}" onclick="singleOrDouble('${s.id}',event)" title="${escHtml(s.display_title)} \u2014 double-click to open in Claude Code GUI">
      <div class="wf-avatar">${emoji}</div>
      <div class="wf-status-label">${label}</div>
      <div class="wf-name">${name}</div>
      <div class="wf-meta">${escHtml(date)}</div>
    </div>`;
  }).join('');
}

function wfCardClick(id) {
  selectSession(id);
}
