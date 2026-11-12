# actions_runner local-mode test report

Generated 2026-11-12T09:29:46Z on win32, Python 3.11.18.
Mode: Storage LOCAL_MODE (no Google credentials), queue in CSV (`--local-queue`), file_ids as local paths (`--local-files`). Drive/Sheets writes were NOT exercised.


## 1. Static checks

- PASS: run-job.yml parses as YAML
- PASS: trigger repository_dispatch types [run-job]
- PASS: trigger workflow_dispatch with job_id input
- PASS: schedule cron every 30 min (*/30 * * * *)
- PASS: concurrency group fungal-jobs, cancel-in-progress false
- PASS: permissions contents: write
- PASS: timeout-minutes 350
- PASS: checkout step
- PASS: setup-python 3.12
- PASS: pip install -r requirements.txt
- PASS: VSEARCH 2.32.0 linux-x86_64 download + --version check
- PASS: actions/cache step for UNITE db
- PASS: fetch_reference on cache miss
- PASS: runner invoked with dispatch/input job_id
- PASS: runner env has GOOGLE_SERVICE_ACCOUNT_JSON
- PASS: runner env has VSEARCH_BIN
- PASS: runner env has UNITE_SINTAX_DB
- PASS: runner env has DATA_DIR
- PASS: runner env has PUBLIC_BASE_URL
- PASS: secret GOOGLE_TOKEN_JSON mapped
- PASS: vars.PUBLIC_BASE_URL mapped
- PASS: commit step: bot identity, pull --rebase, skip when unchanged
- PASS: no echo of the Google secret
- PASS: Code.gs balanced {}: 73 / 73
- PASS: Code.gs balanced (): 293 / 293
- PASS: Code.gs balanced []: 33 / 33
- PASS: Code.gs defines setup()
- PASS: Code.gs defines onFormSubmit()
- PASS: Code.gs defines dispatchGitHub()
- PASS: Code.gs defines appendQueueRow()
- PASS: Code.gs sets the Form destination spreadsheet
- PASS: Code.gs posts repository_dispatch
- PASS: Code.gs reads GITHUB_REPO / GITHUB_PAT script properties
- PASS: Code.gs consent text mentions CC BY 4.0
- PASS: Code.gs QUEUE_HEADERS == platform_config ledger_headers.queue

## 2. Barcoding job (local mode)

- PASS: runner exit code 0 on the barcoding run (got 0, 6s)
- PASS: queue row a3dc8f2a300b -> done (got 'done')
- PASS: started_at / completed_at filled
- PASS: result_page_url = https://example.github.io/fungal-dna-platform/job.html?id=a3dc8f2a300b
- PASS: broken job 8da7d1874ff5 -> failed with error text ('ERROR: local input file not found: C:\\Users\\ASUS\\.claude-science\\orgs\\6bb8f004-25fc-43ec-9')
- PASS: input/pasted.fasta written with 2 FASTA records
- PASS: Form ' - Respondent' suffix stripped from the uploaded file name
- PASS: docs/jobs/a3dc8f2a300b.json exists and parses (28418 bytes)
- PASS: compact JSON status done / job_type barcode
- PASS: 9 queries in the barcoding result (7 from the FASTA + 2 pasted; got 9)

Barcoding assignments:

| query | input | identity % | coverage % | best hit | level |
|---|---|---|---|---|---|
| exact_Aspergillus | barcoding_test_queries.fasta | 100.0 | 100.0 | Aspergillus flavus | species |
| exact_Fusarium | barcoding_test_queries.fasta | 100.0 | 100.0 | Fusarium equiseti | species |
| mut1pct_Trichoderma | barcoding_test_queries.fasta | 99.0 | 100.0 | Trichoderma neorufoides | species |
| mut3pct_Rhodotorula | barcoding_test_queries.fasta | 97.1 | 100.0 | Rhodotorula ngohengohe | genus (species uncertain) |
| mut8pct_Penicillium | barcoding_test_queries.fasta | 92.1 | 100.0 | Penicillium sanguifluum | family/order level only |
| revcomp_Aspergillus | barcoding_test_queries.fasta | 100.0 | 100.0 | Aspergillus flavus | species |
| chimera_Asp_Fus | barcoding_test_queries.fasta | 86.0 | 75.5 | Fusarium incarnatum | chimera suspected (not assigned) |
| pasted_1 | pasted.fasta | 100.0 | 100.0 | Aspergillus flavus | species |
| pasted_2 | pasted.fasta | 100.0 | 100.0 | Fusarium equiseti | species |
- PASS: ledger jobs.csv has rows for a3dc8f2a300b
- PASS: ledger barcoding_hits.csv has rows for a3dc8f2a300b
- PASS: failed job also gets a docs/jobs JSON (status failed) so its page can show the error

Timing: 6 s for the whole run (runner start-up + 2 jobs).

## 3. Amplicon job: ENA PRJNA889370 (local mode)

- PASS: ENA SRR21859822_1.fastq.gz: 6106177 bytes, md5 75cbd308a7195bc977d33101a6eae2ac == ENA record
- PASS: ENA SRR21859822_2.fastq.gz: 8279598 bytes, md5 860b21041467741dbf13eeb2603af6af == ENA record
- PASS: ENA SRR21859823_1.fastq.gz: 6716893 bytes, md5 a093bbf38f0f751b3a411d34e008af64 == ENA record
- PASS: ENA SRR21859823_2.fastq.gz: 11106765 bytes, md5 7b87edd55a5fa7f86566f14aacf7633c == ENA record
- PASS: runner exit code 0 on the amplicon run (got 0, 20s)
- PASS: queue row 6fa73b2b07a7 -> done (got 'done'; error='')
- PASS: docs/jobs/6fa73b2b07a7.json exists and parses (54501 bytes)
- PASS: compact copy <= 400 KB

Summary vs reference run:

| metric | this run | reference |
|---|---|---|
| n_reads_in | 140055 | 140055 |
- PASS: summary.n_reads_in == 140055 (got 140055)
| n_reads_kept | 126975 | 126975 |
- PASS: summary.n_reads_kept == 126975 (got 126975)
| n_asvs | 127 | 127 |
- PASS: summary.n_asvs == 127 (got 127)
| n_fungal_asvs | 114 | 114 |
- PASS: summary.n_fungal_asvs == 114 (got 114)
| n_species | 37 | 37 |
- PASS: summary.n_species == 37 (got 37)
- PASS: 2 samples detected from 4 files (got 2)
- sample SRR21859822: in 59580, merged 54653, filtered 54621, non-chimeric 53604, ASVs 91, fungal 90, Shannon 1.2796, % fungal 99.99
- sample SRR21859823: in 80475, merged 75407, filtered 75375, non-chimeric 73371, ASVs 123, fungal 111, Shannon 1.4494, % fungal 98.27
- PASS: ledger jobs.csv has rows for 6fa73b2b07a7
- PASS: ledger samples.csv has rows for 6fa73b2b07a7
- PASS: ledger taxa.csv has rows for 6fa73b2b07a7
- PASS: result file asv_table.tsv uploaded (local copy present)
- PASS: result file asv_taxonomy.tsv uploaded (local copy present)
- PASS: result file asvs.fasta uploaded (local copy present)
- PASS: result file read_tracking.tsv uploaded (local copy present)
- PASS: result file result.json uploaded (local copy present)

Timing: 20 s for the whole run (runner start-up + download/copy + pipeline with 4 threads).

## Timings

- barcode run (2 jobs incl. 1 broken): 6 s
- amplicon run (PRJNA889370, 4 files): 20 s

## Result: 70/70 checks passed
