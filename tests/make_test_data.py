"""Build a realistic test ledger + result.json files for the dashboard track.

Sources (tests/data/source/): the project's earlier Indian coastal eDNA results
  ITS2_asv_table_with_taxonomy.tsv  Bhitarkanika mangrove, Odisha (~20.7N 86.9E), 4 samples
  ITS1_asv_table_with_taxonomy.tsv  Kerala coast (~10.0N 76.3E), 2 samples
  read_tracking.tsv, diversity_summary.tsv
Outputs:
  tests/data/results/<job_id>/result.json   (schema from the repository contract)
  tests/data/ledger/{jobs,samples,taxa,barcoding_hits}.csv  (exact ledger_headers order)
Plus 3 clearly synthetic barcoding rows under job_id 'demo-barcode'.
Run:  python tests/make_test_data.py
"""
from __future__ import annotations
import csv, json, math, uuid
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tests" / "data" / "source"
OUT_RES = ROOT / "tests" / "data" / "results"
OUT_LED = ROOT / "tests" / "data" / "ledger"
CFG = json.loads((ROOT / "platform_config.json").read_text(encoding="utf-8"))
HEADERS = CFG["ledger_headers"]
RESULTS_FOLDER_URL = CFG["drive_links"]["results"]

RANKS = ["kingdom", "phylum", "class", "order", "family", "genus", "species"]

JOBS = [
    dict(key="ITS2", table="ITS2_asv_table_with_taxonomy.tsv", marker="ITS2",
         fwd_primer="GCATCGATGAAGAACGCAGC", rev_primer="TCCTCCGCTTATTGATATGC",  # ITS3/ITS4
         location="Bhitarkanika mangrove, Odisha (east coast)", lat=20.7, lon=86.9,
         submitted_at="2026-11-10T06:12:00Z", completed_at="2026-11-10T06:41:30Z",
         submitter="demo-user", substrate="rhizosphere soil"),
    dict(key="ITS1", table="ITS1_asv_table_with_taxonomy.tsv", marker="ITS1",
         fwd_primer="CTTGGTCATTTAGAGGAAGTAA", rev_primer="GCTGCGTTCTTCATCGATGC",  # ITS1F/ITS2
         location="Kerala coast (west coast)", lat=10.0, lon=76.3,
         submitted_at="2026-11-11T09:30:00Z", completed_at="2026-11-11T09:44:10Z",
         submitter="demo-user", substrate="sediment"),
]


def job_id_for(key: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"fungal-dna-platform/test/{key}").hex[:12]


def clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return v


