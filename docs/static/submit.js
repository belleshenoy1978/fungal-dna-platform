/* Amplicon submission form: primer presets, upload via fetch, progress polling, redirect to /jobs/{id}. */
(function () {
  'use strict';
  var PRESETS = {
    'ITS1':     { fwd: 'CTTGGTCATTTAGAGGAAGTAA', rev: 'GCTGCGTTCTTCATCGATGC', note: 'ITS1F (Gardes & Bruns 1993) / ITS2 (White et al. 1990)' },
    'ITS2':     { fwd: 'GCATCGATGAAGAACGCAGC',   rev: 'TCCTCCGCTTATTGATATGC', note: 'ITS3 / ITS4 (White et al. 1990)' },
    'ITS2-86F': { fwd: 'GTGAATCATCGAATCTTTGAA',  rev: 'TCCTCCGCTTATTGATATGC', note: 'ITS86F (Turenne et al. 1999) / ITS4' },
    '18S-V4':   { fwd: 'CCAGCASCYGCGGTAATTCC',   rev: 'ACTTTCGTTCTTGATYRA',   note: 'TAReuk454FWD1 / TAReukREV3 (Stoeck et al. 2010); taxonomy via UNITE eukaryote set — fungal coverage limited' },
    '18S-V9':   { fwd: 'GTACACACCGCCCGTC',       rev: 'TGATCCTTCTGCAGGTTCACCTAC', note: '1391F / EukBr (Amaral-Zettler et al. 2009); short amplicon, genus-level resolution at best' },
    'other':    { fwd: '', rev: '', note: 'Enter your own primers, or leave both empty to skip trimming.' }
  };
  var marker = document.getElementById('marker'), fwd = document.getElementById('fwd_primer'), rev = document.getElementById('rev_primer');
  function applyPreset() {
    var p = PRESETS[marker.value] || PRESETS.other;
    fwd.value = p.fwd; rev.value = p.rev; FDP.setText('primer-note', p.note);
  }
  marker.addEventListener('change', applyPreset); applyPreset();

  var files = document.getElementById('files');
  files.addEventListener('change', function () {
    var n = files.files.length, bytes = 0;
    for (var i = 0; i < n; i++) bytes += files.files[i].size;
    FDP.setText('file-summary', n ? n + ' file(s), ' + (bytes / 1048576).toFixed(1) + ' MB' : 'No files selected.');
  });

  var form = document.getElementById('amplicon-form'), btn = document.getElementById('submit-btn');
  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    FDP.setText('form-msg', '');
    if (!files.files.length) { FDP.setText('form-msg', 'Select at least one FASTQ file.'); return; }
    if (!form.reportValidity()) return;
    var fd = new FormData();
    for (var i = 0; i < files.files.length; i++) fd.append('files', files.files[i]);
    // marker value sent to the API is the biological marker; the ITS86F variant is still ITS2
    fd.append('marker', marker.value === 'ITS2-86F' ? 'ITS2' : marker.value);
    ['fwd_primer', 'rev_primer', 'location', 'lat', 'lon', 'submitter', 'pairing'].forEach(function (k) {
      fd.append(k, (document.getElementById(k).value || '').trim());
    });
    btn.disabled = true;
    document.getElementById('progress').classList.remove('hidden');
    setProgress(5, 'Uploading ' + files.files.length + ' file(s)…');
    fetch('/api/amplicon', { method: 'POST', body: fd }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status)); return j; });
    }).then(function (j) {
      FDP.setText('job-id', j.job_id);
      setProgress(15, 'Queued. The worker runs jobs one at a time; this page polls every 5 s.');
      FDP.pollJob(j.job_id, onJob, 5000);
    }).catch(function (e) {
      btn.disabled = false; setProgress(0, 'Submission failed: ' + e.message);
    });
  });

  var STAGE_PCT = { queued: 15, running: 55, done: 100, failed: 100 };
  function onJob(job, err) {
    if (err) { FDP.setText('progress-text', 'Waiting for server… (' + err.message + ')'); return; }
    var notes = (job.notes || (job.result && job.result.notes) || []);
    var ul = document.getElementById('progress-notes'); ul.innerHTML = '';
    notes.slice(-6).forEach(function (n) { var li = document.createElement('li'); li.textContent = n; ul.appendChild(li); });
    var st = job.status || 'queued';
    setProgress(STAGE_PCT[st] || 30, 'Status: ' + st + (job.stage ? ' — ' + job.stage : ''));
    var link = document.getElementById('job-link');
    link.href = FDP.jobUrl(job.job_id || document.getElementById('job-id').textContent);
    if (st === 'done') { link.classList.remove('hidden'); setProgress(100, 'Done — redirecting to the result page…'); setTimeout(function () { window.location.href = link.href; }, 1200); }
    if (st === 'failed') { link.classList.remove('hidden'); link.textContent = 'Open job page (error details) →'; setProgress(100, 'Job failed. ' + (job.error || 'See notes on the job page.')); btn.disabled = false; }
  }
  function setProgress(pct, text) { document.getElementById('progress-fill').style.width = pct + '%'; FDP.setText('progress-text', text); }
})();
