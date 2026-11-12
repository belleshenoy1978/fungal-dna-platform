# Amplicon service — end-to-end test report (LOCAL_MODE)

Date: 2026-11-12 · Host: native Windows sandbox (12 CPU, 32 GiB), conda env `python` (Python 3.11.18) ·
VSEARCH 2.32.0 (Windows build, resolved via `VSEARCH_BIN`) · cutadapt 5.2 (`python -m cutadapt -j 1`) ·
Reference: UNITE v10 eukaryote release in SINTAX format (`unite10_sintax.fasta.gz`, 21 MB) · FastAPI 0.162.5 / uvicorn 0.54.0

## Demonstrator data (real reads, verified)

ENA study **PRJNA889370** (Kerala mangrove sediment, ITS1 amplicons, primers ITS1F / ITS2). File list from
`https://www.ebi.ac.uk/ena/portal/api/filereport?accession=PRJNA889370&result=read_run&fields=run_accession,fastq_ftp,fastq_bytes,fastq_md5`;
files downloaded over HTTPS from `ftp.sra.ebi.ac.uk` and verified against ENA `fastq_bytes` **and** `fastq_md5` before use.

| run | sample_alias | file | bytes (ENA) | MD5 match |
|---|---|---|---|---|
| SRR21859823 | EKM | SRR21859823_1.fastq.gz | 6,716,893 | yes |
| SRR21859823 | EKM | SRR21859823_2.fastq.gz | 11,106,765 | yes |
| SRR21859822 | KAN | SRR21859822_1.fastq.gz | 6,106,177 | yes |
| SRR21859822 | KAN | SRR21859822_2.fastq.gz | 8,279,598 | yes |

Total 32.2 MB (< 300 MB) → **no subsampling**; all reads were analysed.

## Command lines

```
# server (LOCAL_MODE: no Google credentials)
set DATA_DIR=<workspace>/testdata  VSEARCH_BIN=<path>/vsearch(.exe)  UNITE_SINTAX_DB=<path>/unite10_sintax.fasta.gz  LOCAL_MODE=1  PUBLIC_BASE_URL=http://127.0.0.1:8011
python -m uvicorn app.main:app --host 127.0.0.1 --port 8011

# client
python tests/test_amplicon_api.py --reads demo_reads --base http://127.0.0.1:8011 \
    --marker ITS1 --fwd CTTGGTCATTTAGAGGAAGTAA --rev GCTGCGTTCTTCATCGATGC
```
POST /api/amplicon fields: `marker=ITS1`, `fwd_primer=CTTGGTCATTTAGAGGAAGTAA`, `rev_primer=GCTGCGTTCTTCATCGATGC`, `pairing=paired`,
`location`, `lat`, `lon`, `submitter`; 4 files in `files[]`. Pipeline steps executed by the worker (job `e90e6df6dc7f`):

```
cutadapt -g FWD -a rc(REV) -G REV -A rc(FWD) -e 0.15 -O 10 --discard-untrimmed --minimum-length 50 -j 1 --json ... -o R1.trim.gz -p R2.trim.gz R1 R2
vsearch --fastq_mergepairs R1 --reverse R2 --fastq_minovlen 12 --fastq_maxdiffs 10 --fastq_allowmergestagger
vsearch --fastq_filter merged.fastq --fastq_maxee 1.0 --fastq_minlen 100 --fastq_maxns 0 --relabel <sample>.
vsearch --derep_fulllength pooled.fasta --sizeout --minuniquesize 2
vsearch --cluster_unoise derep.fasta --minsize 8 --unoise_alpha 2.0 --sizein --sizeout
vsearch --uchime3_denovo zotus_raw.fasta --nonchimeras asvs.fasta --relabel ITS1_ASV --sizein --xsize
vsearch --usearch_global pooled.fasta --db asvs.fasta --id 0.97 --strand plus --otutabout asv_table_raw.tsv
vsearch --sintax asvs.fasta --db unite10_sintax.fasta.gz --sintax_cutoff 0.8 --strand both --randseed 42
```

## Results (job e90e6df6dc7f)

Summary: **n_reads_in = 140,055** read pairs, **n_reads_kept = 126,975** (90.7 %),
**n_asvs = 127**, **n_fungal_asvs = 114**, **n_species = 37** (distinct species names among the 40 fungal ASVs that carry a species-level SINTAX call ≥ 0.8).

| sample | reads_in | primer-trimmed | merged | filtered (maxEE 1) | mapped to ASVs | n_asvs | fungal ASVs | Shannon (fungal) | % fungal reads |
|---|---|---|---|---|---|---|---|---|---|
| SRR21859822 | 59,580 | 57,339 | 54,653 | 54,621 | 53,604 | 91 | 90 | 1.2796 | 99.99 |
| SRR21859823 | 80,475 | 78,761 | 75,407 | 75,375 | 73,371 | 123 | 111 | 1.4494 | 98.27 |

