# Deploying the Fungal DNA Analysis Platform (Google Cloud Run + Google Drive + Google Sites)

This guide takes the repository from zero to a public service embedded in **www.belleshenoy.in**.
Everything that users upload or that the pipeline produces is stored in **your Google Drive**
(folder `Fungal-DNA-Platform`) and logged in the Google Sheet **fungal-platform-ledger**; the live
dashboard reads that sheet. Expect 30-45 minutes the first time.

## What already exists (created on 12 Nov 2026)

| Item | ID / link |
|---|---|
| Drive root folder `Fungal-DNA-Platform` | `1k2izmHFgtFuOIyGWjckOUELCkVglR7jJ` |
| `uploads/` (private) | `19O9SW_KsbzeY8C5o4Rfjgsowe2z9IJBN` |
| `results/` (public, anyone with link) | https://drive.google.com/drive/folders/1otT7ewsBDb5y05BgTb5x71D-QJb4QQUy |
| `reference/` (public) | https://drive.google.com/drive/folders/1FYgsu73qMZqMDqYGMadp_4ge38Flm47k |
| `public/` (public) | https://drive.google.com/drive/folders/11yFpO3HI-cDALuKLvk1V8R_tLlI6-Y41 |
| Ledger spreadsheet (public read) | https://docs.google.com/spreadsheets/d/1gpTf1k-i0DBJZDlUR1YQhiFgWCO56pjpSDa4M1cEJjA/edit?usp=drivesdk |

`platform_config.json` in the repo root carries these IDs; the container reads its defaults from it.

## 0. Prerequisites on your computer

