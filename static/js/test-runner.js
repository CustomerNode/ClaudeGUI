/* test-runner.js — regression test runner UI (System menu) */

let _testRunning = false;

function runTests(mode) {
  if (_testRunning) {
    showGitSyncModal('Tests Running', '<p style="color:var(--text-muted)">Tests are already running. Cancel the current run first.</p>', [
      {label: 'Cancel Tests', primary: false, onclick: () => { cancelTests(); }},
      {label: 'OK', onclick: closeGitSyncModal}
    ]);
    return;
  }

  const label = mode === 'fast' ? 'Run Tests (Fast)' : 'Run Tests (Full)';
  const desc = mode === 'fast'
    ? 'Running unit and API tests...'
    : 'Running full test suite including e2e...';

  _testRunning = true;

  // Show the animated modal
  showGitSyncModal(label, _testRunnerHtml(desc), []);

  let lines = [];

  // POST to start the test run
  fetch('/api/run-tests', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mode})
  }).then(res => {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    function read() {
      reader.read().then(({done, value}) => {
        if (done) {
          _testRunning = false;
          return;
        }
        buffer += decoder.decode(value, {stream: true});
        const parts = buffer.split('\n\n');
        buffer = parts.pop(); // keep incomplete chunk

        for (const part of parts) {
          if (!part.startsWith('data: ')) continue;
          try {
            const d = JSON.parse(part.slice(6));
            if (d.type === 'line') {
              lines.push(d.line);
              _updateTestOutput(lines);
            } else if (d.type === 'done') {
              _showTestResults(label, d, lines);
            } else if (d.type === 'error') {
              _showTestError(label, d.line);
            }
          } catch(_) {}
        }
        read();
      }).catch(() => { _testRunning = false; });
    }
    read();
  }).catch(e => {
    _testRunning = false;
    _showTestError(label, 'Could not start tests: ' + e.message);
  });
}

function _testRunnerHtml(desc) {
  return '<div style="text-align:center;padding:12px 0;">'
    + '<div class="scan-anim">'
    + '<div class="scan-shield">'
    +   '<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
    +     '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>'
    +   '</svg>'
    +   '<div class="scan-beam"></div>'
    + '</div>'
    + '<div class="scan-label" id="test-status">' + desc + '</div>'
    + '</div>'
    + '<pre id="test-output" style="text-align:left;font-size:11px;font-family:monospace;'
    + 'max-height:300px;overflow-y:auto;background:rgba(0,0,0,0.2);border-radius:6px;'
    + 'padding:8px 10px;margin-top:10px;color:var(--text-secondary);white-space:pre-wrap;'
    + 'word-break:break-all;"></pre>'
    + '</div>';
}

function _updateTestOutput(lines) {
  const el = document.getElementById('test-output');
  if (!el) return;
  // Show last 40 lines to keep it readable
  const visible = lines.slice(-40);
  el.textContent = visible.join('\n');
  el.scrollTop = el.scrollHeight;
}

function _showTestResults(label, summary, lines) {
  _testRunning = false;

  let body;
  const btns = [];

  if (summary.ok) {
    body = '<div style="text-align:center;padding:16px 0;">'
      + '<div style="font-weight:600;color:var(--accent-green,#4ecdc4);font-size:16px;">All Tests Passed</div>'
      + '<div style="font-size:13px;color:var(--text-muted);margin-top:6px;">'
      + summary.passed + ' passed'
      + (summary.skipped ? ', ' + summary.skipped + ' skipped' : '')
      + '</div></div>';
    btns.push({label: 'OK', primary: true, onclick: closeGitSyncModal});
  } else {
    // Show failure summary + scrollable output
    body = '<div style="text-align:center;padding:8px 0;">'
      + '<div style="font-weight:600;color:var(--result-err,#ff4444);font-size:16px;">'
      + summary.failed + ' Test' + (summary.failed !== 1 ? 's' : '') + ' Failed</div>'
      + '<div style="font-size:13px;color:var(--text-muted);margin-top:4px;">'
      + summary.passed + ' passed, ' + summary.failed + ' failed'
      + (summary.errors ? ', ' + summary.errors + ' errors' : '')
      + (summary.skipped ? ', ' + summary.skipped + ' skipped' : '')
      + '</div></div>';

    // Show the failure lines
    const failLines = lines.filter(l =>
      l.startsWith('FAILED') || l.startsWith('ERROR') || l.includes('AssertionError') || l.includes('assert ')
    );
    if (failLines.length > 0) {
      body += '<pre style="text-align:left;font-size:10px;font-family:monospace;'
        + 'max-height:250px;overflow-y:auto;background:rgba(255,60,60,0.06);'
        + 'border:1px solid rgba(255,60,60,0.15);border-radius:6px;'
        + 'padding:8px 10px;margin-top:10px;color:var(--text-secondary);'
        + 'white-space:pre-wrap;word-break:break-all;">'
        + failLines.map(l => _escTestHtml(l)).join('\n')
        + '</pre>';
    }

    btns.push({label: 'OK', onclick: closeGitSyncModal});
  }

  showGitSyncModal(
    summary.ok ? label + ' \u2713' : label + ' \u2014 Failures Found',
    body,
    btns
  );
}

function _showTestError(label, msg) {
  _testRunning = false;
  showGitSyncModal(label + ' \u2014 Error',
    '<p style="color:var(--result-err)">' + _escTestHtml(msg) + '</p>',
    [{label: 'OK', onclick: closeGitSyncModal}]);
}

function cancelTests() {
  fetch('/api/cancel-tests', {method: 'POST'}).then(() => {
    _testRunning = false;
    closeGitSyncModal();
  }).catch(() => {});
}

function _escTestHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}
