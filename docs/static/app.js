/* Shared helpers for the Fungal DNA Analysis Platform pages (no dependencies). */
(function (global) {
  'use strict';
  var FDP = {};

  /* ---------- deployment mode ----------
     window.PLATFORM_CONFIG is written by scripts/build_static_site.py into docs/static/config.js for the
     GitHub Pages ("free mode") build. Under FastAPI it is absent and every helper falls back to the
     server routes (/jobs/<id>, /api/jobs/<id>), so the same JS serves both deployments. */
  FDP.config = global.PLATFORM_CONFIG || null;
  FDP.isStatic = !!(FDP.config && FDP.config.mode === 'static');
  FDP.jobUrl = function (jobId) {
    var id = encodeURIComponent(jobId);
    if (FDP.isStatic) return (FDP.config.job_page || 'job.html?id={job_id}').replace('{job_id}', id);
    return '/jobs/' + id;
  };
  FDP.currentJobId = function (el) {
    var fromEl = el && el.dataset && el.dataset.jobId;
    if (fromEl) return fromEl;
    var q = new URLSearchParams(global.location.search).get('id') || '';
    return q.replace(/[^A-Za-z0-9_-]/g, '').slice(0, 64);
  };

  /* ---------- ledger (Google Sheets CSV export) ---------- */
  FDP.ledgerConfig = function (el) {
    // Priority: ?source=<url template with {sheet}>  >  window.PLATFORM_CONFIG.ledger (static build)
    //           >  window.LEDGER_CONFIG (FastAPI)  >  data-attributes on `el`.
    var q = new URLSearchParams(global.location.search);
    var cfg = (FDP.config && FDP.config.ledger) || global.LEDGER_CONFIG || {};
    var ds = (el && el.dataset) || {};
    var id = cfg.spreadsheet_id || ds.spreadsheetId || '';
    var tpl = cfg.csv_template || ds.csvTemplate || 'https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?tqx=out:csv&sheet={sheet}';
    var source = q.get('source') || cfg.source || ds.source || '';
    return { spreadsheet_id: id, csv_template: tpl, source: source };
  };
  FDP.sheetUrl = function (ledger, sheet) {
    var tpl = ledger.source || ledger.csv_template;
    var url = tpl.replace('{spreadsheet_id}', encodeURIComponent(ledger.spreadsheet_id)).replace('{sheet}', encodeURIComponent(sheet));
    // cache-buster so the 60 s poll sees new rows
    return url + (url.indexOf('?') >= 0 ? '&' : '?') + '_=' + Date.now();
  };
  FDP.fetchSheet = function (ledger, sheet) {
    return fetch(FDP.sheetUrl(ledger, sheet), { credentials: 'omit', cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status + ' for sheet ' + sheet);
      return r.text();
    }).then(function (txt) { return FDP.parseCSV(txt); });
  };

  /* RFC 4180-style CSV parser: quoted fields, doubled quotes, CR/LF. Returns array of objects keyed by header. */
  FDP.parseCSVRows = function (text) {
    var rows = [], row = [], field = '', i = 0, inQ = false, c;
    if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
    while (i < text.length) {
      c = text[i];
      if (inQ) {
        if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else { inQ = false; } }
        else field += c;
      } else if (c === '"') inQ = true;
      else if (c === ',') { row.push(field); field = ''; }
      else if (c === '\n' || c === '\r') {
        if (c === '\r' && text[i + 1] === '\n') i++;
        row.push(field); rows.push(row); row = []; field = '';
      } else field += c;
      i++;
    }
    if (field.length || row.length) { row.push(field); rows.push(row); }
    return rows.filter(function (r) { return !(r.length === 1 && r[0] === ''); });
  };
  FDP.parseCSV = function (text) {
    var rows = FDP.parseCSVRows(text || '');
    if (!rows.length) return [];
    var header = rows[0].map(function (h) { return h.trim(); });
    var out = [];
    for (var r = 1; r < rows.length; r++) {
      var o = {}, allEmpty = true;
      for (var c = 0; c < header.length; c++) { var v = rows[r][c] === undefined ? '' : rows[r][c]; o[header[c]] = v; if (v !== '') allEmpty = false; }
      if (!allEmpty) out.push(o);
    }
    return out;
  };

  /* ---------- aggregation used by index KPI strip and dashboard ---------- */
  FDP.num = function (v) { var n = parseFloat(v); return isFinite(n) ? n : 0; };
  FDP.kpis = function (jobs, samples, hits) {
    jobs = jobs || []; samples = samples || []; hits = hits || [];
    var ampl = jobs.filter(function (j) { return j.job_type === 'amplicon'; });
    var nSamples = samples.length || ampl.reduce(function (a, j) { return a + FDP.num(j.n_samples); }, 0);
    return {
      jobs: jobs.length,
      samples: nSamples,
      asvs: ampl.reduce(function (a, j) { return a + FDP.num(j.n_asvs); }, 0),
      species: ampl.reduce(function (a, j) { return a + FDP.num(j.n_species); }, 0),
      barcodes: hits.length
    };
  };

  /* ---------- formatting / DOM ---------- */
  FDP.fmtInt = function (n) { return (n === null || n === undefined || isNaN(n)) ? '–' : Math.round(n).toLocaleString('en-IN'); };
  FDP.fmtNum = function (n, d) { if (n === null || n === undefined || n === '' || isNaN(n)) return '–'; return Number(n).toFixed(d === undefined ? 2 : d); };
  FDP.fmtPct = function (n, d) { return (n === null || n === undefined || n === '' || isNaN(n)) ? '–' : Number(n).toFixed(d === undefined ? 1 : d) + '%'; };
  FDP.esc = function (s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (ch) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]; }); };
  FDP.setText = function (id, t) { var e = document.getElementById(id); if (e) e.textContent = t; };
  FDP.nowHMS = function () { var d = new Date(); return [d.getHours(), d.getMinutes(), d.getSeconds()].map(function (x) { return (x < 10 ? '0' : '') + x; }).join(':'); };
  FDP.safeUrl = function (u) { u = String(u || ''); return (/^(https?:)?\/\//.test(u) || u.charAt(0) === '/') ? u : ''; };
  FDP.statusClass = function (s) { s = String(s || '').toLowerCase(); return ['done', 'failed', 'running', 'queued'].indexOf(s) >= 0 ? 'status-' + s : ''; };

  /* ---------- Plotly loader: local bundle first, CDN fallback ---------- */
  FDP.whenPlotly = function (cb) {
    if (global.Plotly) return cb(global.Plotly);
    var tries = 0, t = setInterval(function () {
      if (global.Plotly) { clearInterval(t); cb(global.Plotly); }
      else if (++tries > 100) { clearInterval(t); cb(null); }
    }, 100);
  };
  FDP.plotConfig = { responsive: true, displaylogo: false, modeBarButtonsToRemove: ['lasso2d', 'select2d'] };
  FDP.baseLayout = function (extra) {
    var l = { margin: { l: 50, r: 10, t: 30, b: 40 }, font: { family: 'system-ui, Segoe UI, Roboto, sans-serif', size: 12 },
              paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', autosize: true, legend: { orientation: 'h', y: -0.2 } };
    for (var k in (extra || {})) l[k] = extra[k];
    return l;
  };
  FDP.plotOrEmpty = function (Plotly, id, traces, layout, emptyText) {
    var el = document.getElementById(id); if (!el) return;
    var hasData = traces && traces.some(function (t) { return (t.x && t.x.length) || (t.lat && t.lat.length) || (t.values && t.values.length); });
    if (!Plotly || !hasData) { el.innerHTML = '<div class="empty-note">' + FDP.esc(emptyText || (Plotly ? 'No data yet' : 'Plotly failed to load')) + '</div>'; return; }
    el.innerHTML = '';
    Plotly.react(el, traces, layout, FDP.plotConfig);
  };

  /* ---------- job polling shared by submit/barcode/job pages ---------- */
  /* FastAPI: GET /api/jobs/<id>. Static build: FDP.fetchJobStatic (see below). */
  FDP.fetchJob = function (jobId) {
    if (FDP.isStatic) return FDP.fetchJobStatic(jobId);
    return fetch('/api/jobs/' + encodeURIComponent(jobId), { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  };
  /* Static build job lookup, same-origin first:
       1. ./jobs/<id>.json  (compact result.json committed by the GitHub Actions compute workflow)
       2. ledger `queue` tab (CSV export): queued / running / failed status written by Apps Script + Actions
       3. ledger `jobs` tab: result_folder_url once the pipeline has appended its row
     Returns a job dict shaped like /api/jobs/<id>: {job_id, job_type, status, submitted_at, completed_at,
     params, notes, error, result?, result_folder_url?, result_page_url?, source}. */
  FDP.fetchJobStatic = function (jobId) {
    var cfg = FDP.config || {}, root = document.getElementById('job-root');
    var base = cfg.jobs_json_path || (root && root.dataset && root.dataset.jobsPath) || './jobs/';
    var ledger = FDP.ledgerConfig(root);
    var url = base + encodeURIComponent(jobId) + '.json' + '?_=' + Date.now();
    return fetch(url, { cache: 'no-store', credentials: 'omit' }).then(function (r) {
      if (r.ok) return r.json().then(function (res) {
        return { job_id: jobId, job_type: res.job_type, status: res.status || 'done', submitted_at: res.submitted_at, completed_at: res.completed_at,
                 params: res.params || {}, notes: res.notes || [], result: res, result_folder_url: res.result_folder_url || '', source: 'jobs_json' };
      });
      if (r.status !== 404 && r.status !== 403) throw new Error('HTTP ' + r.status + ' for ' + url);
      return null;
    }).then(function (job) {
      if (job) return job;
      // fall back to the ledger: queue tab (status) + jobs tab (folder link)
      var qs = cfg.queue_sheet || 'queue';
      return Promise.allSettled([FDP.fetchSheet(ledger, qs), FDP.fetchSheet(ledger, 'jobs')]).then(function (res) {
        var qrows = res[0].status === 'fulfilled' ? res[0].value : [], jrows = res[1].status === 'fulfilled' ? res[1].value : [];
        var q = qrows.filter(function (r) { return r.job_id === jobId; }).pop() || null;
        var j = jrows.filter(function (r) { return r.job_id === jobId; }).pop() || null;
        if (!q && !j) {
          if (res[0].status === 'rejected' && res[1].status === 'rejected') throw new Error('result not published yet and the ledger could not be read (' + res[0].reason.message + ')');
          return { job_id: jobId, status: 'unknown', params: {}, notes: [], source: 'none' };
        }
        var st = String((q && q.status) || (j && j.status) || 'queued').toLowerCase();
        var notes = [];
        if (q && q.started_at) notes.push('started ' + q.started_at);
        if (j && j.notes) notes.push(j.notes);
        return { job_id: jobId, job_type: (q && q.job_type) || (j && j.job_type) || '', status: st,
                 submitted_at: (q && q.submitted_at) || (j && j.submitted_at) || '', started_at: q && q.started_at, completed_at: (q && q.completed_at) || (j && j.completed_at) || '',
                 params: { submitter: (q && q.submitter) || (j && j.submitter) || '', marker: (q && q.marker) || (j && j.marker) || '', location: (q && q.location) || (j && j.location) || '' },
                 notes: notes, error: (q && q.error) || '', result_folder_url: (j && j.result_folder_url) || (q && q.result_folder_url) || '',
                 result_page_url: (q && q.result_page_url) || '', ledger_row: j, source: q ? 'queue' : 'jobs' };
      });
    });
  };
  FDP.pollJob = function (jobId, onUpdate, intervalMs) {
    intervalMs = intervalMs || (FDP.isStatic ? 30000 : 5000);
    var stop = false;
    function tick() {
      if (stop) return;
      FDP.fetchJob(jobId).then(function (job) {
        // In the static build a 'done' row without the published JSON is not terminal: GitHub Pages
        // redeploys a minute or two after the compute workflow commits docs/jobs/<id>.json.
        var done = job.status === 'failed' || (job.status === 'done' && (!FDP.isStatic || job.source === 'jobs_json'));
        onUpdate(job, null);
        if (!done) setTimeout(tick, intervalMs); else stop = true;
      }).catch(function (e) { onUpdate(null, e); if (!stop) setTimeout(tick, intervalMs * 2); });
    }
    tick();
    return function () { stop = true; };
  };
  /* The job dict returned by /api/jobs/{id} may carry the result under .result or inline. */
  FDP.jobResult = function (job) {
    if (!job) return null;
    if (job.result && typeof job.result === 'object') return job.result;
    if (job.summary || job.taxa || job.barcoding) return job;
    return null;
  };

  /* ?embed=1 hides the site header (Google Sites embed). FastAPI sets the class server-side for the dashboard;
     the static build relies on this. */
  try { if (new URLSearchParams(global.location.search).get('embed') === '1' && document.body) document.body.classList.add('embed'); } catch (e) { /* no-op */ }

  global.FDP = FDP;
})(window);