* `gcloud` CLI (https://cloud.google.com/sdk/docs/install) and `python3`.
* A Google Cloud project with **billing enabled** (Cloud Run has a free tier; a 2 vCPU / 4 GiB
  always-on instance costs roughly USD 50-60/month at `--min-instances 1`; set
  `--min-instances 0` to pay only when used, at the price of a cold start and of jobs being
  lost if the instance stops mid-run).

```bash
gcloud auth login
export PROJECT_ID=<your-project-id>
gcloud config set project $PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    drive.googleapis.com sheets.googleapis.com secretmanager.googleapis.com storage.googleapis.com
```

## 1. Let the service write to your Drive (one-time OAuth)

Google service accounts created after April 2025 have **no Drive storage quota** and cannot
create files inside a My Drive folder (`403 storageQuotaExceeded`). The platform therefore acts
as *you* through an OAuth refresh token:

1. Console -> APIs & Services -> OAuth consent screen: External, add your own address as a test user.
2. Credentials -> Create credentials -> OAuth client ID -> **Desktop app** -> download `client_secret.json`.
3. On your computer:
   ```bash
   pip install google-auth-oauthlib
   python scripts/authorize_google.py client_secret.json      # browser opens, approve Drive + Sheets
   ```
   This writes `google-token.json`. Keep it private (it is git-ignored).
4. Store it as a secret:
   ```bash
   gcloud secrets create fungal-platform-google-token --data-file=google-token.json
   ```

*Alternative (Google Workspace accounts only):* create a **Shared Drive**, move the
`Fungal-DNA-Platform` folder into it, create a service account, share the Shared Drive and the
ledger sheet with it as Editor, and store its key JSON in the same secret. The code accepts both
credential types.

## 2. Deploy

```bash
export REGION=asia-south1                       # Mumbai
export BUCKET=$PROJECT_ID-fungal-platform-data  # keeps the UNITE database between restarts
gsutil mb -l $REGION gs://$BUCKET

RUN_SA=$(gcloud projects describe $PROJECT_ID --format 'value(projectNumber)')-compute@developer.gserviceaccount.com
gcloud secrets add-iam-policy-binding fungal-platform-google-token \
    --member serviceAccount:$RUN_SA --role roles/secretmanager.secretAccessor
gsutil iam ch serviceAccount:$RUN_SA:objectAdmin gs://$BUCKET

gcloud run deploy fungal-dna-platform --source . --region $REGION --platform managed \
  --allow-unauthenticated --use-http2 \
  --cpu 2 --memory 4Gi --timeout 3600 --concurrency 20 \
  --min-instances 1 --max-instances 1 --no-cpu-throttling --execution-environment gen2 \
  --add-volume name=refdata,type=cloud-storage,bucket=$BUCKET \
  --add-volume-mount volume=refdata,mount-path=/data/ref \
  --set-secrets GOOGLE_SERVICE_ACCOUNT_JSON=fungal-platform-google-token:latest \
  --set-env-vars DRIVE_RESULTS_FOLDER_ID=1otT7ewsBDb5y05BgTb5x71D-QJb4QQUy,DRIVE_UPLOADS_FOLDER_ID=19O9SW_KsbzeY8C5o4Rfjgsowe2z9IJBN,DRIVE_REFERENCE_FOLDER_ID=1FYgsu73qMZqMDqYGMadp_4ge38Flm47k,LEDGER_SPREADSHEET_ID=1gpTf1k-i0DBJZDlUR1YQhiFgWCO56pjpSDa4M1cEJjA,DATA_DIR=/data,UNITE_SINTAX_DB=/data/ref/unite_sintax.fasta.gz

URL=$(gcloud run services describe fungal-dna-platform --region $REGION --format 'value(status.url)')
gcloud run services update fungal-dna-platform --region $REGION --update-env-vars PUBLIC_BASE_URL=$URL
echo $URL
```

`deploy.sh` wraps the same commands (`PROJECT_ID=... ./deploy.sh`).

Why these flags:

* `--use-http2` - Cloud Run accepts request bodies above 32 MiB only when the container speaks
  HTTP/2 end-to-end; the image serves h2c with hypercorn, so FASTQ uploads of hundreds of MB work.
* `--no-cpu-throttling`, `--min-instances 1`, `--max-instances 1` - the analysis worker runs inside
  the web process between requests and uses one SQLite queue; one always-on instance keeps that
  consistent. Jobs run sequentially.
* Cloud Storage volume at `/data/ref` - the first start downloads the UNITE v10 eukaryote release
  (50 MB, DOI 10.15156/BIO/3301231), converts it to SINTAX format (~20 MB gz) and leaves it on the
  bucket, so later restarts skip the 2-3 minute build. `/health` reports `unite_db_present`.

## 3. Verify and seed

```bash
python scripts/smoke_test.py $URL     # health, pages, one barcoding job, Drive publication
python scripts/seed_demo.py  $URL     # optional: submits the public ENA ITS1 demonstrator (PRJNA889370)
```

Open `$URL/dashboard`; the ledger sheet now shows rows and the dashboard updates within 60 s.

## 4. Put it on www.belleshenoy.in (Google Sites)

Google Sites cannot run code or receive uploads itself; it embeds the service by URL.

1. Open the site in the Google Sites editor, create pages e.g. *DNA Analysis Platform*,
   *Submit amplicon data*, *DNA barcoding*, *Live dashboard* (or sections on one page).
2. On each page: **Insert -> Embed -> By URL**, paste the matching address and choose *Whole page*:
   * `<service-url>/dashboard?embed=1` (live dashboard)
   * `<service-url>/submit?embed=1` (amplicon upload)
   * `<service-url>/barcode?embed=1` (barcoding)
   `?embed=1` hides the platform's own header so only your site's navigation shows.
3. Drag the embed to full width and about 1400 px tall (dashboard) / 900 px (forms); Publish.
4. If Sites shows a blank box, use **Insert -> Embed -> Embed code** with:
   ```html
   <iframe src="<service-url>/dashboard?embed=1" style="width:100%;height:1400px;border:0" loading="lazy"></iframe>
   ```
   The service sends no `X-Frame-Options`/CSP frame restrictions, needs no cookies, and the
   dashboard reads the ledger through the sheet's public CSV export, so cross-origin embedding works.

Optional custom address: Cloud Run -> *Manage custom domains* -> map `tools.belleshenoy.in`
(add the CNAME Google shows at your DNS provider), then embed the `tools.` URLs instead.

## 5. Operating notes

* Results are **public by design** (your choice): every job folder under `results/` is
  anyone-with-link, and the ledger is world-readable. Tell submitters on the form (the templates do).
* To take a job down: delete its folder in Drive and its rows in the sheet; the dashboard follows.
* Reference refresh: delete `unite_sintax.fasta.gz` from the bucket and update `UNITE_URL` /
  `UNITE_BYTES` in `scripts/fetch_reference.py` when UNITE publishes a new release.
* Logs: `gcloud run services logs read fungal-dna-platform --region $REGION`.
* Costs: Cloud Run instance + Cloud Build minutes + Drive storage (your quota). Cloud Storage holds ~20 MB.

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `/health` -> `service_account_configured: false` | secret not mounted; check `--set-secrets` and the secret accessor binding |
| Drive upload `403 storageQuotaExceeded` | you used a service account against a My Drive folder; use the OAuth token (section 1) or a Shared Drive |
| Sheets `403 PERMISSION_DENIED` | the credential's account is not an editor of the ledger (OAuth token = owner, so this only happens with a service account) |
| Upload fails with 413 | `--use-http2` missing on the service |
| Dashboard empty but ledger has rows | sheet not shared *anyone with link* (it is, today); check the browser console for the CSV URL |
| Job stays `queued` | instance restarted mid-run; jobs re-queue automatically at start-up (`--min-instances 1` avoids this) |