Primer hit rate 96–98 %, merge rate 95–96 %, maxEE loss < 0.1 % — consistent with a correctly specified ITS1F/ITS2 library.

Fungal phylum composition (% of fungal reads): Ascomycota 97.894, Basidiomycota 1.254, Unresolved 0.766, Chytridiomycota 0.071, Rozellomycota 0.015.

Top genera (% of fungal reads): Talaromyces 67.99, Unresolved 14.58, Nigrospora 8.85, Plectosphaerella 1.3, Paecilomyces 1.02, Colletotrichum 0.94, Scytalidium 0.88, Candida 0.6, Aspergillus 0.6, Penicillium 0.46.

Assignment depth of the 127 ASVs: species 40, genus 29, family 14, unassigned 13, kingdom 11, order 10, phylum 5, class 5.
13 ASVs had no kingdom-level call at cutoff 0.8 (non-fungal or novel ITS1 sequences; UNITE eukaryote release used so these are not forced into Fungi).

Top ASVs:
```
      asv_id  reads_total             genus species  sintax_conf
0  ITS1_ASV1        85422       Talaromyces    None         1.00
1  ITS1_ASV3        12179              None    None         1.00
2  ITS1_ASV2        11127        Nigrospora    None         1.00
3  ITS1_ASV4         2830              None    None         1.00
4  ITS1_ASV5         1631  Plectosphaerella    None         0.84
```

Reproducibility: with `--randseed 42` two consecutive runs gave identical summaries (n_species 37 / 37). Without a seed the SINTAX bootstrap gave 37 vs 39 species on the same input — hence the pinned seed.

## Timings (job e90e6df6dc7f, 2 threads)

- cutadapt SRR21859822: 3.0 s
- merge SRR21859822: 0.3 s
- filter SRR21859822: 0.1 s
- cutadapt SRR21859823: 4.0 s
- merge SRR21859823: 0.4 s
- filter SRR21859823: 0.1 s
- derep: 0.2 s
- unoise3: 0.2 s
- uchime3: 0.1 s
- asv table: 20.8 s
- sintax: 2.6 s
- total wall time submit → done: 35 s (polling interval 5 s). The `usearch_global` read-mapping step dominates (20.8 s).

## Checks

| check | result |
|---|---|
| POST /api/amplicon returns job_id; GET /api/jobs/{id} reaches status `done` | pass |
| result.json has every key of the shared schema (top level, summary, samples, taxa, composition, files, notes); timestamps ISO 8601 UTC | pass |
| n_asvs > 0; taxonomy assigned (114/127 ASVs with kingdom; 40 at species level) | pass |
| 9 result files present & downloadable via /api/jobs/{id}/files/{name} (asv_table.tsv, asv_taxonomy.tsv, asvs.fasta, read_tracking.tsv, diversity_summary.tsv, composition_phylum.tsv, composition_genus.tsv, pipeline_log.txt, result.json) | pass |
| Ledger CSVs `jobs` (19 cols), `samples` (13 cols), `taxa` (12 cols) written with header order identical to platform_config.json; all rows have the exact width | pass |
| taxa ledger limited to top 200 ASVs per job (214 rows = 127 ASVs × samples with reads > 0) | pass |
| App starts with no Google credentials (LOCAL_MODE auto-true); barcode/pages routers missing → warning only | pass |
| SQLite queue: 2 jobs run sequentially by the single worker, both `done` | pass |
| `tests/test_amplicon_api.py` exit code 0, "PROBLEMS: none" (3 runs) | pass |

## Bug found and fixed during testing

The first run processed the data single-end: the router called `jobs.create_job()` (which wakes the worker) *before* the
uploaded files were written, so the worker saw only 3 of 4 files. Fixed with `create_job(..., queued=False)` + `jobs.enqueue(job_id)`
after the uploads are on disk. **The barcoding router must do the same** (or write files before calling `create_job`).

## Notes / deviations

- Loopback requests from the sandbox kernel had to bypass the sandbox HTTP proxy (`httpx.Client(trust_env=False)`); this is a test-harness detail, not a code change.
- Shannon in `samples[]` is computed on fungal ASVs (as in edna_fungal_pipeline.py); `shannon_all` (all ASVs) is included as an extra field.
- Extra fields beyond the schema (`length`, `assignment_level` in taxa; `reads_trimmed`, `n_fungal_asvs`, `shannon_all` in samples; `citations`, `result_folder_url` top-level) are additive.
- 18S markers are classified against UNITE unless `PR2_SINTAX_DB` is set (noted in result notes); no PR2 database was tested here.
- Drive/Sheets code path (service account) is implemented but could not be exercised in this sandbox (no credentials); LOCAL_MODE path fully tested.

Results are automated and provisional; no negative controls are enforced; verify taxonomic assignments before publication.
Citations: cutadapt (Martin 2011), VSEARCH (Rognes et al. 2016), UNITE (Abarenkov et al. 2024), SINTAX (Edgar 2016), Plotly (MIT).
