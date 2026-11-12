# Static site (GitHub Pages / free mode) test report

Checked: docs/ · **133/133 checks passed**

Method: no browser, no network. HTML/JS checked as text; JS bracket balance by a string/regex/comment-aware scan (Node unavailable; `plotly.min.js` vendored bundle not scanned); job-page fetch logic mirrored in Python (`./jobs/<id>.json` → ledger `queue` tab → `jobs` tab) against `docs/jobs/*.json` and CSV fixtures; FastAPI pages re-rendered with `fastapi.testclient` against `tests/stub_app.py` where FastAPI is installed.

Not exercised here: live Google Sheets CSV polling in a browser, the Google Form embed (URL not configured yet), GitHub Pages deployment itself.

| # | Check | Result | Detail |
|---|---|---|---|
| 1 | docs: index.html exists | PASS |  |
| 2 | docs: dashboard.html exists | PASS |  |
| 3 | docs: job.html exists | PASS |  |
| 4 | docs: submit.html exists | PASS |  |
| 5 | docs: .nojekyll exists | PASS |  |
| 6 | docs: jobs/README.md exists | PASS |  |
| 7 | docs: static/app.js exists | PASS |  |
| 8 | docs: static/dashboard.js exists | PASS |  |
| 9 | docs: static/job.js exists | PASS |  |
| 10 | docs: static/barcode.js exists | PASS |  |
| 11 | docs: static/submit.js exists | PASS |  |
| 12 | docs: static/config.js exists | PASS |  |
| 13 | docs: static/style.css exists | PASS |  |
| 14 | docs: static/plotly.min.js exists | PASS |  |
| 15 | docs: jobs/ebe1f6d12027.json exists | PASS |  |
| 16 | docs: jobs/cabb8aa86f74.json exists | PASS |  |
| 17 | docs: jobs/demo-barcode.json NOT published (synthetic data) | PASS |  |
| 18 | docs: index.html has caveat footer | PASS |  |
| 19 | docs: index.html has five citations | PASS | missing [] |
| 20 | docs: index.html has DOI links for cutadapt/VSEARCH/UNITE/SINTAX | PASS |  |
| 21 | docs: index.html has no X-Frame-Options / frame-ancestors meta | PASS |  |
| 22 | docs: index.html has viewport meta (responsive) | PASS |  |
| 23 | docs: index.html uses only relative links (no /static, no absolute routes) | PASS |  |
| 24 | docs: index.html loads static/config.js before app.js | PASS |  |
| 25 | docs: index.html has no cookies / storage | PASS |  |
| 26 | docs: index.html has no leftover Jinja syntax | PASS |  |
| 27 | docs: index.html links the public ledger | PASS |  |
| 28 | docs: dashboard.html has caveat footer | PASS |  |
| 29 | docs: dashboard.html has five citations | PASS | missing [] |
| 30 | docs: dashboard.html has DOI links for cutadapt/VSEARCH/UNITE/SINTAX | PASS |  |
| 31 | docs: dashboard.html has no X-Frame-Options / frame-ancestors meta | PASS |  |
| 32 | docs: dashboard.html has viewport meta (responsive) | PASS |  |
| 33 | docs: dashboard.html uses only relative links (no /static, no absolute routes) | PASS |  |
| 34 | docs: dashboard.html loads static/config.js before app.js | PASS |  |
| 35 | docs: dashboard.html has no cookies / storage | PASS |  |
| 36 | docs: dashboard.html has no leftover Jinja syntax | PASS |  |
| 37 | docs: dashboard.html links the public ledger | PASS |  |
| 38 | docs: job.html has caveat footer | PASS |  |
| 39 | docs: job.html has five citations | PASS | missing [] |
| 40 | docs: job.html has DOI links for cutadapt/VSEARCH/UNITE/SINTAX | PASS |  |
| 41 | docs: job.html has no X-Frame-Options / frame-ancestors meta | PASS |  |
| 42 | docs: job.html has viewport meta (responsive) | PASS |  |
| 43 | docs: job.html uses only relative links (no /static, no absolute routes) | PASS |  |
| 44 | docs: job.html loads static/config.js before app.js | PASS |  |
| 45 | docs: job.html has no cookies / storage | PASS |  |
| 46 | docs: job.html has no leftover Jinja syntax | PASS |  |
| 47 | docs: job.html links the public ledger | PASS |  |
| 48 | docs: submit.html has caveat footer | PASS |  |
| 49 | docs: submit.html has five citations | PASS | missing [] |
| 50 | docs: submit.html has DOI links for cutadapt/VSEARCH/UNITE/SINTAX | PASS |  |
| 51 | docs: submit.html has no X-Frame-Options / frame-ancestors meta | PASS |  |
| 52 | docs: submit.html has viewport meta (responsive) | PASS |  |
| 53 | docs: submit.html uses only relative links (no /static, no absolute routes) | PASS |  |
| 54 | docs: submit.html loads static/config.js before app.js | PASS |  |
| 55 | docs: submit.html has no cookies / storage | PASS |  |
| 56 | docs: submit.html has no leftover Jinja syntax | PASS |  |
| 57 | docs: submit.html links the public ledger | PASS |  |
| 58 | docs: dashboard.html links job.html?id= | PASS |  |
| 59 | docs: dashboard.html keeps #dashboard data-attributes (ledger fallback) | PASS |  |
| 60 | docs: dashboard.html loads dashboard.js (60 s poll) | PASS |  |
| 61 | docs: job.html references ./jobs/ | PASS |  |
| 62 | docs: job.html has empty data-job-id (id comes from ?id=) | PASS |  |
| 63 | docs: job.html mentions queue tab fallback + 30 s refresh | PASS |  |
| 64 | docs: submit.html explains both analysis types | PASS |  |
| 65 | docs: submit.html lists accepted inputs | PASS |  |
| 66 | docs: submit.html carries the public-results notice | PASS |  |
| 67 | docs: submit.html shows 'form not configured yet' placeholder (config still has <...> placeholder) | PASS |  |
| 68 | docs: index.html has live KPI strip + cards to submit.html / dashboard.html | PASS |  |
| 69 | docs: config.js parses as JSON | PASS |  |
| 70 | docs: config.js mode=static | PASS |  |
| 71 | docs: config.js carries ledger id 1gpTf1k-i0DBJZDlUR1YQhiFgWCO56pjpSDa4M1cEJjA | PASS |  |
| 72 | docs: config.js carries the CSV template | PASS |  |
| 73 | docs: config.js carries queue sheet name + status values | PASS |  |
| 74 | docs: config.js job_page / jobs_json_path | PASS |  |
| 75 | docs: config.js has Pages base URL + form URL keys (empty until configured) | PASS |  |
| 76 | docs: config.js contains no secrets-looking keys | PASS |  |
| 77 | docs: platform_config.json queue header matches spec order | PASS |  |
| 78 | docs: static/app.js bracket balance | PASS | balanced |
| 79 | docs: static/dashboard.js bracket balance | PASS | balanced |
| 80 | docs: static/job.js bracket balance | PASS | balanced |
| 81 | docs: static/barcode.js bracket balance | PASS | balanced |
| 82 | docs: static/submit.js bracket balance | PASS | balanced |
| 83 | docs: static/config.js bracket balance | PASS | balanced |
| 84 | docs: app.js reads window.PLATFORM_CONFIG and keeps FastAPI fallback | PASS |  |
| 85 | docs: app.js static job lookup: jobs json -> queue -> jobs tab | PASS |  |
| 86 | docs: app.js static poll interval 30 s | PASS |  |
| 87 | docs: app.js applies ?embed=1 body class | PASS |  |
| 88 | docs: dashboard.js job links via FDP.jobUrl (no hard-coded /jobs/) | PASS |  |
| 89 | docs: dashboard.js polls every 60000 ms | PASS |  |
| 90 | docs: dashboard.js links result_folder_url as 'Drive folder' | PASS |  |
| 91 | docs: job.js reads id from query string (FDP.currentJobId) | PASS |  |
| 92 | docs: static/app.js identical to app/static/app.js | PASS |  |
| 93 | docs: static/dashboard.js identical to app/static/dashboard.js | PASS |  |
| 94 | docs: static/job.js identical to app/static/job.js | PASS |  |
| 95 | docs: static/barcode.js identical to app/static/barcode.js | PASS |  |
| 96 | docs: static/submit.js identical to app/static/submit.js | PASS |  |
| 97 | docs: style.css responsive down to 320 px | PASS |  |
| 98 | docs: style.css styles the form iframe + placeholder | PASS |  |
| 99 | docs: jobs/cabb8aa86f74.json <= 400 KB | PASS | 32534 B |
| 100 | docs: jobs/cabb8aa86f74.json job_id matches filename | PASS |  |
| 101 | docs: jobs/cabb8aa86f74.json has every field job.html reads | PASS | missing [] |
| 102 | docs: jobs/cabb8aa86f74.json file links are http(s) or root-relative | PASS | [] |
| 103 | docs: mirror fetch jobs/cabb8aa86f74.json -> status done from jobs_json (terminal) | PASS |  |
| 104 | docs: jobs/cabb8aa86f74.json summary/samples identical to tests/data/results source | PASS |  |
| 105 | docs: jobs/cabb8aa86f74.json keeps all 127 taxa rows (no truncation needed) | PASS |  |
| 106 | docs: jobs/cabb8aa86f74.json marker/location: ITS1 / Kerala coast (west coast) | PASS |  |
| 107 | docs: jobs/ebe1f6d12027.json <= 400 KB | PASS | 116934 B |
| 108 | docs: jobs/ebe1f6d12027.json job_id matches filename | PASS |  |
| 109 | docs: jobs/ebe1f6d12027.json has every field job.html reads | PASS | missing [] |
| 110 | docs: jobs/ebe1f6d12027.json file links are http(s) or root-relative | PASS | [] |
| 111 | docs: mirror fetch jobs/ebe1f6d12027.json -> status done from jobs_json (terminal) | PASS |  |
| 112 | docs: jobs/ebe1f6d12027.json summary/samples identical to tests/data/results source | PASS |  |
| 113 | docs: jobs/ebe1f6d12027.json keeps all 500 taxa rows (no truncation needed) | PASS |  |
| 114 | docs: jobs/ebe1f6d12027.json marker/location: ITS2 / Bhitarkanika mangrove, Odisha (east coast) | PASS |  |
| 115 | docs: compact_result keeps every barcode field job.html reads (demo-barcode, not published) | PASS | [] |
| 116 | docs: compact_result truncates an oversized result under 400 KB with a note | PASS | 357052 B, 1536 taxa kept |
| 117 | docs: mirror fallback: queued job -> status queued, keeps polling | PASS |  |
| 118 | docs: mirror fallback: running job -> status running | PASS |  |
| 119 | docs: mirror fallback: failed job -> terminal with error text | PASS |  |
| 120 | docs: mirror fallback: done without json -> folder link from jobs tab, keeps polling for Pages deploy | PASS |  |
| 121 | docs: mirror fallback: unknown id -> status unknown (keeps polling) | PASS |  |
| 122 | docs: mirror fallback: job only in jobs tab (test ledger) -> done + Drive folder | PASS |  |
| 123 | docs: FastAPI / still renders (200, defaults, no config.js tag) | PASS |  |
| 124 | docs: FastAPI /submit still renders (200, defaults, no config.js tag) | PASS |  |
| 125 | docs: FastAPI /barcode still renders (200, defaults, no config.js tag) | PASS |  |
| 126 | docs: FastAPI /dashboard still renders (200, defaults, no config.js tag) | PASS |  |
| 127 | docs: FastAPI /jobs/ebe1f6d12027 still renders (200, defaults, no config.js tag) | PASS |  |
| 128 | docs: FastAPI /dashboard?embed=1 keeps server-side embed class | PASS |  |
| 129 | pages.yml exists | PASS |  |
| 130 | pages.yml parses; triggers on push(main, docs/**) + workflow_dispatch | PASS |  |
| 131 | pages.yml permissions pages: write, id-token: write | PASS |  |
| 132 | pages.yml build runs scripts/build_static_site.py before upload-pages-artifact | PASS |  |
| 133 | pages.yml uploads docs/ and deploys with actions/deploy-pages | PASS |  |
