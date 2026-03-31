// Inject this in browser console to trace what calls initKanban/renderTaskDetail
window._origInit = initKanban;
window._origRender = renderTaskDetail;
window._rlog = [];

initKanban = function(f) {
  var s = new Error().stack.split('\n').slice(1,5).join(' << ');
  console.error('[RELOAD TRACE] initKanban called from:', s);
  window._rlog.push('initKanban: ' + s);
  return window._origInit(f);
};

renderTaskDetail = function(id) {
  var s = new Error().stack.split('\n').slice(1,5).join(' << ');
  console.error('[RELOAD TRACE] renderTaskDetail called from:', s);
  window._rlog.push('renderTaskDetail(' + id + '): ' + s);
  return window._origRender(id);
};

console.log('Reload tracing active. Do your drag, then run: console.log(window._rlog)');
