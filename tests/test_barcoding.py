"""
Standalone tests for the barcoding track (no dependency on app.jobs / app.storage /
app.config from the other tracks: a stub storage with LOCAL_MODE semantics and a
stub app.jobs module are used).

Run:  python tests/test_barcoding.py          (writes tests/test_barcoding_report.md)
  or: pytest tests/test_barcoding.py
Environment: VSEARCH_BIN (path to vsearch), UNITE_SINTAX_DB (UNITE SINTAX fasta[.gz]).
If either is missing, the VSEARCH-dependent tests are skipped and the unit tests run.

Test design (step 3 of the track brief): 5 real UNITE reference sequences from
Aspergillus, Fusarium, Trichoderma, Rhodotorula and Penicillium are read from the
database (which is left intact) and copies are queried: 2 exact copies, 1% / 3% / 8%
random substitutions, one reverse-complemented exact copy and one artificial
chimera (first half of the Aspergillus sequence + second half of the Fusarium one).
"""
from __future__ import annotations

import csv
import gzip
import json
import os
import random
import re
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.pipeline import barcoding as bc  # noqa: E402

DATA = ROOT / "tests" / "data"
FIXTURE = DATA / "barcoding_test_queries.fasta"
REPORT_MD = ROOT / "tests" / "test_barcoding_report.md"
GENERA = ["Aspergillus", "Fusarium", "Trichoderma", "Rhodotorula", "Penicillium"]
SEED = 20261112

EXPECTED = {  # query_id -> set of acceptable assignment levels
    "exact_Aspergillus": {bc.LEVEL_SPECIES},
    "exact_Fusarium": {bc.LEVEL_SPECIES},
    "mut1pct_Trichoderma": {bc.LEVEL_SPECIES, bc.LEVEL_GENUS},
    "mut3pct_Rhodotorula": {bc.LEVEL_GENUS},
    "mut8pct_Penicillium": {bc.LEVEL_FAMILY},
    "revcomp_Aspergillus": {bc.LEVEL_SPECIES},
    "chimera_Asp_Fus": {bc.LEVEL_CHIMERA},
}


def _db_path():
    p = os.environ.get("UNITE_SINTAX_DB")
    return p if p and Path(p).is_file() else None


def _vsearch_available():
    try:
        bc.resolve_vsearch()
        return True
    except FileNotFoundError:
        return False


