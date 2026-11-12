# Barcoding track – test report

Job `testbarcode01` run with `app.pipeline.barcoding.run_job` against the intact UNITE v10 SINTAX database (VSEARCH 2.32.0), stub LOCAL_MODE storage. Completed 2026-11-12T05:59:52Z.

## Source sequences (real UNITE records, read from the database; nothing withheld)

| Genus | UNITE SH | Species | Length (bp) |
|---|---|---|---|
| Aspergillus | SH1187575.10FU | *Aspergillus flavus* | 513 |
| Trichoderma | SH1413670.10FU | *Trichoderma neorufoides* | 506 |
| Penicillium | SH1308127.10FU | *Penicillium sanguifluum* | 516 |
| Fusarium | SH1050214.10FU | *Fusarium equiseti* | 539 |
| Rhodotorula | SH1231671.10FU | *Rhodotorula ngohengohe* | 524 |

## Query results

| Query | Design | Strand | Best hit (SH) | Identity % | Query cov % | SINTAX genus (conf) | SINTAX species (conf) | Chimera | Assignment level | Expected | Pass |
|---|---|---|---|---|---|---|---|---|---|---|---|
| exact_Aspergillus | exact copy | + | *Aspergillus flavus* (SH1187575.10FU) | 100.0 | 100.0 | Aspergillus (1.00) | Aspergillus flavus (1.00) | no | species | species | ✓ |
| exact_Fusarium | exact copy | + | *Fusarium equiseti* (SH1050214.10FU) | 100.0 | 100.0 | Fusarium (1.00) | Fusarium equiseti (1.00) | no | species | species | ✓ |
| mut1pct_Trichoderma | 1% random substitutions | + | *Trichoderma neorufoides* (SH1413670.10FU) | 99.0 | 100.0 | Trichoderma (1.00) | Trichoderma neorufoides (1.00) | no | species | genus (species uncertain) or species | ✓ |
| mut3pct_Rhodotorula | 3% random substitutions | + | *Rhodotorula ngohengohe* (SH1231671.10FU) | 97.1 | 100.0 | Rhodotorula (1.00) | Rhodotorula ngohengohe (1.00) | no | genus (species uncertain) | genus (species uncertain) | ✓ |
| mut8pct_Penicillium | 8% random substitutions | + | *Penicillium sanguifluum* (SH1308127.10FU) | 92.1 | 100.0 | Penicillium (0.98) | Penicillium sanguifluum (0.41) | no | family/order level only | family/order level only | ✓ |
| revcomp_Aspergillus | reverse complement of exact copy | - | *Aspergillus flavus* (SH1187575.10FU) | 100.0 | 100.0 | Aspergillus (1.00) | Aspergillus flavus (1.00) | no | species | species | ✓ |
| chimera_Asp_Fus | chimera: Aspergillus 5' half + Fusarium 3' half | + | *Fusarium incarnatum* (SH1050282.10FU) | 86.0 | 75.5 | Fusarium (0.39) | Fusarium equiseti (0.37) | YES | chimera suspected (not assigned) | chimera suspected (not assigned) | ✓ |

## Notes per query

- **revcomp_Aspergillus**: query was reverse-complemented (minus strand matched better)
- **chimera_Asp_Fus**: uchime_ref: chimera of SH1187575.10FU and SH1050214.10FU (score 48.96)

## Checks

- report.html contains the caveat footer and the cutadapt / VSEARCH / UNITE / SINTAX / Plotly citations: yes
- files uploaded via storage.upload_result_file: hits.tsv, queries_oriented.fasta, report.html
- ledger rows written: jobs (1 row), barcoding_hits (7 rows), column order = platform_config.json ledger_headers
- summary: {"n_samples": 7, "n_reads_in": 7, "n_reads_kept": 7, "n_asvs": 7, "n_fungal_asvs": 7, "n_species": 3, "marker": "ITS", "location": null, "lat": null, "lon": null}

**Result: ALL CHECKS PASSED**

## Caveats

- The .ab1 code path (Biopython SeqIO 'abi' + Mott-style Q20 trimming) is covered by unit tests on synthetic Phred arrays and a FASTQ record; no real Sanger .ab1 trace was available in this sandbox.
- Mutations are random substitutions only (no indels); identities reported are VSEARCH default identity (matching columns / alignment columns, terminal gaps excluded).
- A chimera is reported with `assignment_level = chimera suspected (not assigned)` even though hits are listed; a '?' uchime verdict is noted but not flagged.
