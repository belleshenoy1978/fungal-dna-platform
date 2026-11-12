"""
End-to-end test of scripts/actions_runner.py in LOCAL mode (no Google account,
no GitHub): queue in a CSV, file_ids as local paths, Storage in LOCAL_MODE.

    python tests/test_actions_runner.py --vsearch <path-to-vsearch> --db <unite_sintax.fasta.gz> \
        [--ena-dir <dir with PRJNA889370 FASTQ.gz>] [--work <dir>] [--skip-amplicon]

What it does
  1. static checks: .github/workflows/run-job.yml parses (PyYAML) and has the
     required triggers / concurrency / steps; apps_script/Code.gs has balanced
     braces/brackets/parentheses and defines setup() and onFormSubmit().
  2. barcoding job: tests/data/barcoding_test_queries.fasta as a Form upload plus
     two pasted bare sequences -> queue row must reach `done`, docs/jobs/<id>.json
     must exist and parse, ledger CSVs jobs + barcoding_hits must contain the job.
  3. amplicon job: ENA PRJNA889370 (4 FASTQ.gz, downloaded if missing and verified
     against ENA byte counts + MD5), ITS1, primers CTTGGTCATTTAGAGGAAGTAA /
     GCTGCGTTCTTCATCGATGC -> `done`, docs/jobs/<id>.json, summary numbers compared
     with the reference run (140,055 pairs in, 126,975 kept, 127 ASVs, 114 fungal,
     37 species).
  4. a job with a missing input file -> `failed` with an error text, while the
     other jobs in the same run still complete (failure isolation).
Writes tests/test_actions_runner_report.md with the real numbers and timings.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNNER = REPO / "scripts" / "actions_runner.py"
WORKFLOW = REPO / ".github" / "workflows" / "run-job.yml"
CODE_GS = REPO / "apps_script" / "Code.gs"
QUEUE_HEADERS = json.load(open(REPO / "platform_config.json", encoding="utf-8"))["ledger_headers"]["queue"]
ENA_REPORT = ("https://www.ebi.ac.uk/ena/portal/api/filereport?accession=PRJNA889370&result=read_run"
              "&fields=run_accession,fastq_ftp,fastq_bytes,fastq_md5")
EXPECTED_AMPLICON = {"n_reads_in": 140055, "n_reads_kept": 126975, "n_asvs": 127, "n_fungal_asvs": 114, "n_species": 37}
FWD, REV = "CTTGGTCATTTAGAGGAAGTAA", "GCTGCGTTCTTCATCGATGC"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failures: list[str] = []
        self.checks = 0

    def check(self, ok: bool, what: str) -> bool:
        self.checks += 1
        self.lines.append(f"- {'PASS' if ok else 'FAIL'}: {what}")
        if not ok:
            self.failures.append(what)
        return ok

    def note(self, s: str) -> None:
        self.lines.append(s)


# ------------------------------------------------------------------ static checks
def check_workflow(rep: Report) -> None:
    import yaml

    rep.note("\n## 1. Static checks\n")
    try:
        wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        rep.check(False, f"run-job.yml parses as YAML ({exc})")
        return
    rep.check(isinstance(wf, dict), "run-job.yml parses as YAML")
    on = wf.get("on") or wf.get(True)  # PyYAML reads a bare `on:` key as boolean True
    rep.check(isinstance(on, dict) and "repository_dispatch" in on and on["repository_dispatch"].get("types") == ["run-job"],
              "trigger repository_dispatch types [run-job]")
    rep.check(isinstance(on, dict) and "workflow_dispatch" in on and "job_id" in (on["workflow_dispatch"] or {}).get("inputs", {}),
              "trigger workflow_dispatch with job_id input")
    cron = (isinstance(on, dict) and on.get("schedule") or [{}])[0].get("cron")
    rep.check(cron == "*/30 * * * *", f"schedule cron every 30 min ({cron})")
    conc = wf.get("concurrency", {})
    rep.check(conc.get("group") == "fungal-jobs" and conc.get("cancel-in-progress") is False,
              "concurrency group fungal-jobs, cancel-in-progress false")
    rep.check(wf.get("permissions", {}).get("contents") == "write", "permissions contents: write")
    job = wf["jobs"]["run"]
    rep.check(job.get("timeout-minutes") == 350, "timeout-minutes 350")
    steps = job["steps"]
    uses = [s.get("uses", "") for s in steps]
    runs = "\n".join(s.get("run", "") for s in steps)
    rep.check(any(u.startswith("actions/checkout@") for u in uses), "checkout step")
    rep.check(any(u.startswith("actions/setup-python@") for u in uses)
              and any(str((s.get("with") or {}).get("python-version")) == "3.12" for s in steps), "setup-python 3.12")
    rep.check("pip install -r requirements.txt" in runs, "pip install -r requirements.txt")
    rep.check("vsearch-${VSEARCH_VERSION}-linux-x86_64.tar.gz" in runs and wf["env"]["VSEARCH_VERSION"] == "2.32.0"
              and "vsearch --version" in runs, "VSEARCH 2.32.0 linux-x86_64 download + --version check")
    rep.check(any(u.startswith("actions/cache@") for u in uses), "actions/cache step for UNITE db")
    rep.check("scripts/fetch_reference.py --out ref/unite_sintax.fasta.gz" in runs, "fetch_reference on cache miss")
    rep.check("scripts/actions_runner.py --job-id" in runs
              and "github.event.client_payload.job_id || inputs.job_id" in runs, "runner invoked with dispatch/input job_id")
    env_run = next((s.get("env", {}) for s in steps if "actions_runner.py" in s.get("run", "")), {})
    for k in ("GOOGLE_SERVICE_ACCOUNT_JSON", "VSEARCH_BIN", "UNITE_SINTAX_DB", "DATA_DIR", "PUBLIC_BASE_URL"):
        rep.check(k in env_run, f"runner env has {k}")
    rep.check("secrets.GOOGLE_TOKEN_JSON" in str(env_run.get("GOOGLE_SERVICE_ACCOUNT_JSON")), "secret GOOGLE_TOKEN_JSON mapped")
    rep.check("vars.PUBLIC_BASE_URL" in str(env_run.get("PUBLIC_BASE_URL")), "vars.PUBLIC_BASE_URL mapped")
    rep.check("fungal-platform-bot" in runs and "git pull --rebase" in runs and "git diff --cached --quiet" in runs,
              "commit step: bot identity, pull --rebase, skip when unchanged")
    echo_lines = [l for l in runs.splitlines() if "echo" in l]
    rep.check(not any("GOOGLE_SERVICE_ACCOUNT_JSON" in l or "secrets." in l or "GOOGLE_TOKEN_JSON" in l and "::error" not in l
                      for l in echo_lines), "no echo of the Google secret")


def strip_js(src: str) -> str:
    """Remove string literals, template literals, regex-free line/block comments from JS source."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        two = src[i:i + 2]
        if two == "//":
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif two == "/*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in ("'", '"', "`"):
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append("''")
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def check_apps_script(rep: Report) -> None:
    src = CODE_GS.read_text(encoding="utf-8")
    stripped = strip_js(src)
    for a, b in (("{", "}"), ("(", ")"), ("[", "]")):
        rep.check(stripped.count(a) == stripped.count(b), f"Code.gs balanced {a}{b}: {stripped.count(a)} / {stripped.count(b)}")
    for fn in ("setup", "onFormSubmit", "dispatchGitHub", "appendQueueRow"):
        rep.check(re.search(rf"function\s+{fn}\s*\(", src) is not None, f"Code.gs defines {fn}()")
    rep.check("FormApp.DestinationType.SPREADSHEET" in src, "Code.gs sets the Form destination spreadsheet")
    rep.check("repository_dispatch" in src or "/dispatches" in src, "Code.gs posts repository_dispatch")
    rep.check("GITHUB_PAT" in src and "GITHUB_REPO" in src, "Code.gs reads GITHUB_REPO / GITHUB_PAT script properties")
    rep.check("CC BY 4.0" in src, "Code.gs consent text mentions CC BY 4.0")
    hdr_in_gs = re.search(r"QUEUE_HEADERS\s*=\s*\[(.*?)\]", src, flags=re.S)
    gs_headers = re.findall(r"'([a-z_]+)'", hdr_in_gs.group(1)) if hdr_in_gs else []
    rep.check(gs_headers == QUEUE_HEADERS, "Code.gs QUEUE_HEADERS == platform_config ledger_headers.queue")


# ------------------------------------------------------------------ data
def ensure_ena(ena_dir: Path, rep: Report) -> list[Path]:
    ena_dir.mkdir(parents=True, exist_ok=True)
    report_path = ena_dir / "filereport.tsv"
    if not report_path.exists():
        with urllib.request.urlopen(ENA_REPORT, timeout=120) as r:
            report_path.write_bytes(r.read())
    rows = [l.split("\t") for l in report_path.read_text().strip().splitlines()]
    hdr, body = rows[0], rows[1:]
    files = []
    for row in body:
        d = dict(zip(hdr, row))
        for ftp, nbytes, md5 in zip(d["fastq_ftp"].split(";"), d["fastq_bytes"].split(";"), d["fastq_md5"].split(";")):
            dest = ena_dir / ftp.rsplit("/", 1)[-1]
            if not dest.exists() or dest.stat().st_size != int(nbytes):
                with urllib.request.urlopen("https://" + ftp, timeout=900) as r, open(dest, "wb") as f:
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
            got_md5 = hashlib.md5(dest.read_bytes()).hexdigest()
            rep.check(dest.stat().st_size == int(nbytes) and got_md5 == md5,
                      f"ENA {dest.name}: {dest.stat().st_size} bytes, md5 {got_md5} == ENA record")
            files.append(dest)
    return sorted(files)


def write_queue(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(QUEUE_HEADERS)
        for r in rows:
            w.writerow([r.get(h, "") for h in QUEUE_HEADERS])


def read_queue(path: Path) -> dict[str, dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return {r["job_id"]: r for r in rows}


def run_runner(args_list: list[str], env: dict, log_path: Path) -> tuple[int, float]:
    t0 = time.time()
    with open(log_path, "w", encoding="utf-8") as fh:
        p = subprocess.run([sys.executable, str(RUNNER), *args_list], env=env, stdout=fh, stderr=subprocess.STDOUT,
                           cwd=REPO, text=True)
    return p.returncode, time.time() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vsearch", default=os.environ.get("VSEARCH_BIN", "vsearch"))
    ap.add_argument("--db", default=os.environ.get("UNITE_SINTAX_DB", ""), required=not os.environ.get("UNITE_SINTAX_DB"))
    ap.add_argument("--ena-dir", default=str(REPO / "tests" / "data" / "ena_PRJNA889370"))
    ap.add_argument("--work", default=str(REPO / "tests" / "work_actions_runner"))
    ap.add_argument("--skip-amplicon", action="store_true")
    ap.add_argument("--threads", default="4")
    a = ap.parse_args()

    rep = Report()
    rep.note("# actions_runner local-mode test report\n")
    rep.note(f"Generated {utcnow()} on {sys.platform}, Python {sys.version.split()[0]}.")
    rep.note("Mode: Storage LOCAL_MODE (no Google credentials), queue in CSV (`--local-queue`), "
             "file_ids as local paths (`--local-files`). Drive/Sheets writes were NOT exercised.\n")

    check_workflow(rep)
    check_apps_script(rep)

    work = Path(a.work)
    if work.exists():
        import shutil

        shutil.rmtree(work)
    data_dir = work / "data"
    docs_jobs = work / "docs_jobs"
    data_dir.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if k not in ("GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_CREDENTIALS_JSON")}
    env.update(DATA_DIR=str(data_dir), LOCAL_MODE="1", VSEARCH_BIN=a.vsearch, UNITE_SINTAX_DB=a.db,
               PIPELINE_THREADS=a.threads, PUBLIC_BASE_URL="https://example.github.io/fungal-dna-platform",
               DOCS_JOBS_DIR=str(docs_jobs), PYTHONUNBUFFERED="1")

    timings: dict[str, float] = {}

    # ---------------- 2. barcoding job (+ a deliberately broken job in the same run)
    rep.note("\n## 2. Barcoding job (local mode)\n")
    bc_id, bad_id = new_id(), new_id()
    # pasted bare sequences = the first two records of the test FASTA (Aspergillus flavus, Fusarium equiseti
    # exact UNITE copies) separated by a blank line, as a user would paste them without headers
    fa_recs = [r for r in (REPO / "tests" / "data" / "barcoding_test_queries.fasta").read_text().split(">") if r.strip()]
    pasted = "\n\n".join("".join(r.strip().splitlines()[1:]) for r in fa_recs[:2])
    queue_csv = work / "queue_barcode.csv"
    write_queue(queue_csv, [
        dict(job_id=bc_id, job_type="barcode", submitted_at=utcnow(), status="queued", submitter="local test",
             submitter_email="", marker="ITS", location="Visakhapatnam", lat="17.68", lon="83.21",
             sequences_text=pasted, file_ids=str(REPO / "tests" / "data" / "barcoding_test_queries.fasta"),
             file_names="barcoding_test_queries.fasta - Test Respondent", form_response_id="local-1"),
        dict(job_id=bad_id, job_type="amplicon", submitted_at=utcnow(), status="queued", submitter="local test",
             marker="ITS1", fwd_primer=FWD, rev_primer=REV, pairing="paired-end", location="",
             file_ids=str(work / "does_not_exist_R1.fastq.gz"), file_names="does_not_exist_R1.fastq.gz",
             form_response_id="local-2"),
    ])
    rc, dt = run_runner(["--local-queue", str(queue_csv), "--local-files", "--job-id", bc_id], env, work / "runner_barcode.log")
    timings["barcode run (2 jobs incl. 1 broken)"] = dt
    rep.check(rc == 0, f"runner exit code 0 on the barcoding run (got {rc}, {dt:.0f}s)")
    q = read_queue(queue_csv)
    rep.check(q[bc_id]["status"] == "done", f"queue row {bc_id} -> done (got {q[bc_id]['status']!r})")
    rep.check(bool(q[bc_id]["started_at"]) and bool(q[bc_id]["completed_at"]), "started_at / completed_at filled")
    rep.check(q[bc_id]["result_page_url"] == f"https://example.github.io/fungal-dna-platform/job.html?id={bc_id}",
              f"result_page_url = {q[bc_id]['result_page_url']}")
    rep.check(q[bad_id]["status"] == "failed" and q[bad_id]["error"] != "",
              f"broken job {bad_id} -> failed with error text ({q[bad_id]['error'][:90]!r})")
    pasted_fa = data_dir / "jobs" / bc_id / "input" / "pasted.fasta"
    rep.check(pasted_fa.exists() and pasted_fa.read_text().count(">") == 2, "input/pasted.fasta written with 2 FASTA records")
    rep.check((data_dir / "jobs" / bc_id / "input" / "barcoding_test_queries.fasta").exists(),
              "Form ' - Respondent' suffix stripped from the uploaded file name")
    cj = docs_jobs / f"{bc_id}.json"
    ok = cj.exists()
    bres = {}
    if ok:
        try:
            bres = json.loads(cj.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            ok = False
    rep.check(ok, f"docs/jobs/{bc_id}.json exists and parses ({cj.stat().st_size if cj.exists() else 0} bytes)")
    rep.check(bres.get("status") == "done" and bres.get("job_type") == "barcode", "compact JSON status done / job_type barcode")
    nq = len(bres.get("barcoding", []))
    rep.check(nq == 9, f"9 queries in the barcoding result (7 from the FASTA + 2 pasted; got {nq})")
    if bres.get("barcoding"):
        rep.note("\nBarcoding assignments:\n")
        rep.note("| query | input | identity % | coverage % | best hit | level |")
        rep.note("|---|---|---|---|---|---|")
        for b in bres["barcoding"]:
            rep.note(f"| {b['query_id']} | {b.get('input_file','')} | {b.get('identity_pct')} | {b.get('coverage_pct')} | "
                     f"{b.get('best_hit_species')} | {b.get('assignment_level')} |")
    ledger = data_dir / "ledger"
    for sheet in ("jobs", "barcoding_hits"):
        p = ledger / f"{sheet}.csv"
        rows = list(csv.reader(open(p, encoding="utf-8"))) if p.exists() else []
        rep.check(bool(rows) and any(r and r[0] == bc_id for r in rows[1:]), f"ledger {sheet}.csv has rows for {bc_id}")
    bad_cj = docs_jobs / f"{bad_id}.json"
    rep.check(bad_cj.exists() and json.loads(bad_cj.read_text())["status"] == "failed",
              "failed job also gets a docs/jobs JSON (status failed) so its page can show the error")
    rep.note(f"\nTiming: {dt:.0f} s for the whole run (runner start-up + 2 jobs).")

    # ---------------- 3. amplicon job
    if not a.skip_amplicon:
        rep.note("\n## 3. Amplicon job: ENA PRJNA889370 (local mode)\n")
        fastqs = ensure_ena(Path(a.ena_dir), rep)
        am_id = new_id()
        queue_csv2 = work / "queue_amplicon.csv"
        write_queue(queue_csv2, [
            dict(job_id=am_id, job_type="amplicon", submitted_at=utcnow(), status="queued", submitter="local test",
                 submitter_email="", marker="ITS1", fwd_primer=FWD, rev_primer=REV, pairing="paired-end",
                 location="Kerala coast (west coast)", lat="9.5", lon="76.3",
                 file_ids=",".join(str(p) for p in fastqs),
                 file_names=",".join(f"{p.name} - Test Respondent" for p in fastqs), form_response_id="local-3"),
        ])
        rc, dt = run_runner(["--local-queue", str(queue_csv2), "--local-files"], env, work / "runner_amplicon.log")
        timings["amplicon run (PRJNA889370, 4 files)"] = dt
        rep.check(rc == 0, f"runner exit code 0 on the amplicon run (got {rc}, {dt:.0f}s)")
        q2 = read_queue(queue_csv2)
        rep.check(q2[am_id]["status"] == "done", f"queue row {am_id} -> done (got {q2[am_id]['status']!r}; error={q2[am_id]['error'][:200]!r})")
        cj = docs_jobs / f"{am_id}.json"
        ares = {}
        ok = cj.exists()
        if ok:
            try:
                ares = json.loads(cj.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                ok = False
        rep.check(ok, f"docs/jobs/{am_id}.json exists and parses ({cj.stat().st_size if cj.exists() else 0} bytes)")
        rep.check(cj.exists() and cj.stat().st_size <= 400 * 1024, "compact copy <= 400 KB")
        s = ares.get("summary", {})
        rep.note("\nSummary vs reference run:\n")
        rep.note("| metric | this run | reference |")
        rep.note("|---|---|---|")
        for k, v in EXPECTED_AMPLICON.items():
            rep.note(f"| {k} | {s.get(k)} | {v} |")
            rep.check(s.get(k) == v, f"summary.{k} == {v} (got {s.get(k)})")
        rep.check(s.get("n_samples") == 2, f"2 samples detected from 4 files (got {s.get('n_samples')})")
        for sm in ares.get("samples", []):
            rep.note(f"- sample {sm['sample_id']}: in {sm['reads_in']}, merged {sm['reads_merged']}, filtered {sm['reads_filtered']}, "
                     f"non-chimeric {sm['reads_nonchimeric']}, ASVs {sm['n_asvs']}, fungal {sm['n_fungal_asvs']}, "
                     f"Shannon {sm['shannon']}, % fungal {sm['pct_fungal']}")
        for sheet in ("jobs", "samples", "taxa"):
            p = ledger / f"{sheet}.csv"
            rows = list(csv.reader(open(p, encoding="utf-8"))) if p.exists() else []
            rep.check(bool(rows) and any(r and r[0] == am_id for r in rows[1:]), f"ledger {sheet}.csv has rows for {am_id}")
        files = ares.get("files", {})
        for name in ("asv_table.tsv", "asv_taxonomy.tsv", "asvs.fasta", "read_tracking.tsv", "result.json"):
            rep.check(name in files and (data_dir / "results" / am_id / name).exists(), f"result file {name} uploaded (local copy present)")
        rep.note(f"\nTiming: {dt:.0f} s for the whole run (runner start-up + download/copy + pipeline with {a.threads} threads).")
    else:
        rep.note("\n## 3. Amplicon job: SKIPPED (--skip-amplicon)\n")

    rep.note("\n## Timings\n")
    for k, v in timings.items():
        rep.note(f"- {k}: {v:.0f} s")
    rep.note(f"\n## Result: {rep.checks - len(rep.failures)}/{rep.checks} checks passed"
             + ("" if not rep.failures else "\n\nFailures:\n" + "\n".join(f"- {f}" for f in rep.failures)))
    out = REPO / "tests" / "test_actions_runner_report.md"
    out.write_text("\n".join(rep.lines) + "\n", encoding="utf-8")
    print("\n".join(rep.lines))
    print(f"\nreport written to {out}")
    return 1 if rep.failures else 0


if __name__ == "__main__":
    sys.exit(main())
