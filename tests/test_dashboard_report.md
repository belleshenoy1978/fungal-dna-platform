# Dashboard track — test report

Repository: `fungal-dna-platform` · track: Dashboard (templates, static assets, `app/routers/pages.py`) · date: 2026-11-12

## 1. What was built

| Repo path | Purpose |
|---|---|
| `app/routers/pages.py` | `router = APIRouter()` with GET `/`, `/submit`, `/barcode`, `/dashboard` (`?embed=1`, `?source=`), `/jobs/{job_id}`. Jinja2 templates from `app/templates`. Reads `platform_config.json` at the repo root (override: `PLATFORM_CONFIG_JSON`, `LEDGER_SPREADSHEET_ID`, or `app.config.LEDGER_SPREADSHEET_ID` when the Amplicon track's module is present). Sets only `Cache-Control: no-store`; **no X-Frame-Options, no CSP frame-ancestors, no cookies**. |
| `app/templates/base.html` | Header "Fungal DNA Analysis Platform — belleshenoy.in", nav, footer with the mandatory caveat + cutadapt / VSEARCH / UNITE / SINTAX / Plotly citations, `<meta name="viewport">`. |
| `app/templates/index.html` | Landing page: description, Amplicon / Barcoding / Dashboard cards, live KPI strip read from the ledger `jobs`, `samples`, `barcoding_hits` sheets. |
| `app/templates/submit.html` + `app/static/submit.js` | Multi-file FASTQ(.gz) upload; marker select ITS1 / ITS2 (ITS3-ITS4) / ITS2 (ITS86F-ITS4) / 18S-V4 / 18S-V9 / other with primer presets (ITS1F/ITS2, ITS3/ITS4, ITS86F/ITS4, TAReuk454FWD1/TAReukREV3, 1391F/EukBr); location, lat, lon, submitter, `pairing` (paired/single). `fetch` POST to `/api/amplicon`, polls `/api/jobs/{id}` every 5 s, redirects to `/jobs/{id}` on done, shows notes on failure. |
| `app/templates/barcode.html` + `app/static/barcode.js` | Paste FASTA (`sequences`) or upload FASTA/.ab1 (`files`), `submitter`; POST `/api/barcode`; polls; renders a hit table with confidence badges and links to `/api/barcode/{id}/report.html`. |
| `app/templates/dashboard.html` + `app/static/dashboard.js` | Standalone page. Ledger CSV URL built from `platform_config.json` (`ledger.csv_export_template` + `ledger.spreadsheet_id`) injected as `window.LEDGER_CONFIG` **and** as `data-spreadsheet-id` / `data-csv-template` fallback. Own RFC-4180 CSV parser (quoted fields, doubled quotes, CRLF). Polls every 60 s; "last updated hh:mm:ss"; `Promise.allSettled` so one missing sheet does not blank the page; empty ledger (headers only) → KPI zeros and "No … yet" panels. Panels: 5 KPI tiles, submissions per day (amplicon vs barcode, stacked), top-20 genera by summed reads (taxa sheet), barcoding best-hit genera (barcoding_hits), phylum composition per job (100 % stacked), Plotly `scattergeo` map (India-centred, lat 4–38 / lon 64–100, click → job page), filterable table of all jobs with links to `/jobs/{job_id}` and `result_folder_url`. |
| `app/templates/job.html` + `app/static/job.js` | Fetches `/api/jobs/{id}` (accepts the result under `.result` or inline). Amplicon: summary tiles, read-survival grouped bars (in / merged / filtered / non-chimeric), Shannon bars, phylum + genus stacked composition bars, per-sample table, searchable ASV/taxonomy table (top 500), download links from `result.files`. Barcode: hit table + report link. queued/running → auto-refresh every 5 s; failed → notes + error. |
| `app/static/app.js` | Shared helpers (`FDP.*`): ledger config resolution, CSV parser, KPI aggregation, formatting/escaping, Plotly loader (local bundle first, CDN fallback), job polling. |
| `app/static/plotly.min.js` | Vendored Plotly bundle (see deviations). |
| `app/static/style.css` | Responsive layout, `min-width: 320px`, `body.embed` hides the header for tight Google Sites embeds. |
| `tests/stub_app.py` | Minimal FastAPI app: real `pages` router + `/static`, fake `GET /api/jobs/{id}` from `tests/data/results/<id>/result.json` (+ `stub-queued`, `stub-running`, `stub-failed`), `/test-ledger/{sheet}.csv` and `/test-ledger-empty/{sheet}.csv`. |
| `tests/make_test_data.py` | Builds the test ledger and result JSONs from the earlier project results (`tests/data/source/*.tsv`). |
| `tests/test_dashboard_pages.py` | Page/asset/schema/ledger checks → `tests/test_dashboard_pages_results.json`. |
| `tests/render_dashboard_preview.py` | Matplotlib rendering of the dashboard panels → `tests/dashboard_preview.png` + `.json` with the plotted numbers. |

## 2. Test data (from the project's earlier Indian coastal eDNA results)

Jobs sheet (`tests/data/ledger/jobs.csv`):

| job_id | type | marker | location | samples | reads in | reads kept | ASVs | fungal ASVs | species |
|---|---|---|---|---|---|---|---|---|---|
| `ebe1f6d12027` | amplicon | ITS2 | Bhitarkanika mangrove, Odisha (east coast) | 4 | 1180368 | 543648 | 2745 | 1605 | 147 |
| `cabb8aa86f74` | amplicon | ITS1 | Kerala coast (west coast) | 2 | 140055 | 126975 | 127 | 114 | 37 |
| `demo-barcode` | barcode | ITS | — | 3 | 3 | 3 | — | — | 2 |

Samples sheet (`tests/data/ledger/samples.csv`):

| sample | job | reads_in | merged | filtered | non-chimeric (mapped) | ASVs | Shannon | % fungal |
|---|---|---|---|---|---|---|---|---|
| Dangmal | ebe1f6d12027 | 358716 | 198060 | 185120 | 180328 | 1090 | 4.696 | 58.9 |
| Gupti | ebe1f6d12027 | 250208 | 128092 | 116634 | 106854 | 1044 | 4.383 | 58.6 |
| Habalikhati | ebe1f6d12027 | 250864 | 149110 | 140641 | 138130 | 848 | 4.461 | 57.6 |
| Kalibhanjadiha | ebe1f6d12027 | 320580 | 125524 | 121053 | 118336 | 904 | 4.841 | 88.0 |
| Ernakulam | cabb8aa86f74 | 80475 | 75407 | 75375 | 73371 | 123 | 1.449 | 98.3 |
| Kannur | cabb8aa86f74 | 59580 | 54653 | 54621 | 53604 | 91 | 1.28 | 100.0 |

Mapping from the source tables: `reads_in` = `raw_read_pairs` (diversity_summary), `reads_merged` / `reads_filtered` from read_tracking, `reads_nonchimeric` = column sum of the ASV table (reads mapped to non-chimeric ASVs), `shannon` and `pct_fungal` from diversity_summary, `n_species` = distinct non-null fungal species names. The source tables carry no SINTAX confidence column, so `sintax_conf` is `null` in result.json and empty in the taxa sheet. Ledger `taxa` sheet holds the top 200 ASVs per job (627 rows total: 4 samples × ≤200 ASVs for ITS2, 2 × ≤200 for ITS1), result.json holds the top 500. `barcoding_hits` holds 3 rows for job_id `demo-barcode` whose query ids start with `SYNTHETIC_` — these are invented and only exist so the panel renders.

## 3. Page tests — 175/175 checks passed

Client mode: **in-process fastapi.testclient.TestClient (loopback sockets blocked in sandbox)**. The script first tries a real uvicorn server on 127.0.0.1:8012 and falls back to FastAPI's in-process `TestClient` (same ASGI app, routers, templates and response headers) when loopback sockets are blocked, as they are in this sandbox.

| Check group | passed |
|---|---|
| client mode | 1/1 |
| landing | 12/12 |
| submit | 22/22 |
| barcode | 12/12 |
| dashboard | 21/21 |
| dashboard-embed | 8/8 |
| job ebe1f6d12027 | 12/12 |
| job cabb8aa86f74 | 12/12 |
| job demo-barcode | 12/12 |
| job stub-queued | 12/12 |
| job stub-failed | 12/12 |
| static /static/style.css | 1/1 |
| static /static/app.js | 1/1 |
| static /static/dashboard.js | 1/1 |
| static /static/job.js | 1/1 |
| static /static/submit.js | 1/1 |
| static /static/barcode.js | 1/1 |
| static /static/plotly.min.js | 1/1 |
| submit.js primer preset CTTGGTCATTTAGAGGAAGTAA | 1/1 |
| submit.js primer preset GCTGCGTTCTTCATCGATGC | 1/1 |
| submit.js primer preset GCATCGATGAAGAACGCAGC | 1/1 |
| submit.js primer preset TCCTCCGCTTATTGATATGC | 1/1 |
| submit.js primer preset GTGAATCATCGAATCTTTGAA | 1/1 |
| submit.js primer preset CCAGCASCYGCGGTAATTCC | 1/1 |
| submit.js primer preset ACTTTCGTTCTTGATYRA | 1/1 |
| submit.js primer preset GTACACACCGCCCGTC | 1/1 |
| submit.js primer preset TGATCCTTCTGCAGGTTCACCTAC | 1/1 |
| api ebe1f6d12027 | 3/3 |
| api cabb8aa86f74 | 3/3 |
| api demo-barcode | 2/2 |
| ledger jobs | 2/2 |
| empty ledger jobs | 1/1 |
| ledger samples | 2/2 |
| empty ledger samples | 1/1 |
| ledger taxa | 3/3 |
| empty ledger taxa | 1/1 |
| ledger barcoding_hits | 3/3 |
| empty ledger barcoding_hits | 1/1 |
| 404 for unknown job | 1/1 |

Checks per page: HTTP 200, no `X-Frame-Options`, no `frame-ancestors` CSP, no `Set-Cookie`, viewport meta, caveat sentence, all five citations, key element ids/names; dashboard additionally: spreadsheet id `1gpTf1k-i0DBJZDlUR1YQhiFgWCO56pjpSDa4M1cEJjA` present in `LEDGER_CONFIG` and in the data attribute, CSV template `…/gviz/tq?tqx=out:csv&sheet={sheet}` present, every panel container present, vendored Plotly path plus CDN fallback URL present. Ledger CSVs: header order byte-identical to `platform_config.json` `ledger_headers` for all four sheets; empty variants are headers-only; taxa ≤ 200 ASVs per job; result.json files carry all schema keys and ≤ 500 taxa.

## 4. Dashboard preview

`tests/dashboard_preview.png` is a **matplotlib rendering of the same ledger CSVs, not a browser screenshot** (headless browsers crash in the build sandbox and no JS runtime is available). The aggregations in `tests/render_dashboard_preview.py` mirror `FDP.kpis`, `dash.submissionsPerDay`, `dash.generaByReads`, `dash.barcodeGenera`, `dash.phylaByJob` and `dash.mapPoints` in `dashboard.js`. Numbers plotted (from `tests/dashboard_preview.json`):

- KPIs: jobs 3, samples 6, ASVs 2,872, species-level assignments 184, barcoding queries 3.
- Submissions: 2026-11-10 1 amplicon, 2026-11-11 1 amplicon, 2026-11-12 1 barcode.
- Top genera by reads (top-200 ASVs per job): *Talaromyces* (86,066), *Nigrospora* (12,137), *Candolleomyces* (11,314), *Penicillium* (10,392), *Fusarium* (6,008).
- Phylum composition, ITS2 Bhitarkanika: Ascomycota 45.2 %, Unassigned 20.1 %, Non-fungal 19.7 %, Basidiomycota 13.8 %; ITS1 Kerala: Ascomycota 96.9 %.
- Map points: Bhitarkanika 20.7 N 86.9 E (4 samples), Kerala 10.0 N 76.3 E (2 samples). The preview draws a schematic India outline on plain lat/lon axes; the live page uses Plotly `scattergeo` with real coastlines.

## 5. Deviations and open points for the lead agent

1. **Plotly bundle version.** `cdn.plot.ly` is not on the sandbox allowlist, so `app/static/plotly.min.js` was vendored from the `plotly` Python package (plotly 7.1.0 → **plotly.js v4.1.1**, 4.8 MB), not 2.35.2. The CDN fallback in the templates still points to `plotly-2.35.2.min.js`. Only stable APIs are used (`Plotly.react`, `bar`, `scattergeo`, `layout.geo`, `title: {text}`), which exist in both versions. Replace the vendored file with 2.35.2 at assembly time if version parity matters.
2. **Test transport.** Loopback TCP is blocked in this sandbox, so the page tests ran through FastAPI's in-process `TestClient`, not a real socket on 127.0.0.1:8012. The script still tries the socket server first and will use it on an ordinary machine.
3. **JavaScript was not executed.** No browser, Node or Python-JS engine could be installed here. The JS files passed a bracket-balance check only (one false positive on the regex literal `/[&<>"']/g` in `app.js`, confirmed by inspection); the aggregation logic was validated through its Python mirror. A browser smoke test of `/dashboard?source=/test-ledger/{sheet}.csv` against `tests/stub_app.py` is recommended on first deploy.
4. **`sintax_conf` is null** in the test result.json/taxa sheet (source tables lack it); real pipeline output will populate it.
5. **Field name for pairing mode:** the submit form sends `pairing` = `paired` | `single`. The Amplicon track's `POST /api/amplicon` must accept that name (or the lead should rename it in `submit.html`/`submit.js`).
6. **Jinja2Templates directory** is resolved from `__file__` (`app/templates` absolute), equivalent to the contract's relative `'app/templates'` but independent of the process CWD.
7. **`/api/jobs/{id}` shape:** `job.js` accepts the result either nested under `result` (what `tests/stub_app.py` returns) or inline in the job dict.
8. The ledger must be shared "anyone with the link can view" for the gviz CSV export to be readable cross-origin; the pages fetch it with `credentials: 'omit'`.

## 5b. Citation check

The footer's bibliographic details were verified against Crossref on 2026-11-12: Martin 2011 → EMBnet.journal 17(1):10, doi:10.14806/ej.17.1.200; Rognes et al. 2016 → PeerJ 4:e2584, doi:10.7717/peerj.2584; Edgar 2016 → bioRxiv preprint posted 2016-09-09, doi:10.1101/074161; Abarenkov et al. 2024 → Nucleic Acids Research 52(D1):D791–D797, doi:10.1093/nar/gkad1039 (Crossref `issued` year is 2023 for the online-first record; the task's author-year "2024" is the issue year). The DOIs are linked in `base.html` and `dashboard.html`.

## 6. How to run

```
cd fungal-dna-platform
python tests/make_test_data.py            # rebuild tests/data/ledger + tests/data/results
python tests/test_dashboard_pages.py      # page tests (writes tests/test_dashboard_pages_results.json)
python tests/render_dashboard_preview.py  # tests/dashboard_preview.png + .json
python -m uvicorn tests.stub_app:app --port 8012
#  then open http://127.0.0.1:8012/dashboard?source=/test-ledger/{sheet}.csv
#            http://127.0.0.1:8012/dashboard?source=/test-ledger-empty/{sheet}.csv   (empty-ledger state)
#            http://127.0.0.1:8012/jobs/ebe1f6d12027   /jobs/demo-barcode   /jobs/stub-running   /jobs/stub-failed
```
