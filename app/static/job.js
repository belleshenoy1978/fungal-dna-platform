/* Job result page: FastAPI -> fetch /api/jobs/{id} every 5 s; static GitHub Pages build -> ./jobs/<id>.json,
   falling back to the ledger `queue` / `jobs` tabs every 30 s (FDP.fetchJobStatic). Renders amplicon or barcode result. */
(function () {
  'use strict';
  var root = document.getElementById('job-root'); if (!root) return;
  var jobId = FDP.currentJobId(root), rendered = false;
  var C = { in: '#9fb3c8', merged: '#5c8fbf', filtered: '#2a6f97', nonchim: '#0b6e4f' };
  var POLL_MS = FDP.isStatic ? 30000 : 5000;

  FDP.setText('job-id', jobId || '?');
  if (jobId) document.title = 'Job ' + jobId + ' — Fungal DNA Analysis Platform';
  if (!jobId) {
    document.getElementById('job-status-text').textContent = 'No job id given. Open this page as job.html?id=<job_id> (the id is shown on the dashboard and in the form confirmation).';
    return;
  }
  FDP.pollJob(jobId, onJob, POLL_MS);

  function jobLinks(job) {
    var el = document.getElementById('job-links'); if (!el) return;
    var html = [];
    var folder = FDP.safeUrl(job.result_folder_url || (job.result && job.result.result_folder_url));
    if (folder) html.push('<a href="' + FDP.esc(folder) + '" target="_blank" rel="noopener">Drive result folder →</a>');
    if (FDP.isStatic && job.params && job.params.location) html.push('<span>' + FDP.esc(job.params.marker || '') + (job.params.marker ? ' · ' : '') + FDP.esc(job.params.location) + '</span>');
    el.innerHTML = html.join('');
  }

  function onJob(job, err) {
    if (err) { FDP.setText('job-status-text', (FDP.isStatic ? 'Could not load the job (' : 'Could not reach the server (') + err.message + '); retrying…'); return; }
    var st = job.status || 'queued';
    jobLinks(job);
    var notes = job.notes || (job.result && job.result.notes) || [];
    var ul = document.getElementById('job-notes'); ul.innerHTML = '';
    notes.forEach(function (n) { var li = document.createElement('li'); li.textContent = n; ul.appendChild(li); });
    var tb = document.getElementById('job-type-badge'); tb.textContent = job.job_type || ''; tb.className = 'badge ' + (job.job_type === 'barcode' ? 'warn' : 'info');
    var meta = [];
    if (job.submitted_at) meta.push('submitted ' + String(job.submitted_at).replace('T', ' ').replace(/Z$/, ' UTC'));
    if (job.completed_at) meta.push('completed ' + String(job.completed_at).replace('T', ' ').replace(/Z$/, ' UTC'));
    var params = job.params || (job.result && job.result.params) || {};
    if (params.submitter) meta.push('submitter ' + params.submitter);
    FDP.setText('job-meta', meta.join(' · '));
    var stEl = document.getElementById('job-status-text'); stEl.className = FDP.statusClass(st);
    var every = 'This page refreshes every ' + Math.round(POLL_MS / 1000) + ' s.';
    if (st === 'unknown') { stEl.className = 'status-queued'; stEl.textContent = 'Job ' + jobId + ' is not in the queue yet (new submissions appear within a minute). ' + every; return; }
    if (st === 'queued' || st === 'running') { stEl.textContent = 'Status: ' + st + (job.stage ? ' — ' + job.stage : '') + '. ' + every; return; }
    if (st === 'failed') { stEl.textContent = 'Status: failed. ' + (job.error || 'See notes below.'); return; }
    if (FDP.isStatic && job.source !== 'jobs_json') {
      stEl.textContent = 'Status: done — the result page is being published (GitHub Pages redeploys within a few minutes). ' + (job.result_folder_url ? 'Files are already in the Drive folder linked above. ' : '') + every;
      return;
    }
    stEl.textContent = 'Status: done.';
    if (rendered) return; rendered = true;
    var res = FDP.jobResult(job);
    if (!res) { stEl.textContent = 'Status: done, but the result JSON is not available yet.'; return; }
    if ((res.job_type || job.job_type) === 'barcode') renderBarcode(res); else FDP.whenPlotly(function (P) { renderAmplicon(P, res); });
  }

  function tile(v, label) { return '<div class="kpi"><div class="kpi-value">' + v + '</div><div class="kpi-label">' + FDP.esc(label) + '</div></div>'; }
  function fileList(ulId, files) {
    var ul = document.getElementById(ulId); ul.innerHTML = '';
    var names = Object.keys(files || {});
    if (!names.length) { ul.innerHTML = '<li class="small">No files listed.</li>'; return; }
    names.forEach(function (n) { var u = FDP.safeUrl(files[n]); var li = document.createElement('li'); li.innerHTML = u ? '<a href="' + FDP.esc(u) + '" target="_blank" rel="noopener">' + FDP.esc(n) + '</a>' : FDP.esc(n); ul.appendChild(li); });
  }

  function renderAmplicon(Plotly, res) {
    document.getElementById('amplicon-view').classList.remove('hidden');
    var s = res.summary || {}, samples = res.samples || [], taxa = res.taxa || [];
    var kept = s.n_reads_in ? FDP.fmtPct(100 * s.n_reads_kept / s.n_reads_in) : '–';
    document.getElementById('summary-tiles').innerHTML =
      tile(FDP.esc(s.marker || '–'), 'marker') + tile(FDP.fmtInt(s.n_samples), 'samples') + tile(FDP.fmtInt(s.n_reads_in), 'read pairs in') +
      tile(FDP.fmtInt(s.n_reads_kept) + ' <span class="small">(' + kept + ')</span>', 'reads kept') + tile(FDP.fmtInt(s.n_asvs), 'ASVs') +
      tile(FDP.fmtInt(s.n_fungal_asvs), 'fungal ASVs') + tile(FDP.fmtInt(s.n_species), 'species-level') +
      tile(FDP.esc(s.location || '–') + (s.lat !== null && s.lat !== undefined && s.lat !== '' ? ' <span class="small">' + FDP.esc(s.lat) + 'N ' + FDP.esc(s.lon) + 'E</span>' : ''), 'location');

    var ids = samples.map(function (x) { return x.sample_id; });
    function col(k) { return samples.map(function (x) { return FDP.num(x[k]); }); }
    FDP.plotOrEmpty(Plotly, 'plot-reads', [
      { type: 'bar', name: 'reads in', x: ids, y: col('reads_in'), marker: { color: C.in } },
      { type: 'bar', name: 'merged', x: ids, y: col('reads_merged'), marker: { color: C.merged } },
      { type: 'bar', name: 'filtered (maxEE 1.0)', x: ids, y: col('reads_filtered'), marker: { color: C.filtered } },
      { type: 'bar', name: 'non-chimeric (mapped)', x: ids, y: col('reads_nonchimeric'), marker: { color: C.nonchim } }],
      FDP.baseLayout({ barmode: 'group', yaxis: { title: { text: 'reads' } }, xaxis: { type: 'category' } }), 'No per-sample data');
    FDP.plotOrEmpty(Plotly, 'plot-shannon', [{ type: 'bar', x: ids, y: col('shannon'), marker: { color: '#6a4c93' }, text: col('shannon').map(function (v) { return v.toFixed(2); }), textposition: 'outside' }],
      FDP.baseLayout({ yaxis: { title: { text: "Shannon H'" }, rangemode: 'tozero' }, xaxis: { type: 'category' } }), 'No diversity data');

    var comp = res.composition || {};
    function compBars(id, obj, title) {
      var names = Object.keys(obj || {}).sort(function (a, b) { return obj[b] - obj[a]; });
      var traces = names.map(function (n, i) { return { type: 'bar', orientation: 'h', name: n, y: [title], x: [obj[n]], hovertemplate: n + ': %{x:.2f}%<extra></extra>' }; });
      FDP.plotOrEmpty(Plotly, id, traces, FDP.baseLayout({ barmode: 'stack', margin: { l: 10, r: 10, t: 10, b: 40 }, xaxis: { range: [0, 100], title: { text: '% of kept reads' } }, yaxis: { showticklabels: false }, legend: { orientation: 'h', y: -0.35, font: { size: 10 } } }), 'No composition data');
    }
    compBars('plot-phylum', comp.phylum, 'phylum'); compBars('plot-genus', comp.genus, 'genus');

    var tb = document.querySelector('#samples-table tbody'); tb.innerHTML = '';
    samples.forEach(function (x) {
      var tr = document.createElement('tr');
      tr.innerHTML = '<td>' + FDP.esc(x.sample_id) + '</td><td class="num">' + FDP.fmtInt(x.reads_in) + '</td><td class="num">' + FDP.fmtInt(x.reads_merged) + '</td><td class="num">' + FDP.fmtInt(x.reads_filtered) + '</td><td class="num">' + FDP.fmtInt(x.reads_nonchimeric) + '</td>' +
        '<td class="num">' + (x.reads_in ? FDP.fmtPct(100 * x.reads_nonchimeric / x.reads_in) : '–') + '</td><td class="num">' + FDP.fmtInt(x.n_asvs) + '</td><td class="num">' + FDP.fmtNum(x.shannon, 3) + '</td><td class="num">' + FDP.fmtPct(x.pct_fungal) + '</td>';
      tb.appendChild(tr);
    });

    var taxaSorted = taxa.slice().sort(function (a, b) { return FDP.num(b.reads_total) - FDP.num(a.reads_total); });
    FDP.setText('asv-count', '(' + taxaSorted.length + ' ASVs shown; top 500 by reads)');
    function drawTaxa() {
      var q = (document.getElementById('asv-filter').value || '').toLowerCase();
      var body = document.querySelector('#asv-table tbody'), html = [];
      taxaSorted.forEach(function (t) {
        var line = [t.asv_id, t.kingdom, t.phylum, t['class'], t.order, t.family, t.genus, t.species].join(' ').toLowerCase();
        if (q && line.indexOf(q) < 0) return;
        var ps = t.per_sample || {}; var psTxt = Object.keys(ps).map(function (k) { return k + ': ' + FDP.fmtInt(ps[k]); }).join(', ');
        html.push('<tr><td><code>' + FDP.esc(t.asv_id) + '</code></td><td class="num">' + FDP.fmtInt(t.reads_total) + '</td><td>' + FDP.esc(t.kingdom) + '</td><td>' + FDP.esc(t.phylum) + '</td><td>' + FDP.esc(t['class']) + '</td><td>' + FDP.esc(t.order) + '</td><td>' + FDP.esc(t.family) + '</td><td><em>' + FDP.esc(t.genus) + '</em></td><td><em>' + FDP.esc(String(t.species || '').replace(/_/g, ' ')) + '</em></td><td class="num">' + (t.sintax_conf === null || t.sintax_conf === undefined ? '–' : FDP.fmtNum(t.sintax_conf, 2)) + '</td><td class="small">' + FDP.esc(psTxt) + '</td></tr>');
      });
      body.innerHTML = html.join('') || '<tr><td colspan="11" class="empty-note">No ASVs match</td></tr>';
    }
    document.getElementById('asv-filter').addEventListener('input', drawTaxa); drawTaxa();
    fileList('file-links', res.files);
  }

  function renderBarcode(res) {
    document.getElementById('barcode-view').classList.remove('hidden');
    var rl = document.getElementById('report-link');
    var reportUrl = (res.files && FDP.safeUrl(res.files['report.html'])) || (FDP.isStatic ? FDP.safeUrl(res.result_folder_url) : ('/api/barcode/' + encodeURIComponent(jobId) + '/report.html'));
    if (reportUrl) rl.href = reportUrl; else { rl.removeAttribute('href'); rl.textContent = 'Printable report not available'; }
    FDP.renderBarcodeTable(document.querySelector('#hits-table tbody'), res.barcoding || []);
    fileList('file-links-bc', res.files);
  }
})();
