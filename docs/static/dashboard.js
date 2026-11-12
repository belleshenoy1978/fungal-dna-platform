/* Live dashboard: polls the Google Sheets ledger CSV export every 60 s and renders Plotly panels.
   Pure aggregation functions are exposed on FDP.dash so they can be unit-tested outside a browser.
   Works under FastAPI (/dashboard) and in the static GitHub Pages build (dashboard.html); job links go
   through FDP.jobUrl() -> /jobs/<id> or job.html?id=<id>. */
(function () {
  'use strict';
  var SHEETS = ['jobs', 'samples', 'taxa', 'barcoding_hits'];
  var COLORS = { amplicon: '#0b6e4f', barcode: '#c9771e' };
  var PHYLUM_COLORS = ['#0b6e4f', '#2a6f97', '#8c2f39', '#6a4c93', '#d4a017', '#1b998b', '#b56576', '#3a7d44', '#7b5e3b', '#4f6d7a'];
  var dash = {};

  /* ---- aggregation (pure) ---- */
  dash.dayOf = function (iso) { return String(iso || '').slice(0, 10); };
  dash.submissionsPerDay = function (jobs) {
    var byDay = {};
    jobs.forEach(function (j) {
      var d = dash.dayOf(j.submitted_at); if (!d) return;
      var t = j.job_type === 'barcode' ? 'barcode' : 'amplicon';
      byDay[d] = byDay[d] || { amplicon: 0, barcode: 0 }; byDay[d][t]++;
    });
    var days = Object.keys(byDay).sort();
    return { days: days, amplicon: days.map(function (d) { return byDay[d].amplicon; }), barcode: days.map(function (d) { return byDay[d].barcode; }) };
  };
  dash.generaByReads = function (taxa, top) {
    var acc = {};
    taxa.forEach(function (r) { var g = (r.genus || '').trim(); if (!g || /^unassigned$/i.test(g)) return; acc[g] = (acc[g] || 0) + FDP.num(r.reads); });
    return dash.topN(acc, top || 20);
  };
  dash.genusFromSpecies = function (name) { return String(name || '').trim().split(/[_\s]+/)[0] || ''; };
  dash.barcodeGenera = function (hits, top) {
    var acc = {};
    hits.forEach(function (h) { var g = dash.genusFromSpecies(h.best_hit_species); if (!g) return; acc[g] = (acc[g] || 0) + 1; });
    return dash.topN(acc, top || 20);
  };
  dash.topN = function (acc, n) {
    var arr = Object.keys(acc).map(function (k) { return [k, acc[k]]; }).sort(function (a, b) { return b[1] - a[1]; }).slice(0, n);
    return { names: arr.map(function (x) { return x[0]; }), values: arr.map(function (x) { return x[1]; }) };
  };
  dash.phylaByJob = function (taxa, jobs) {
    var byJob = {}, totals = {}, phyla = {};
    taxa.forEach(function (r) {
      var jid = r.job_id, p = (r.phylum || '').trim();
      if (r.kingdom && r.kingdom !== 'Fungi') p = 'Non-fungal'; else if (!p) p = 'Unassigned';
      var n = FDP.num(r.reads);
      byJob[jid] = byJob[jid] || {}; byJob[jid][p] = (byJob[jid][p] || 0) + n; totals[jid] = (totals[jid] || 0) + n; phyla[p] = (phyla[p] || 0) + n;
    });
    var label = {}; (jobs || []).forEach(function (j) { label[j.job_id] = (j.marker ? j.marker + ' · ' : '') + (j.location || j.job_id); });
    var jids = Object.keys(byJob), order = Object.keys(phyla).sort(function (a, b) { return phyla[b] - phyla[a]; });
    return { jobs: jids, labels: jids.map(function (j) { return label[j] || j; }), phyla: order,
             pct: order.map(function (p) { return jids.map(function (j) { return totals[j] ? 100 * (byJob[j][p] || 0) / totals[j] : 0; }); }) };
  };
  dash.mapPoints = function (jobs) {
    var pts = { lat: [], lon: [], text: [], size: [], ids: [] };
    jobs.forEach(function (j) {
      var la = parseFloat(j.lat), lo = parseFloat(j.lon); if (!isFinite(la) || !isFinite(lo)) return;
      pts.lat.push(la); pts.lon.push(lo); pts.ids.push(j.job_id);
      pts.size.push(10 + 4 * Math.sqrt(FDP.num(j.n_samples)));
      pts.text.push((j.location || j.job_id) + '<br>' + (j.marker || '') + ' · ' + FDP.num(j.n_samples) + ' sample(s) · ' + FDP.fmtInt(FDP.num(j.n_asvs)) + ' ASVs<br>' + dash.dayOf(j.submitted_at));
    });
    return pts;
  };
  FDP.dash = dash;

  /* ---- rendering ---- */
  var root = document.getElementById('dashboard'); if (!root) return;
  var ledger = FDP.ledgerConfig(root);
  var state = { jobs: [], samples: [], taxa: [], barcoding_hits: [] };

  function render(Plotly) {
    var jobs = state.jobs, k = FDP.kpis(jobs, state.samples, state.barcoding_hits);
    FDP.setText('kpi-jobs', FDP.fmtInt(k.jobs)); FDP.setText('kpi-samples', FDP.fmtInt(k.samples)); FDP.setText('kpi-asvs', FDP.fmtInt(k.asvs));
    FDP.setText('kpi-species', FDP.fmtInt(k.species)); FDP.setText('kpi-barcodes', FDP.fmtInt(k.barcodes));

    var s = dash.submissionsPerDay(jobs);
    FDP.plotOrEmpty(Plotly, 'plot-submissions', [
      { type: 'bar', name: 'amplicon', x: s.days, y: s.amplicon, marker: { color: COLORS.amplicon } },
      { type: 'bar', name: 'barcode', x: s.days, y: s.barcode, marker: { color: COLORS.barcode } }],
      FDP.baseLayout({ barmode: 'stack', xaxis: { type: 'category', title: { text: 'day (UTC)' } }, yaxis: { title: { text: 'jobs' }, dtick: 1 } }), 'No submissions yet');

    var m = dash.mapPoints(jobs);
    FDP.plotOrEmpty(Plotly, 'plot-map', [{ type: 'scattergeo', mode: 'markers', lat: m.lat, lon: m.lon, text: m.text, hoverinfo: 'text', customdata: m.ids,
      marker: { size: m.size, color: COLORS.amplicon, opacity: 0.75, line: { color: '#fff', width: 1 } } }],
      FDP.baseLayout({ margin: { l: 0, r: 0, t: 10, b: 0 }, geo: { scope: 'asia', resolution: 50, showland: true, landcolor: '#eef0e9', showocean: true, oceancolor: '#dbe9f2',
        showcountries: true, countrycolor: '#bbb', coastlinecolor: '#888', lataxis: { range: [4, 38] }, lonaxis: { range: [64, 100] }, center: { lat: 21, lon: 82 }, projection: { type: 'mercator' } } }),
      'No georeferenced jobs yet');
    var mapEl = document.getElementById('plot-map');
    if (Plotly && mapEl && mapEl.on && !mapEl.__clickBound) { mapEl.__clickBound = true; mapEl.on('plotly_click', function (ev) { var id = ev.points && ev.points[0] && ev.points[0].customdata; if (id) window.open(FDP.jobUrl(id), '_blank'); }); }

    var g = dash.generaByReads(state.taxa, 20);
    FDP.plotOrEmpty(Plotly, 'plot-genera', [{ type: 'bar', orientation: 'h', x: g.values.slice().reverse(), y: g.names.slice().reverse(), marker: { color: COLORS.amplicon } }],
      FDP.baseLayout({ margin: { l: 140, r: 10, t: 10, b: 40 }, xaxis: { title: { text: 'reads (sum over samples)' } }, yaxis: { tickfont: { style: 'italic' }, automargin: true } }), 'No taxa in ledger yet');

    var bg = dash.barcodeGenera(state.barcoding_hits, 20);
    FDP.plotOrEmpty(Plotly, 'plot-barcode-genera', [{ type: 'bar', orientation: 'h', x: bg.values.slice().reverse(), y: bg.names.slice().reverse(), marker: { color: COLORS.barcode } }],
      FDP.baseLayout({ margin: { l: 140, r: 10, t: 10, b: 40 }, xaxis: { title: { text: 'queries' }, dtick: 1 }, yaxis: { tickfont: { style: 'italic' }, automargin: true } }), 'No barcoding queries yet');

    var p = dash.phylaByJob(state.taxa, jobs);
    FDP.plotOrEmpty(Plotly, 'plot-phyla', p.phyla.map(function (ph, i) {
      var colour = ph === 'Unassigned' ? '#a3a3a3' : (ph === 'Non-fungal' ? '#cfd6d5' : PHYLUM_COLORS[i % PHYLUM_COLORS.length]);
      return { type: 'bar', orientation: 'h', name: ph, y: p.labels, x: p.pct[i], marker: { color: colour }, hovertemplate: ph + ': %{x:.1f}%<extra></extra>' };
    }), FDP.baseLayout({ barmode: 'stack', margin: { l: 10, r: 10, t: 10, b: 40 }, xaxis: { range: [0, 100], title: { text: '% of reads' } }, yaxis: { automargin: true }, legend: { orientation: 'h', y: -0.3 } }), 'No taxa in ledger yet');

    renderJobsTable();
  }

  function renderJobsTable() {
    var q = (document.getElementById('jobs-filter').value || '').toLowerCase();
    var tbody = document.querySelector('#jobs-table tbody'); tbody.innerHTML = '';
    var rows = state.jobs.slice().sort(function (a, b) { return String(b.submitted_at).localeCompare(String(a.submitted_at)); });
    if (q) rows = rows.filter(function (j) { return [j.job_id, j.location, j.submitter, j.marker, j.job_type, j.status].join(' ').toLowerCase().indexOf(q) >= 0; });
    if (!rows.length) { tbody.innerHTML = '<tr><td colspan="12" class="empty-note">' + (state.jobs.length ? 'No jobs match the filter' : 'No jobs yet — the ledger is empty') + '</td></tr>'; return; }
    rows.forEach(function (j) {
      var folder = FDP.safeUrl(j.result_folder_url), tr = document.createElement('tr');
      tr.innerHTML = '<td><a href="' + FDP.esc(FDP.jobUrl(j.job_id)) + '"><code>' + FDP.esc(j.job_id) + '</code></a></td>' +
        '<td>' + FDP.esc(j.job_type) + '</td><td class="' + FDP.statusClass(j.status) + '">' + FDP.esc(j.status) + '</td>' +
        '<td>' + FDP.esc(String(j.submitted_at || '').replace('T', ' ').replace(/Z$/, '')) + '</td><td>' + FDP.esc(j.marker) + '</td><td>' + FDP.esc(j.location) + '</td><td>' + FDP.esc(j.submitter) + '</td>' +
        '<td class="num">' + FDP.esc(j.n_samples) + '</td><td class="num">' + (j.n_reads_kept !== '' ? FDP.fmtInt(FDP.num(j.n_reads_kept)) : '') + '</td><td class="num">' + (j.n_asvs !== '' ? FDP.fmtInt(FDP.num(j.n_asvs)) : '') + '</td><td class="num">' + FDP.esc(j.n_species) + '</td>' +
        '<td>' + (folder ? '<a href="' + FDP.esc(folder) + '" target="_blank" rel="noopener">Drive folder</a>' : '') + '</td>';
      tbody.appendChild(tr);
    });
  }
  document.getElementById('jobs-filter').addEventListener('input', renderJobsTable);

  function refresh() {
    FDP.setText('status-msg', '· refreshing…');
    Promise.allSettled(SHEETS.map(function (s) { return FDP.fetchSheet(ledger, s); })).then(function (res) {
      var failed = [];
      res.forEach(function (r, i) { if (r.status === 'fulfilled') state[SHEETS[i]] = r.value; else failed.push(SHEETS[i]); });
      FDP.whenPlotly(function (Plotly) {
        render(Plotly);
        FDP.setText('last-updated', 'last updated ' + FDP.nowHMS());
        FDP.setText('status-msg', failed.length ? '· could not load sheet(s): ' + failed.join(', ') : (state.jobs.length ? '' : '· ledger is empty'));
      });
    });
  }
  refresh();
  setInterval(refresh, 60000);
})();
