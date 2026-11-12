# Integration test report - assembled platform (12 Nov 2026)

Environment: Windows sandbox, Python 3.11, VSEARCH 2.32.0 (win-x86_64), cutadapt (pip), UNITE v10 SINTAX
database (unite10_sintax.fasta.gz, 21.1 MB). App run in LOCAL_MODE (no Google credentials) through FastAPI's
in-process TestClient, because loopback TCP sockets are blocked in this sandbox. Docker build not possible
here; the Dockerfile's external inputs were checked individually (VSEARCH asset
`vsearch-2.32.0-linux-x86_64.tar.gz` present in the GitHub v2.32.0 release; UNITE tgz 50,350,085 bytes
reachable at the PlutoF S3 URL; all .py files byte-compile).

## Pages
| Route | HTTP | X-Frame-Options | caveat footer |
|---|---|---|---|
| / | 200 | absent | yes |
| /submit | 200 | absent | yes |
| /barcode | 200 | absent | yes |
| /dashboard | 200 | absent | yes |
| /jobs/doesnotexist | 200 (renders 'unknown job' state) | absent | yes |

## Barcoding job (tests/data/barcoding_test_queries.fasta, 7 queries from 5 real UNITE records) - 6 s
| query | best hit | identity % | assignment | chimera |
|---|---|---|---|---|
| exact_Aspergillus | Aspergillus flavus | 100.0 | species | no |
| exact_Fusarium | Fusarium equiseti | 100.0 | species | no |
| mut1pct_Trichoderma | Trichoderma neorufoides | 99.0 | species | no |
| mut3pct_Rhodotorula | Rhodotorula ngohengohe | 97.1 | genus (species uncertain) | no |
| mut8pct_Penicillium | Penicillium sanguifluum | 92.1 | family/order level only | no |
| revcomp_Aspergillus | Aspergillus flavus | 100.0 | species | no |
| chimera_Asp_Fus | Fusarium incarnatum | 86.0 | chimera suspected (not assigned) | YES |
Ledger: jobs 1 row, barcoding_hits 7 rows, header order identical to platform_config.json.

## Amplicon job (ENA PRJNA889370, 2 ITS1 libraries, Kerala; 4 FASTQ.gz verified by byte count + MD5) - 30 s
| sample | reads in | merged | filtered | non-chimeric | ASVs | Shannon | % fungal |
|---|---|---|---|---|---|---|---|
| SRR21859822 (KAN) | 59,580 | 54,653 | 54,621 | 53,604 | 91 | 1.28 | 99.99 |
| SRR21859823 (EKM) | 80,475 | 75,407 | 75,375 | 73,371 | 123 | 1.45 | 98.27 |
Summary: 140,055 pairs in, 126,975 kept (90.7 %), 127 ASVs (114 fungal), 37 species-level calls.
Top genera (% reads): Talaromyces 68.0, unresolved 14.6, Nigrospora 8.9, Plectosphaerella 1.3, Paecilomyces 1.0, Colletotrichum 0.9.
Files produced: asv_table.tsv, asv_taxonomy.tsv, asvs.fasta, read_tracking.tsv, diversity_summary.tsv,
composition_phylum.tsv, composition_genus.tsv, pipeline_log.txt, result.json. Ledger: jobs +1, samples 2, taxa 214 rows (<=200 ASVs); headers match.
These numbers are identical to the amplicon track's own run (tests/test_amplicon_report.md).

## scripts/smoke_test.py
Executed in-process: 9 of 11 checks pass; the two failures are the expected LOCAL_MODE outcomes
("Google credentials configured" and "files published to Drive"), which turn green once the OAuth token
secret is mounted on Cloud Run.

## Not tested here (carried-over deviations)
* Drive/Sheets write path with real credentials (no Google credential in the sandbox). Both credential
  types (OAuth authorized_user, service account) are constructed correctly; API calls are untested.
* Docker image build and hypercorn HTTP/2 serving on Cloud Run.
* Dashboard JavaScript in a browser (no browser available); aggregation logic verified by a Python mirror.
* Real Sanger .ab1 traces (synthetic Phred arrays only).
* Vendored plotly.js is v4.1.1 (from the PyPI plotly package), CDN fallback 2.35.2.
