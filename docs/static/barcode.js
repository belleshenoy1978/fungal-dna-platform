/* Barcoding form: paste FASTA or upload files -> POST /api/barcode -> poll -> show hit table. */
(function () {
  'use strict';
  var form = document.getElementById('barcode-form'), btn = document.getElementById('submit-btn');
  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var seqs = document.getElementById('sequences').value.trim(), files = document.getElementById('files').files;
    FDP.setText('form-msg', '');
    if (!seqs && !files.length) { FDP.setText('form-msg', 'Paste a FASTA sequence or choose a file.'); return; }
    if (!form.reportValidity()) return;
    var fd = new FormData();
    if (seqs) fd.append('sequences', seqs);
    for (var i = 0; i < files.length; i++) fd.append('files', files[i]);
    fd.append('submitter', document.getElementById('submitter').value.trim());
    btn.disabled = true;
    document.getElementById('progress').classList.remove('hidden');
    document.getElementById('report').classList.add('hidden');
    FDP.setText('progress-text', 'Uploading…');
    fetch('/api/barcode', { method: 'POST', body: fd }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status)); return j; });
    }).then(function (j) {
      FDP.setText('job-id', j.job_id);
      FDP.setText('progress-text', 'Queued; polling every 5 s…');
      FDP.pollJob(j.job_id, function (job, err) { onJob(j.job_id, job, err); }, 5000);
    }).catch(function (e) { btn.disabled = false; FDP.setText('progress-text', 'Submission failed: ' + e.message); });
  });

  function onJob(jobId, job, err) {
    if (err) { FDP.setText('progress-text', 'Waiting for server… (' + err.message + ')'); return; }
    FDP.setText('progress-text', 'Status: ' + job.status);
    var ul = document.getElementById('progress-notes'); ul.innerHTML = '';
    (job.notes || (job.result && job.result.notes) || []).slice(-6).forEach(function (n) { var li = document.createElement('li'); li.textContent = n; ul.appendChild(li); });
    if (job.status === 'failed') { btn.disabled = false; FDP.setText('progress-text', 'Job failed: ' + (job.error || 'see notes')); return; }
    if (job.status !== 'done') return;
    btn.disabled = false;
    var res = FDP.jobResult(job) || {};
    document.getElementById('report-link').href = '/api/barcode/' + encodeURIComponent(jobId) + '/report.html';
    document.getElementById('job-link').href = FDP.jobUrl(jobId);
    FDP.renderBarcodeTable(document.querySelector('#hits-table tbody'), res.barcoding || []);
    document.getElementById('report').classList.remove('hidden');
  }

  /* shared with job.js */
  FDP.confBadge = function (level, identity, conf) {
    var id = FDP.num(identity), c = conf === null || conf === undefined ? null : FDP.num(conf);
    var cls = 'bad', label = level || 'unassigned';
    if (level === 'species' && id >= 97 && (c === null || c >= 0.8)) cls = 'ok';
    else if (level === 'species' || (level === 'genus' && id >= 90)) cls = 'warn';
    else if (level === 'family' || level === 'order') cls = 'info';
    return '<span class="badge ' + cls + '" title="assignment level: ' + FDP.esc(label) + (c !== null ? ', SINTAX conf ' + c.toFixed(2) : '') + '">' + FDP.esc(label) + '</span>';
  };
  FDP.renderBarcodeTable = function (tbody, rows) {
    tbody.innerHTML = '';
    if (!rows.length) { tbody.innerHTML = '<tr><td colspan="9" class="empty-note">No queries</td></tr>'; return; }
    rows.forEach(function (h) {
      var sx = h.sintax || {}, g = sx.genus || [], s = sx.species || [];
      var conf = (s[1] !== undefined && s[1] !== null) ? s[1] : g[1];
      var tr = document.createElement('tr');
      tr.innerHTML = '<td>' + FDP.esc(h.query_id) + '</td><td class="num">' + FDP.esc(h.query_length) + '</td>' +
        '<td><em>' + FDP.esc(String(h.best_hit_species || '').replace(/_/g, ' ')) + '</em> <span class="small">' + FDP.esc(h.best_hit_accession || '') + '</span></td>' +
        '<td>' + FDP.esc(h.best_hit_sh || '') + '</td><td class="num">' + FDP.fmtNum(h.identity_pct, 1) + '</td><td class="num">' + FDP.fmtNum(h.coverage_pct, 1) + '</td>' +
        '<td>' + FDP.esc(g[0] || '–') + (g[1] !== undefined && g[1] !== null ? ' (' + FDP.fmtNum(g[1], 2) + ')' : '') + (s[0] ? ' / <em>' + FDP.esc(String(s[0]).replace(/_/g, ' ')) + '</em>' + (s[1] !== undefined && s[1] !== null ? ' (' + FDP.fmtNum(s[1], 2) + ')' : '') : '') + '</td>' +
        '<td>' + FDP.confBadge(h.assignment_level, h.identity_pct, conf) + '</td>' +
        '<td>' + (String(h.chimera_flag).toUpperCase() === 'Y' || h.chimera_flag === true ? '<span class="badge bad">chimera?</span>' : 'no') + '</td>';
      tbody.appendChild(tr);
    });
  };
})();
