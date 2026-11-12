# Fungal DNA Analysis Platform

Web platform for **DNA barcoding** and **amplicon (metabarcoding) analysis** of fungal sequence data,
built for www.belleshenoy.in (Dr Belle Damodara Shenoy, CSIR-NIO Visakhapatnam). Users upload
Sanger/FASTA barcodes or Illumina FASTQ amplicon libraries; results are published to Google Drive,
logged in a Google Sheet and shown on a live dashboard that the Google Sites page embeds.

## Services

| Route | What it does |
|---|---|
| `/submit` -> `POST /api/amplicon` | Upload paired/single FASTQ(.gz) + marker (ITS1, ITS2, 18S-V4, 18S-V9) + primers. Pipeline: cutadapt primer trimming -> VSEARCH merge -> maxEE 1.0 filter -> dereplication -> UNOISE3 denoising -> UCHIME3 chimera removal -> read mapping -> SINTAX taxonomy against UNITE v10 (eukaryotes). Outputs ASV table, taxonomy, per-sample QC, Shannon, composition. |
| `/barcode` -> `POST /api/barcode` | Paste FASTA or upload FASTA/.ab1 traces. Quality trimming (Mott Q20), orientation, `usearch_global` top-10 hits, SINTAX confidence, `uchime_ref` chimera check; species call at >=98.5 % identity (UNITE SH convention), genus 97-98.5 %, family/order 90-97 %. |
| `/dashboard` | Live public dashboard (Plotly) polling the ledger sheet every 60 s: KPIs, submissions over time, genus leaderboard, phylum composition, sample map, job table. |
| `/jobs/{id}` | Result page per job with downloads. |
| `GET /api/jobs/{id}`, `/health` | JSON status. |

## Two ways to run it

| | Cloud Run (DEPLOY.md) | **Zero-cost / free mode (DEPLOY_FREE.md)** |
|---|---|---|
| Cost | ~USD 50-60/month always-on, or free tier with a billing account | USD 0, no credit card |
| Intake | web upload form (`/submit`, `/barcode`) | Google Form (files land in your Drive) |
| Compute | container worker, live progress | GitHub Actions in a public repo (`.github/workflows/run-job.yml`, `scripts/actions_runner.py`) |
| Trigger | immediate | Apps Script on-submit -> `repository_dispatch` (+ 30-min cron catch-up) |
| Dashboard | served by the app | static GitHub Pages site built from the same templates (`scripts/build_static_site.py` -> `docs/`) |
| Latency | seconds | ~2-4 min barcoding, 5-30 min amplicon |
| Results | Drive + Sheets ledger | Drive + Sheets ledger + `docs/jobs/<id>.json` for job pages |

Both modes share `app/pipeline/*`, `app/storage.py` and the ledger layout; the dashboard reads the same sheet.

## Layout

```
app/            FastAPI application (main, config, storage, jobs, pipeline/, routers/, templates/, static/)
scripts/        entrypoint.sh, fetch_reference.py (UNITE download + SINTAX conversion), make_sintax_db.py,
                authorize_google.py (one-time OAuth), smoke_test.py, seed_demo.py,
                actions_runner.py (free mode queue consumer), build_static_site.py (free mode site)
apps_script/    Code.gs + README: Google Form creation and on-submit trigger (free mode)
.github/        run-job.yml (compute) and pages.yml (GitHub Pages deploy)
docs/           generated static site (GitHub Pages) incl. docs/jobs/<id>.json
tests/          end-to-end tests and reports for each service, test ledger, dashboard preview
Dockerfile      python:3.12-slim + VSEARCH 2.32.0 + cutadapt; served by hypercorn (HTTP/2)
DEPLOY.md       Cloud Run deployment and Google Sites embedding, step by step
platform_config.json   Drive folder IDs, ledger spreadsheet ID, ledger column order
```

## Run locally

```bash
pip install -r requirements.txt
# VSEARCH: https://github.com/torognes/vsearch/releases (set VSEARCH_BIN if not on PATH)
python scripts/fetch_reference.py --out data/ref/unite_sintax.fasta.gz   # 50 MB download, once
export DATA_DIR=./data UNITE_SINTAX_DB=./data/ref/unite_sintax.fasta.gz   # LOCAL_MODE: no Google credentials needed
uvicorn app.main:app --port 8080
```

Without Google credentials the app runs in LOCAL_MODE: results stay under `DATA_DIR/results/<job>`
and the ledger is written as CSV files under `DATA_DIR/ledger/`.

* Free mode: Actions runner in local mode - 70/70 checks (same PRJNA889370 numbers; barcoding 9 queries; failure isolation); static site build 133/133 checks. `tests/test_actions_runner_report.md`, `tests/test_static_site_report.md`

## Tests performed (this sandbox, Windows, VSEARCH 2.32.0, UNITE v10)

* Amplicon: ENA PRJNA889370 (2 ITS1 libraries, Kerala mangrove; files verified against ENA byte counts and MD5):
  140,055 read pairs in -> 126,975 mapped (90.7 %), 127 ASVs (114 fungal), 37 species-level calls, 30 s. `tests/test_amplicon_report.md`
* Barcoding: 7 queries built from 5 real UNITE records - exact, 1 %, 3 %, 8 % mutated, reverse-complement, chimera -
  all assigned at the expected tier; chimera flagged. `tests/test_barcoding_report.md`
* Dashboard/pages: 175 checks (HTTP 200, iframe-safe headers, caveat + citations, ledger header order). `tests/test_dashboard_report.md`

## Implementation notes

* `POST /api/barcode` stores uploads under `DATA_DIR/uploads/barcode/<upload_id>/` and passes the paths in the job params; `barcoding.run_job` copies them into `<job_dir>/input/`. This differs from the amplicon router (which writes straight into `<job_dir>/input/`) because the barcode router saves files before the job row exists; both end in the same place.
* Jobs run one at a time in a worker thread inside the web process (SQLite queue, re-queued after a restart).
* Citations in the footers were checked against Crossref on 12 Nov 2026.

## Caveats

Results are automated and provisional; no negative controls are enforced; verify taxonomic
assignments before publication. 18S markers are classified against UNITE (eukaryote release) unless
`PR2_SINTAX_DB` is provided. Results are public by design.

## Citations

Martin M. (2011) cutadapt. EMBnet.journal 17:10-12. - Rognes T. et al. (2016) VSEARCH. PeerJ 4:e2584. -
Edgar R.C. (2016) SINTAX. bioRxiv 074161. - Abarenkov K. et al. (2025) UNITE general FASTA release for
eukaryotes, DOI 10.15156/BIO/3301231 (CC BY 4.0). - Plotly.js (MIT).

## Licence

Code: MIT. Data produced by the platform: CC BY 4.0 (see LICENSE).