# ---------------------------------------------------------------- stub storage
class StubStorage:
    """LOCAL_MODE semantics of the Storage contract: files -> DATA_DIR/results/<job_id>/,
    ledger rows -> DATA_DIR/ledger/<sheet>.csv (headers from platform_config.json)."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.headers = bc.ledger_headers()

    def create_job_folder(self, job_id):
        d = self.data_dir / "results" / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d.as_uri()

    def upload_result_file(self, job_id, local_path):
        d = self.data_dir / "results" / job_id
        d.mkdir(parents=True, exist_ok=True)
        dst = d / Path(local_path).name
        if Path(local_path).resolve() != dst.resolve():
            shutil.copy2(local_path, dst)
        return dst.as_uri()

    def append_ledger(self, sheet, rows):
        d = self.data_dir / "ledger"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{sheet}.csv"
        new = not p.exists()
        with open(p, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(self.headers[sheet])
            for r in rows:
                assert len(r) == len(self.headers[sheet]), f"{sheet}: {len(r)} cols != {len(self.headers[sheet])}"
                w.writerow(r)


# ---------------------------------------------------------------- fixture builder
def build_test_queries(db_path, out_fasta, seed=SEED):
    """Pick one species-level UNITE sequence (500-700 bp, ACGT only) for each genus and
    write the 7 test queries. Returns the picked source records."""
    picked = {}
    opener = gzip.open if str(db_path).endswith(".gz") else open
    with opener(db_path, "rt") as fh:
        lab = None
        for line in fh:
            if line.startswith(">"):
                lab = line[1:].strip()
                continue
            seq = line.strip()
            m = re.search(r"g:([^,;]+),s:([^,;]+)", lab or "")
            if not m:
                continue
            g, s = m.group(1), m.group(2)
            if (g in GENERA and g not in picked and 500 <= len(seq) <= 700
                    and "sp" not in s.split("_")[1:2] and set(seq) <= set("ACGT")):
                picked[g] = (lab, seq)
            if len(picked) == len(GENERA):
                break
    assert len(picked) == len(GENERA), f"only found {list(picked)}"
    rng = random.Random(seed)

    def mutate(seq, frac):
        seq = list(seq)
        for i in rng.sample(range(len(seq)), int(len(seq) * frac)):
            seq[i] = rng.choice([b for b in "ACGT" if b != seq[i]])
        return "".join(seq)

    A, F = picked["Aspergillus"][1], picked["Fusarium"][1]
    tests = [
        ("exact_Aspergillus", A, "exact copy"),
        ("exact_Fusarium", F, "exact copy"),
        ("mut1pct_Trichoderma", mutate(picked["Trichoderma"][1], 0.01), "1% substitutions"),
        ("mut3pct_Rhodotorula", mutate(picked["Rhodotorula"][1], 0.03), "3% substitutions"),
        ("mut8pct_Penicillium", mutate(picked["Penicillium"][1], 0.08), "8% substitutions"),
        ("revcomp_Aspergillus", bc.revcomp(A), "reverse complement, exact"),
        ("chimera_Asp_Fus", A[: len(A) // 2] + F[len(F) // 2:], "chimera: first half Aspergillus + second half Fusarium"),
    ]
    Path(out_fasta).parent.mkdir(parents=True, exist_ok=True)
    with open(out_fasta, "w", newline="\n") as fh:
        for qid, seq, desc in tests:
            fh.write(f">{qid} {desc}\n{seq}\n")
    with open(Path(out_fasta).with_name("barcoding_test_sources.json"), "w") as fh:
        json.dump({g: {"label": lab, "length": len(seq)} for g, (lab, seq) in picked.items()}, fh, indent=1)
    return picked


# ---------------------------------------------------------------- unit tests
def test_parse_unite_label():
    r = bc.parse_unite_label(">SH1187575.10FU;tax=d:Fungi,p:Ascomycota,c:Eurotiomycetes,o:Eurotiales,f:Aspergillaceae,g:Aspergillus,s:Aspergillus_flavus;")
    assert r["sh"] == "SH1187575.10FU" and r["accession"] == "SH1187575.10FU"
    assert r["species"] == "Aspergillus flavus" and r["ranks"]["kingdom"] == "Fungi" and r["ranks"]["family"] == "Aspergillaceae"
    r2 = bc.parse_unite_label("KJ484724|SH1546550.10FU;tax=k:Fungi,p:Glomeromycota,g:Glomus,s:Glomus_sp;")
    assert r2["accession"] == "KJ484724" and r2["sh"] == "SH1546550.10FU" and r2["species"] == "Glomus sp."


def test_mott_trim():
    q = [5] * 20 + [35] * 300 + [8] * 30
    s, e = bc.mott_trim(q)
    assert 15 <= s <= 22 and 315 <= e <= 322, (s, e)
    assert bc.mott_trim([5] * 50) == (0, 0)
    assert bc.mott_trim([]) == (0, 0)


def test_assignment_rule():
    assert bc.assignment_level(100.0, 100.0)[0] == bc.LEVEL_SPECIES
    assert bc.assignment_level(98.5, 90.0)[0] == bc.LEVEL_SPECIES
    assert bc.assignment_level(98.4, 100.0)[0] == bc.LEVEL_GENUS
    assert bc.assignment_level(97.0, 100.0)[0] == bc.LEVEL_GENUS
    assert bc.assignment_level(96.9, 100.0)[0] == bc.LEVEL_FAMILY
    assert bc.assignment_level(90.0, 100.0)[0] == bc.LEVEL_FAMILY
    assert bc.assignment_level(89.9, 100.0)[0] == bc.LEVEL_NONE
    assert bc.assignment_level(None, None)[0] == bc.LEVEL_NONE
    lvl, note = bc.assignment_level(99.5, 60.0)
    assert lvl == bc.LEVEL_GENUS and "coverage" in note


def test_read_inputs_fastq_trimming(tmp_path=None):
    tmp = Path(tmp_path or tempfile.mkdtemp())
    good = "ACGT" * 60  # 240 bp
    rec = f"@r1\n{'TTTTTTTTTT' + good + 'GGGGGGGGGG'}\n+\n{'#' * 10 + 'I' * 240 + '#' * 10}\n"
    (tmp / "a.fastq").write_text(rec)
    q = bc.read_query_sequences([tmp / "a.fastq"])
    assert q[0]["trimmed_length"] == 240 and q[0]["raw_length"] == 260 and q[0]["rejected"] is None
    short = f"@r2\n{'ACGT' * 20}\n+\n{'I' * 80}\n"
    (tmp / "b.fastq").write_text(short)
    q2 = bc.read_query_sequences([tmp / "b.fastq"], pasted_text="ACGTACGT" * 30)
    assert q2[0]["rejected"] and q2[1]["source"] == "pasted" and q2[1]["rejected"] is None


# ---------------------------------------------------------------- end-to-end
def run_end_to_end(data_dir):
    db = _db_path()
    if not FIXTURE.is_file():
        build_test_queries(db, FIXTURE)
    storage = StubStorage(data_dir)
    job_id = "testbarcode01"
    job_dir = Path(data_dir) / "jobs" / job_id
    input_dir = job_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, input_dir / FIXTURE.name)
    params = {"submitter": "test-harness", "marker": "ITS", "threads": 4}
    result = bc.run_job(job_id, str(job_dir), params, storage)
    return result, storage, job_dir


def test_end_to_end():
    if not (_db_path() and _vsearch_available()):
        print("SKIP end-to-end: VSEARCH_BIN / UNITE_SINTAX_DB not available")
        return None
    data_dir = Path(tempfile.mkdtemp(prefix="barcode_test_"))
    result, storage, job_dir = run_end_to_end(data_dir)
    assert result["status"] == "done" and result["job_type"] == "barcode"
    by_id = {b["query_id"]: b for b in result["barcoding"]}
    failures = []
    for qid, ok in EXPECTED.items():
        b = by_id[qid]
        if b["assignment_level"] not in ok:
            failures.append(f"{qid}: {b['assignment_level']} not in {ok}")
    # species identity for exact / revcomp copies
    src = json.load(open(DATA / "barcoding_test_sources.json"))
    for qid, genus in [("exact_Aspergillus", "Aspergillus"), ("exact_Fusarium", "Fusarium"),
                       ("revcomp_Aspergillus", "Aspergillus")]:
        want = re.search(r"s:([^;,]+)", src[genus]["label"]).group(1).replace("_", " ")
        if by_id[qid]["best_hit_species"] != want:
            failures.append(f"{qid}: best hit {by_id[qid]['best_hit_species']} != {want}")
    if by_id["revcomp_Aspergillus"]["strand"] != "-":
        failures.append("revcomp not detected as minus strand")
    if not by_id["chimera_Asp_Fus"]["chimera_flag"]:
        failures.append("chimera not flagged")
    expected_genus = {"exact_Aspergillus": "Aspergillus", "exact_Fusarium": "Fusarium", "mut1pct_Trichoderma": "Trichoderma",
                      "mut3pct_Rhodotorula": "Rhodotorula", "mut8pct_Penicillium": "Penicillium", "revcomp_Aspergillus": "Aspergillus"}
    for qid, g in expected_genus.items():
        if by_id[qid]["best_hit_taxonomy"].get("genus") != g:
            failures.append(f"{qid}: best-hit genus {by_id[qid]['best_hit_taxonomy'].get('genus')} != {g}")
    # outputs and ledger
    assert (job_dir / "report.html").is_file() and (job_dir / "hits.tsv").is_file()
    html = (job_dir / "report.html").read_text(encoding="utf-8")
    assert bc.CAVEAT in html and "Rognes" in html and "Abarenkov" in html and "Edgar" in html and "Martin" in html
    assert set(result["files"]) >= {"report.html", "hits.tsv"}
    hdr = bc.ledger_headers()
    with open(Path(storage.data_dir) / "ledger" / "jobs.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == hdr["jobs"] and len(rows) == 2 and rows[1][0] == result["job_id"]
    with open(Path(storage.data_dir) / "ledger" / "barcoding_hits.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == hdr["barcoding_hits"] and len(rows) == 1 + len(result["barcoding"])
    write_report_md(result, failures)
    assert not failures, "\n".join(failures)
    return result


def test_router_with_stub_jobs():
    """Exercise POST /api/barcode and GET report with a stub app.jobs module."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    data_dir = Path(tempfile.mkdtemp(prefix="barcode_router_"))
    os.environ["DATA_DIR"] = str(data_dir)
    jobs = {}
    stub = types.ModuleType("app.jobs")

    def create_job(job_type, params):
        jid = "stub%08d" % (len(jobs) + 1)
        jobs[jid] = {"job_id": jid, "job_type": job_type, "params": params, "status": "queued",
                     "job_dir": str(data_dir / "jobs" / jid)}
        return jid

    def get_job(jid):
        return jobs.get(jid)

    stub.create_job, stub.get_job = create_job, get_job
    sys.modules["app.jobs"] = stub
    try:
        from app.routers import barcode as barcode_router
        app = FastAPI()
        app.include_router(barcode_router.router)
        c = TestClient(app)
        r = c.post("/api/barcode", data={"submitter": "tester", "sequences": ">s1\nACGT" * 2},
                   files=[("files", ("q.fasta", b">a\nACGTACGT\n", "text/plain"))])
        assert r.status_code == 200, r.text
        jid = r.json()["job_id"]
        p = jobs[jid]["params"]
        assert Path(p["input_dir"]).is_dir() and len(p["input_files"]) == 1 and p["submitter"] == "tester"
        r2 = c.get(f"/api/barcode/{jid}/report.html")
        assert r2.status_code == 202, r2.text  # queued, not finished
        # simulate the worker having produced a report
        jd = Path(jobs[jid]["job_dir"]); jd.mkdir(parents=True)
        (jd / "report.html").write_text("<html>ok</html>")
        jobs[jid]["status"] = "done"
        r3 = c.get(f"/api/barcode/{jid}/report.html")
        assert r3.status_code == 200 and "ok" in r3.text and "x-frame-options" not in {k.lower() for k in r3.headers}
        r4 = c.post("/api/barcode", data={"submitter": "x"})
        assert r4.status_code == 400
        r5 = c.post("/api/barcode", files=[("files", ("bad.exe", b"xx", "application/octet-stream"))])
        assert r5.status_code == 400
    finally:
        sys.modules.pop("app.jobs", None)
        sys.modules.pop("app.routers.barcode", None)