def build_amplicon(job: dict, rt: pd.DataFrame, div: pd.DataFrame):
    asv = pd.read_csv(SRC / job["table"], sep="\t")
    sample_ids = [c for c in asv.columns if c not in RANKS + ["ASV", "sequence", "length", "total_reads"]]
    jid = job_id_for(job["key"])
    asv = asv.sort_values("total_reads", ascending=False).reset_index(drop=True)
    fungal = asv["kingdom"] == "Fungi"
    total_reads = int(asv["total_reads"].sum())

    samples = []
    for s in sample_ids:
        r = rt[rt.sample_id == s].iloc[0]
        d = div[div.sample_id == s].iloc[0]
        col = asv[s]
        samples.append(dict(
            sample_id=s,
            reads_in=int(d["raw_read_pairs"]),
            reads_merged=int(r["merged"]),
            reads_filtered=int(r["filtered"]),
            reads_nonchimeric=int(col.sum()),          # reads mapped to non-chimeric ASVs
            n_asvs=int((col > 0).sum()),
            shannon=float(d["shannon"]),
            pct_fungal=float(d["pct_reads_fungal"]),
        ))

    def pct_table(rank: str, top: int | None):
        g = asv.copy()
        g[rank] = g[rank].where(fungal, "Non-fungal").fillna("Unassigned")
        ser = g.groupby(rank)["total_reads"].sum().sort_values(ascending=False)
        if top and len(ser) > top:
            keep = ser.iloc[:top]
            keep.loc["Other"] = ser.iloc[top:].sum()
            ser = keep
        return {k: round(100.0 * v / total_reads, 3) for k, v in ser.items()}

    taxa = []
    for _, row in asv.head(500).iterrows():
        rec = dict(asv_id=row["ASV"], reads_total=int(row["total_reads"]))
        for rk in RANKS:
            rec[rk] = clean(row[rk])
        rec["sintax_conf"] = None   # source table carries no SINTAX confidence column
        rec["per_sample"] = {s: int(row[s]) for s in sample_ids if int(row[s]) > 0}
        taxa.append(rec)

    n_species = int(asv.loc[fungal, "species"].dropna().nunique())
    summary = dict(
        n_samples=len(sample_ids),
        n_reads_in=int(sum(x["reads_in"] for x in samples)),
        n_reads_kept=total_reads,
        n_asvs=int(len(asv)),
        n_fungal_asvs=int(fungal.sum()),
        n_species=n_species,
        marker=job["marker"], location=job["location"], lat=job["lat"], lon=job["lon"],
    )
    files = {
        "result.json": f"/api/jobs/{jid}/files/result.json",
        "asv_table_with_taxonomy.tsv": f"/api/jobs/{jid}/files/asv_table_with_taxonomy.tsv",
        "asvs.fasta": f"/api/jobs/{jid}/files/asvs.fasta",
        "read_tracking.tsv": f"/api/jobs/{jid}/files/read_tracking.tsv",
    }
    result = dict(
        job_id=jid, job_type="amplicon", status="done",
        submitted_at=job["submitted_at"], completed_at=job["completed_at"],
        params=dict(marker=job["marker"], fwd_primer=job["fwd_primer"], rev_primer=job["rev_primer"],
                    location=job["location"], lat=job["lat"], lon=job["lon"],
                    submitter=job["submitter"], pairing="paired"),
        summary=summary, samples=samples, taxa=taxa,
        composition=dict(phylum=pct_table("phylum", None), genus=pct_table("genus", 15)),
        files=files,
        notes=["Test data rebuilt from the project's earlier Indian coastal eDNA results (tests/make_test_data.py).",
               "sintax_conf is null: the source ASV table has no SINTAX confidence column."],
    )

    # ledger rows
    jobs_row = dict(job_id=jid, job_type="amplicon", submitted_at=job["submitted_at"],
                    completed_at=job["completed_at"], status="done", submitter=job["submitter"],
                    marker=job["marker"], n_samples=summary["n_samples"], n_reads_in=summary["n_reads_in"],
                    n_reads_kept=summary["n_reads_kept"], n_asvs=summary["n_asvs"],
                    n_fungal_asvs=summary["n_fungal_asvs"], n_species=summary["n_species"],
                    location=job["location"], lat=job["lat"], lon=job["lon"],
                    result_folder_url=RESULTS_FOLDER_URL, result_json_url=files["result.json"],
                    notes="test ledger; rebuilt from earlier project results")
    sample_rows = [dict(job_id=jid, **{k: v for k, v in s.items()}, location=job["location"],
                        lat=job["lat"], lon=job["lon"], substrate=job["substrate"]) for s in samples]
    taxa_rows = []
    for t in taxa[:200]:                      # ledger cap: top 200 ASVs per job
        for s, reads in t["per_sample"].items():
            taxa_rows.append(dict(job_id=jid, sample_id=s, asv_id=t["asv_id"], reads=reads,
                                  **{rk: t[rk] for rk in RANKS}, sintax_conf=""))
    return result, jobs_row, sample_rows, taxa_rows


