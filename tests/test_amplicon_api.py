"""
End-to-end test of the amplicon service in LOCAL_MODE.

Usage (from the repo root, server already running or started by this script):
    python tests/test_amplicon_api.py --reads demo_reads --base http://127.0.0.1:8011 \
        --marker ITS1 --fwd CTTGGTCATTTAGAGGAAGTAA --rev GCTGCGTTCTTCATCGATGC [--start-server]

Environment needed by the server: VSEARCH_BIN, UNITE_SINTAX_DB, DATA_DIR, LOCAL_MODE=1.
The script posts every *.fastq(.gz) in --reads, polls the job, then asserts the
result JSON schema, n_asvs > 0, taxonomy assigned, ledger CSV headers in
platform_config.json order, and that every listed file downloads.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
REQUIRED_TOP = ["job_id", "job_type", "status", "submitted_at", "completed_at", "params", "summary", "samples",
                "taxa", "composition", "files", "notes"]
REQUIRED_SUMMARY = ["n_samples", "n_reads_in", "n_reads_kept", "n_asvs", "n_fungal_asvs", "n_species", "marker",
                    "location", "lat", "lon"]
REQUIRED_SAMPLE = ["sample_id", "reads_in", "reads_merged", "reads_filtered", "reads_nonchimeric", "n_asvs",
                   "shannon", "pct_fungal"]
REQUIRED_TAXON = ["asv_id", "reads_total", "kingdom", "phylum", "class", "order", "family", "genus", "species",
                  "sintax_conf", "per_sample"]


def check_schema(res: dict) -> list[str]:
    p = [f"missing {k}" for k in REQUIRED_TOP if k not in res]
    p += [f"missing summary.{k}" for k in REQUIRED_SUMMARY if k not in res.get("summary", {})]
    p += [f"missing samples.{k}" for k in REQUIRED_SAMPLE if any(k not in s for s in res.get("samples", []))]
    p += [f"missing taxa.{k}" for k in REQUIRED_TAXON if any(k not in t for t in res.get("taxa", []))]
    p += [f"missing composition.{k}" for k in ("phylum", "genus") if k not in res.get("composition", {})]
    for k in ("submitted_at", "completed_at"):
        try:
            datetime.strptime(res[k], "%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, ValueError):
            p.append(f"{k} is not ISO 8601 UTC (YYYY-MM-DDTHH:MM:SSZ)")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reads", required=True, help="directory with FASTQ(.gz) files")
    ap.add_argument("--base", default="http://127.0.0.1:8011")
    ap.add_argument("--marker", default="ITS1")
    ap.add_argument("--fwd", default="")
    ap.add_argument("--rev", default="")
    ap.add_argument("--pairing", default="paired")
    ap.add_argument("--start-server", action="store_true")
    ap.add_argument("--timeout", type=int, default=3600)
    a = ap.parse_args()

    client = httpx.Client(trust_env=False, timeout=600)  # never route loopback through a proxy
    server = None
    if a.start_server:
        port = a.base.rsplit(":", 1)[-1]
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                   "--port", port], cwd=REPO)
    try:
        for _ in range(60):
            try:
                h = client.get(f"{a.base}/health")
                break
            except httpx.HTTPError:
                time.sleep(1)
        else:
            print("server not reachable")
            return 2
        print("health:", h.json())

        paths = sorted(p for p in glob.glob(os.path.join(a.reads, "*")) if p.lower().endswith((".fastq", ".fq", ".fastq.gz", ".fq.gz")))
        assert paths, "no FASTQ files found"
        files = [("files", (os.path.basename(p), open(p, "rb"), "application/octet-stream")) for p in paths]
        data = dict(marker=a.marker, fwd_primer=a.fwd, rev_primer=a.rev, pairing=a.pairing,
                    location="test", lat="0", lon="0", submitter="test_amplicon_api")
        t0 = time.time()
        r = client.post(f"{a.base}/api/amplicon", files=files, data=data)
        r.raise_for_status()
        job_id = r.json()["job_id"]
        print("job", job_id, "submitted with", [os.path.basename(p) for p in paths])
        while time.time() - t0 < a.timeout:
            j = client.get(f"{a.base}/api/jobs/{job_id}").json()
            if j["status"] in ("done", "failed"):
                break
            time.sleep(5)
        print(f"status={j['status']} after {time.time() - t0:.0f}s")
        if j["status"] != "done":
            print(json.dumps(j.get("result", {}).get("notes"), indent=1))
            return 1
        res = j["result"]
        problems = check_schema(res)
        s = res["summary"]
        if s["n_asvs"] <= 0:
            problems.append("n_asvs == 0")
        if not any(t.get("kingdom") for t in res["taxa"]):
            problems.append("no taxonomy assigned")
        for name, url in res["files"].items():
            fr = client.get(url)
            if fr.status_code != 200 or not fr.content:
                problems.append(f"file {name} not downloadable ({fr.status_code})")
        headers = json.load(open(REPO / "platform_config.json"))["ledger_headers"]
        data_dir = Path(h.json()["data_dir"])
        for sheet in ("jobs", "samples", "taxa"):
            path = data_dir / "ledger" / f"{sheet}.csv"
            if not path.exists():
                problems.append(f"ledger {sheet}.csv missing")
                continue
            rows = list(csv.reader(open(path, encoding="utf-8")))
            if rows[0] != headers[sheet]:
                problems.append(f"ledger {sheet} header mismatch: {rows[0]}")
            if any(len(r) != len(headers[sheet]) for r in rows):
                problems.append(f"ledger {sheet} has rows of wrong width")
            if not any(r and r[0] == job_id for r in rows[1:]):
                problems.append(f"ledger {sheet} has no row for job {job_id}")
        print(json.dumps(s, indent=1))
        for smp in res["samples"]:
            print(smp)
        print("PROBLEMS:", problems or "none")
        return 1 if problems else 0
    finally:
        if server:
            server.terminate()


if __name__ == "__main__":
    sys.exit(main())
