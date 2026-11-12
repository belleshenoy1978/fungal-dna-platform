# Apps Script intake (free mode)

`Code.gs` turns a Google Form into the job intake of the platform:

```
Google Form (file uploads -> owner's Drive)
   | onFormSubmit trigger
   v
`queue` tab of the ledger spreadsheet  (status = queued)
   | POST /repos/<repo>/dispatches  (event run-job, {job_id})
   v
GitHub Actions  .github/workflows/run-job.yml  ->  scripts/actions_runner.py
```

Nothing here costs money: Apps Script, Forms, Drive (15 GB free quota of the owner's
account), Sheets and GitHub Actions on a public repository are all free.

## One-time setup (about 10 minutes)

1. Open the ledger spreadsheet
   (`https://docs.google.com/spreadsheets/d/1gpTf1k-i0DBJZDlUR1YQhiFgWCO56pjpSDa4M1cEJjA`)
   as the platform owner and choose **Extensions > Apps Script**. This creates a
   *container-bound* script with access to the spreadsheet.
2. Replace the contents of `Code.gs` with the file in this folder and save.
3. **Project settings (gear icon) > Script properties > Add script property**:

   | property | value |
   |---|---|
   | `GITHUB_REPO` | `<github-user>/fungal-dna-platform` (the public repo that runs the workflow) |
   | `GITHUB_PAT` | fine-grained personal access token, see below (optional but recommended) |
   | `LEDGER_SPREADSHEET_ID` | only if different from the default in the script |

   **Fine-grained PAT**: GitHub > Settings > Developer settings > Personal access tokens >
   Fine-grained tokens > Generate. Resource owner = you, Repository access = *Only select
   repositories* -> the platform repo, Permissions > Repository permissions >
   **Contents: Read and write** (this is the permission `repository_dispatch` requires).
   Choose an expiry; when it expires, jobs are still processed by the 30-minute cron,
   only the instant start is lost. The token is stored in Script Properties, never in code.
4. In the editor select the function **`setup`** and click **Run**. Authorise the
   requested scopes (Forms, Drive, Sheets, external requests, triggers) when asked.
   `setup()`:
   * creates the Form **"Fungal DNA Analysis - submit data"** with the questions listed
     below and sets its response destination to the ledger spreadsheet
     (`FormApp.DestinationType.SPREADSHEET`; responses appear in a tab named
     *Form Responses 1*),
   * creates the `queue` tab with the exact header
     `job_id, job_type, submitted_at, status, submitter, submitter_email, marker,
     fwd_primer, rev_primer, pairing, location, lat, lon, sequences_text, file_ids,
     file_names, form_response_id, started_at, completed_at, result_folder_url,
     result_page_url, error` (= `platform_config.json -> ledger_headers.queue`),
   * installs the installable `onFormSubmit` trigger,
   * stores `FORM_ID`, `FORM_URL`, `FORM_EMBED_URL`, `FORM_EDIT_URL` in Script Properties
     and prints them in the execution log (**View > Executions** or `showUrls()`).
5. **File-upload question.** The documented `FormApp` API cannot create a *File upload*
   question. `setup()` tries `addFileUploadItem()` (present in some runtimes) and, if that
   is unavailable, inserts a placeholder titled `>>> ACTION NEEDED ...` at the right
   position. In that case open `FORM_EDIT_URL`, click **+ (Add question) > File upload**,
   title it exactly **`Upload sequence files`**, set *Allow only specific file types* OFF
   (FASTQ/.ab1 are not in the preset list), *Maximum number of files* = **10**,
   *Maximum file size* = **1 GB**, then run **`verifyForm`** in the editor: it deletes the
   placeholder and reports anything still missing. The trigger locates the upload item
   by type, so the title only matters for humans.
6. Copy `FORM_URL` / `FORM_EMBED_URL` into `platform_config.json -> free_mode` and embed
   `FORM_EMBED_URL` in Google Sites (*Insert > Embed > By URL*).