def build_barcode_demo():
    """Three clearly synthetic barcoding rows (job_id 'demo-barcode') so the panel renders."""
    jid = "demo-barcode"
    submitted = "2026-11-12T04:05:00Z"
    queries = [
        dict(query_id="SYNTHETIC_q1_Aspergillus", query_length=562, best_hit_accession="MH865380",
             best_hit_species="Aspergillus_niger", best_hit_sh="SH0987654.10FU", identity_pct=99.6,
             coverage_pct=100.0, sintax_genus="Aspergillus", sintax_species="Aspergillus_niger",
             sintax_conf=0.98, assignment_level="species", chimera_flag="N"),
        dict(query_id="SYNTHETIC_q2_Penicillium", query_length=548, best_hit_accession="MN431384",
             best_hit_species="Penicillium_citrinum", best_hit_sh="SH1234567.10FU", identity_pct=98.9,
             coverage_pct=99.1, sintax_genus="Penicillium", sintax_species="Penicillium_citrinum",
             sintax_conf=0.91, assignment_level="species", chimera_flag="N"),
        dict(query_id="SYNTHETIC_q3_Talaromyces", query_length=531, best_hit_accession="KX961234",
             best_hit_species="Talaromyces_sp", best_hit_sh="SH2345678.10FU", identity_pct=95.2,
             coverage_pct=97.4, sintax_genus="Talaromyces", sintax_species="", sintax_conf=0.74,
             assignment_level="genus", chimera_flag="N"),
    ]
    report_url = f"/api/barcode/{jid}/report.html"
    hit_rows = [dict(job_id=jid, submitted_at=submitted, report_url=report_url, **q) for q in queries]
    barcoding = []
    for q in queries:
        barcoding.append(dict(
            query_id=q["query_id"], query_length=q["query_length"],
            best_hit_accession=q["best_hit_accession"], best_hit_species=q["best_hit_species"],
            best_hit_sh=q["best_hit_sh"], identity_pct=q["identity_pct"], coverage_pct=q["coverage_pct"],
            sintax={"genus": [q["sintax_genus"], q["sintax_conf"]],
                    "species": [q["sintax_species"] or None, q["sintax_conf"] if q["sintax_species"] else None]},
            assignment_level=q["assignment_level"], chimera_flag=q["chimera_flag"],
            hits=[dict(accession=q["best_hit_accession"], species=q["best_hit_species"], sh=q["best_hit_sh"],
                       identity_pct=q["identity_pct"], coverage_pct=q["coverage_pct"]),
                  dict(accession="SYN000002", species=q["best_hit_species"], sh=q["best_hit_sh"],
                       identity_pct=round(q["identity_pct"] - 0.7, 1), coverage_pct=q["coverage_pct"])],
        ))
    result = dict(job_id=jid, job_type="barcode", status="done", submitted_at=submitted,
                  completed_at="2026-11-12T04:06:20Z", params=dict(submitter="demo-user", n_queries=3),
                  summary=dict(n_samples=3, n_reads_in=3, n_reads_kept=3, n_asvs=None, n_fungal_asvs=None,
                               n_species=2, marker="ITS", location="", lat=None, lon=None),
                  samples=[], barcoding=barcoding,
                  files={"result.json": f"/api/jobs/{jid}/files/result.json", "report.html": report_url},
                  notes=["SYNTHETIC demo barcoding job: query sequences and hits are invented for layout testing only."])
    jobs_row = dict(job_id=jid, job_type="barcode", submitted_at=submitted, completed_at=result["completed_at"],
                    status="done", submitter="demo-user", marker="ITS", n_samples=3, n_reads_in=3, n_reads_kept=3,
                    n_asvs="", n_fungal_asvs="", n_species=2, location="", lat="", lon="",
                    result_folder_url=RESULTS_FOLDER_URL, result_json_url=f"/api/jobs/{jid}/files/result.json",
                    notes="SYNTHETIC demo barcoding job")
    return result, jobs_row, hit_rows


def write_csv(path: Path, header: list[str], rows: list[dict]):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow(["" if r.get(h) is None else r.get(h) for h in header])


def main():
    rt = pd.read_csv(SRC / "read_tracking.tsv", sep="\t")
    div = pd.read_csv(SRC / "diversity_summary.tsv", sep="\t")
    OUT_LED.mkdir(parents=True, exist_ok=True)
    jobs_rows, sample_rows, taxa_rows = [], [], []
    for job in JOBS:
        result, jr, sr, tr = build_amplicon(job, rt, div)
        d = OUT_RES / result["job_id"]; d.mkdir(parents=True, exist_ok=True)
        (d / "result.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
        jobs_rows.append(jr); sample_rows += sr; taxa_rows += tr
        print(result["job_id"], job["marker"], json.dumps(result["summary"]))
    bres, bjr, hits = build_barcode_demo()
    d = OUT_RES / bres["job_id"]; d.mkdir(parents=True, exist_ok=True)
    (d / "result.json").write_text(json.dumps(bres, indent=1), encoding="utf-8")
    jobs_rows.append(bjr)
    write_csv(OUT_LED / "jobs.csv", HEADERS["jobs"], jobs_rows)
    write_csv(OUT_LED / "samples.csv", HEADERS["samples"], sample_rows)
    write_csv(OUT_LED / "taxa.csv", HEADERS["taxa"], taxa_rows)
    write_csv(OUT_LED / "barcoding_hits.csv", HEADERS["barcoding_hits"], hits)
    # empty ledger (headers only) for the empty-state test
    empty = OUT_LED / "empty"; empty.mkdir(exist_ok=True)
    for sheet, hdr in HEADERS.items():
        write_csv(empty / f"{sheet}.csv", hdr, [])
    print("rows: jobs", len(jobs_rows), "samples", len(sample_rows), "taxa", len(taxa_rows), "hits", len(hits))


if __name__ == "__main__":
    main()