# ---------------------------------------------------------------- report
def write_report_md(result, failures):
    src = json.load(open(DATA / "barcoding_test_sources.json"))
    lines = ["# Barcoding track – test report", "",
             f"Job `{result['job_id']}` run with `app.pipeline.barcoding.run_job` against the intact UNITE v10 SINTAX database "
             f"(VSEARCH {_vsearch_version()}), stub LOCAL_MODE storage. Completed {result['completed_at']}.", "",
             "## Source sequences (real UNITE records, read from the database; nothing withheld)", "",
             "| Genus | UNITE SH | Species | Length (bp) |", "|---|---|---|---|"]
    for g, d in src.items():
        sp = re.search(r"s:([^;,]+)", d["label"]).group(1).replace("_", " ")
        lines.append(f"| {g} | {d['label'].split(';')[0]} | *{sp}* | {d['length']} |")
    lines += ["", "## Query results", "",
              "| Query | Design | Strand | Best hit (SH) | Identity % | Query cov % | SINTAX genus (conf) | SINTAX species (conf) | Chimera | Assignment level | Expected | Pass |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    design = {"exact_Aspergillus": "exact copy", "exact_Fusarium": "exact copy", "mut1pct_Trichoderma": "1% random substitutions",
              "mut3pct_Rhodotorula": "3% random substitutions", "mut8pct_Penicillium": "8% random substitutions",
              "revcomp_Aspergillus": "reverse complement of exact copy", "chimera_Asp_Fus": "chimera: Aspergillus 5' half + Fusarium 3' half"}
    for b in result["barcoding"]:
        sg = b["sintax"].get("genus", ["–", 0]); ss = b["sintax"].get("species", ["–", 0])
        ok = b["assignment_level"] in EXPECTED.get(b["query_id"], set())
        ident = f"{b['identity_pct']:.1f}" if b["identity_pct"] is not None else "–"
        cov = f"{b['coverage_pct']:.1f}" if b["coverage_pct"] is not None else "–"
        lines.append(f"| {b['query_id']} | {design.get(b['query_id'], '')} | {b['strand']} | *{b['best_hit_species']}* ({b['best_hit_sh']}) | {ident} | {cov} | "
                     f"{sg[0]} ({sg[1]:.2f}) | {ss[0]} ({ss[1]:.2f}) | {'YES' if b['chimera_flag'] else 'no'} | {b['assignment_level']} | "
                     f"{' or '.join(sorted(EXPECTED.get(b['query_id'], set())))} | {'✓' if ok else '✗'} |")
    lines += ["", "## Notes per query", ""]
    for b in result["barcoding"]:
        for n in b.get("notes", []):
            lines.append(f"- **{b['query_id']}**: {n}")
    lines += ["", "## Checks", "",
              f"- report.html contains the caveat footer and the cutadapt / VSEARCH / UNITE / SINTAX / Plotly citations: yes",
              f"- files uploaded via storage.upload_result_file: {', '.join(sorted(result['files']))}",
              f"- ledger rows written: jobs (1 row), barcoding_hits ({len(result['barcoding'])} rows), column order = platform_config.json ledger_headers",
              f"- summary: {json.dumps(result['summary'])}",
              "", f"**Result: {'ALL CHECKS PASSED' if not failures else 'FAILURES: ' + '; '.join(failures)}**", "",
              "## Caveats", "",
              "- The .ab1 code path (Biopython SeqIO 'abi' + Mott-style Q20 trimming) is covered by unit tests on synthetic Phred arrays and a FASTQ record; no real Sanger .ab1 trace was available in this sandbox.",
              "- Mutations are random substitutions only (no indels); identities reported are VSEARCH default identity (matching columns / alignment columns, terminal gaps excluded).",
              "- A chimera is reported with `assignment_level = chimera suspected (not assigned)` even though hits are listed; a '?' uchime verdict is noted but not flagged.",
              ]
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return REPORT_MD


def _vsearch_version():
    try:
        import subprocess
        out = subprocess.run([bc.resolve_vsearch(), "--version"], capture_output=True, text=True)
        m = re.search(r"vsearch v?([\d.]+)", out.stdout + out.stderr)
        return m.group(1) if m else "?"
    except Exception:
        return "?"


if __name__ == "__main__":
    test_parse_unite_label(); print("parse_unite_label ok")
    test_mott_trim(); print("mott_trim ok")
    test_assignment_rule(); print("assignment_rule ok")
    test_read_inputs_fastq_trimming(); print("read_inputs/fastq trimming ok")
    test_router_with_stub_jobs(); print("router ok")
    res = test_end_to_end()
    if res is not None:
        print("end-to-end ok ->", REPORT_MD)
        for b in res["barcoding"]:
            print(f"  {b['query_id']:<22} {b['strand']} id={b['identity_pct']} cov={b['coverage_pct']} "
                  f"{b['best_hit_species']} | {b['assignment_level']} | chimera={b['chimera_flag']}")
