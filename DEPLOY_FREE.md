# Zero-cost deployment: Google Form + GitHub Actions + GitHub Pages + Google Sites

No server, no credit card. Everything runs on free tiers you already have (Google account) or that are
free for public repositories (GitHub). Trade-off versus the Cloud Run version: results are
asynchronous - a barcoding job returns in about 2-4 minutes, an amplicon job in 5-30 minutes - and the
pipeline code is public (MIT licence; it contains no secrets).

```
 submitter ──Google Form (files)──▶ Google Drive  ◀──results──┐
                 │                                             │
          Apps Script trigger ──▶ ledger sheet `queue` ──▶ GitHub Actions (cutadapt/VSEARCH/UNITE)
                 │                                             │  └─▶ docs/jobs/<id>.json (git commit)
                 └── repository_dispatch ──────────────────────┘
 belleshenoy.in (Google Sites) ──embeds──▶ GitHub Pages: dashboard.html / job.html / submit.html
                                          dashboard polls the ledger sheet's public CSV every 60 s
```

## What already exists
| Item | ID / link |
|---|---|
| Drive root `Fungal-DNA-Platform` | `1k2izmHFgtFuOIyGWjckOUELCkVglR7jJ` |
| `results/` (public) | https://drive.google.com/drive/folders/1otT7ewsBDb5y05BgTb5x71D-QJb4QQUy |
| Ledger spreadsheet (tabs jobs, samples, taxa, barcoding_hits, **queue**) | https://docs.google.com/spreadsheets/d/1gpTf1k-i0DBJZDlUR1YQhiFgWCO56pjpSDa4M1cEJjA/edit?usp=drivesdk |

## Step 1 - GitHub repository (10 min)
1. Create a **public** repository, e.g. `fungal-dna-platform`, and push the contents of the bundle:
   ```bash
   unzip fungal-dna-platform.zip && cd fungal-dna-platform
   git init -b main && git add . && git commit -m "Fungal DNA Analysis Platform"
   git remote add origin https://github.com/<you>/fungal-dna-platform.git && git push -u origin main
   ```
2. Settings -> Pages -> Source: **GitHub Actions**. The `pages.yml` workflow publishes `docs/` at
   `https://<you>.github.io/fungal-dna-platform/`.
3. Settings -> Secrets and variables -> Actions:
   * Secret `GOOGLE_TOKEN_JSON` - contents of `google-token.json` (next step).
   * Variable `PUBLIC_BASE_URL` - your Pages URL (no trailing slash).
4. Settings -> Actions -> General -> Workflow permissions: **Read and write** (the runner commits
   `docs/jobs/<id>.json`).
5. Edit `platform_config.json` -> `free_mode.github_repo` and `github_pages_url`, commit, push.

## Step 2 - Let GitHub Actions write to your Drive (one-time OAuth, 10 min)
Service accounts cannot create files in a personal Drive (zero quota since April 2025), so the
pipeline acts as you through a refresh token:
1. console.cloud.google.com -> new project (no billing needed) -> APIs & Services -> enable
   **Google Drive API** and **Google Sheets API**.
2. OAuth consent screen: External; add your own address as a test user.
3. Credentials -> Create credentials -> OAuth client ID -> **Desktop app** -> download `client_secret.json`.
4. On your computer: `pip install google-auth-oauthlib` then
   `python scripts/authorize_google.py client_secret.json` -> approve in the browser -> `google-token.json`.
5. Paste the file's contents into the GitHub secret `GOOGLE_TOKEN_JSON`. Do not commit it.

## Step 3 - Google Form + trigger (Apps Script, 10 min)
1. Open https://script.google.com -> New project -> replace `Code.gs` with `apps_script/Code.gs`.
2. Project Settings -> Script Properties: `LEDGER_SPREADSHEET_ID` = `1gpTf1k-i0DBJZDlUR1YQhiFgWCO56pjpSDa4M1cEJjA`,
   `GITHUB_REPO` = `<you>/fungal-dna-platform`, `GITHUB_PAT` = a fine-grained personal access token
   (github.com -> Settings -> Developer settings -> Fine-grained tokens; repository access: only this
   repo; permission **Contents: Read and write**; expiry up to 1 year - set a reminder).
3. Run `setup()` once (authorise when asked). It creates the Form, links its responses to the ledger
   spreadsheet, installs the on-submit trigger and logs the Form's **edit**, **public** and **embed** URLs.
   **One manual step:** the Apps Script API cannot create a *File upload* question. Open the Form's edit URL,
   add a question titled exactly `Upload sequence files` of type *File upload* (allow multiple files, 10 files,
   1 GB each, any file type), then run `verifyForm()` in Apps Script - it removes the placeholder and confirms
   the mapping. Details: `apps_script/README.md`.
4. Put the public and embed URLs into `platform_config.json -> free_mode`, run
   `python scripts/build_static_site.py`, commit and push (Pages redeploys automatically).
5. Test: submit the Form yourself with `tests/data/barcoding_test_queries.fasta`. Within ~3 minutes the
   Actions tab shows a run, the `queue` row turns `done`, `results/<job_id>/` appears in Drive, and the
   dashboard shows the job.

Note: Google Forms file-upload questions require respondents to sign in with a Google account
(any Gmail works) - this is a Google Forms rule. Uploaded files count against *your* Drive quota
(15 GB free); the `uploads` are the Form's own folder, so delete old inputs from time to time.

## Step 4 - Google Sites (5 min)
Insert -> Embed -> By URL (choose *Whole page*):
* `https://<you>.github.io/fungal-dna-platform/dashboard.html?embed=1` - live dashboard
* `https://<you>.github.io/fungal-dna-platform/submit.html?embed=1` - submission page (contains the Form)
* or embed the Google Form directly: Insert -> Forms -> pick 'Fungal DNA Analysis - submit data'.
Publish the site.

## Operating notes
* Latency: GitHub runners start in ~30-60 s; the UNITE database is restored from the Actions cache
  (built once, ~2 min). Barcoding ≈ 2-4 min end to end; amplicon depends on data size (2 ITS1 libraries,
  140k read pairs: ~2 min of compute).
* Missed trigger? The workflow also runs every 30 min and processes any `queued` row. GitHub disables
  scheduled workflows after 60 days without commits on public repos - the result commits keep it alive,
  or re-enable it under Actions.
* Limits: ubuntu-latest runner 4 vCPU / 16 GB / 14 GB disk, 6 h per run; a job is skipped with an
  error row if its inputs exceed 10 GB. Actions minutes are unlimited for public repositories.
* Privacy: results are public by design (your choice). Submitter e-mail is stored in the `queue`
  tab only; the `jobs`/dashboard rows carry the free-text name they typed.
* Taking a job down: delete its Drive folder, its rows in the sheets, and `docs/jobs/<id>.json`.
* Keeping both options: the Cloud Run version (DEPLOY.md) remains in the same repository; nothing in
  free mode prevents switching later.

## Troubleshooting
| Symptom | Fix |
|---|---|
| Form submission but no Actions run | Script Properties `GITHUB_PAT`/`GITHUB_REPO` missing or token expired; row stays `queued` and the 30-min cron will pick it up |
| Run fails at 'Google credentials' | `GOOGLE_TOKEN_JSON` secret missing/invalid; re-run `authorize_google.py` |
| `403 storageQuotaExceeded` | a service-account key was used instead of the OAuth token |
| Dashboard shows no jobs | ledger sheet must stay *Anyone with the link: Viewer* (it is today) |
| job.html says 'not found' but queue says done | Pages deploy lag (1-2 min) after the result commit; the page retries |