Re-running `setup()` is safe: the Form (by `FORM_ID`) and trigger are reused.

## Form questions

| title | type | maps to queue column |
|---|---|---|
| Analysis type | multiple choice: Amplicon metabarcoding / DNA barcoding | `job_type` = amplicon / barcode |
| Marker / region | ITS1, ITS2, 18S-V4, 18S-V9, other | `marker` |
| Forward primer (5'-3') | text (help text lists ITS1F/ITS2, fITS7/ITS4, TAReuk454FWD1/TAReukREV3, 1389F/1510R) | `fwd_primer` (letters only, upper-case) |
| Reverse primer (5'-3') | text | `rev_primer` |
| Read layout (amplicon only) | paired-end / single-end | `pairing` |
| Sampling location | text | `location` |
| Latitude / Longitude (decimal degrees) | text with numeric validation | `lat`, `lon` |
| Your name and affiliation | text, required | `submitter` |
| Paste sequences (DNA barcoding only) | paragraph | `sequences_text` (barcode jobs only, max 45 000 characters - a Sheets cell holds 50 000) |
| Upload sequence files | file upload, up to 10 files x 1 GB | `file_ids`, `file_names` (comma-separated) |
| Consent | checkbox "I understand results are published openly under CC BY 4.0", required | - |

The respondent's Google account e-mail is collected (`setCollectEmail(true)`) into
`submitter_email`; it is written only to the `queue` tab, never to the public result
pages. Note that a Form with a file-upload question requires respondents to be signed in
to a Google account (a Google Forms rule, not a choice of this script).

## What `onFormSubmit(e)` writes

One row per submission, `status = queued`, `job_id` = first 12 hex characters of
`Utilities.getUuid()` (same format as the FastAPI app), `submitted_at` in UTC ISO-8601
(`YYYY-MM-DDTHH:MM:SSZ`), `file_ids` = Drive ids returned by the upload item,
`file_names` = their Drive names (Forms renames uploads to `<original> - <respondent>`;
`scripts/actions_runner.py` strips that suffix so R1/R2 mate detection keeps working),
`form_response_id` = `FormResponse.getId()`. Appends are serialised with `LockService`.

Then `dispatchGitHub(job_id)` POSTs
`{"event_type":"run-job","client_payload":{"job_id":"<id>"}}` to
`https://api.github.com/repos/<GITHUB_REPO>/dispatches` with the PAT as a Bearer token
(HTTP 204 = accepted). If `GITHUB_REPO`/`GITHUB_PAT` are missing or the call fails, the
row simply stays `queued` and the workflow's `*/30 * * * *` schedule runs it.

## Manual helpers

* `verifyForm()` - checks upload question, destination, trigger, PAT/repo properties.
* `testDispatch()` - fires a `run-job` dispatch with an empty `job_id` (the workflow then
  drains every queued row); use it to confirm the PAT works (look for HTTP 204 in the log).
* `showUrls()` - prints the stored Form URLs.

## Troubleshooting

| symptom | cause / fix |
|---|---|
| Row appears in `queue` but no Actions run for 30+ min | PAT missing/expired or wrong `GITHUB_REPO`; run `testDispatch()` and read the HTTP code (401 = bad token, 404 = repo name or missing *Contents: write*) |
| `onFormSubmit` error "called without a FormResponse" | the trigger was created as a *spreadsheet* trigger; delete it (Triggers page) and run `setup()` again |
| Runner log: "queue tab header mismatch" | someone edited row 1 of `queue`; restore the header listed above |
| Uploaded file ids but runner reports 404 | the Drive files belong to the Form owner; the OAuth token in `GOOGLE_TOKEN_JSON` must be the same account |
| Amplicon job says "no R2 found" | file names must contain `_1`/`_2` or `_R1`/`_R2` before `.fastq(.gz)` |
