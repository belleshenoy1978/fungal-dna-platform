#!/usr/bin/env python
"""Seed a deployed instance with the public demonstrator dataset so the dashboard is not empty.
Downloads ENA PRJNA889370 (2 ITS1 libraries, Kerala mangrove sediment, ITS1F/ITS2; 32 MB),
verifies each file against ENA's byte count and MD5, then submits it to POST /api/amplicon.
Usage: python scripts/seed_demo.py https://<service-url>
"""
import hashlib, json, os, sys, time
import requests

base = sys.argv[1].rstrip("/")
rep = requests.get("https://www.ebi.ac.uk/ena/portal/api/filereport", timeout=60,
                   params={"accession": "PRJNA889370", "result": "read_run", "format": "tsv",
                           "fields": "run_accession,sample_alias,fastq_ftp,fastq_bytes,fastq_md5"}).text
rows = [l.split("\t") for l in rep.strip().splitlines()]
hdr, files = rows[0], []
os.makedirs("demo_reads", exist_ok=True)
for r in rows[1:]:
    d = dict(zip(hdr, r))
    for url, nb, md5 in zip(d["fastq_ftp"].split(";"), d["fastq_bytes"].split(";"), d["fastq_md5"].split(";")):
        fn = os.path.join("demo_reads", url.split("/")[-1])
        for _ in range(3):
            if os.path.exists(fn) and os.path.getsize(fn) == int(nb) and hashlib.md5(open(fn, "rb").read()).hexdigest() == md5:
                break
            with requests.get("https://" + url, stream=True, timeout=600) as rr, open(fn, "wb") as f:
                for chunk in rr.iter_content(1 << 20):
                    f.write(chunk)
        else:
            raise SystemExit(f"{fn}: failed ENA byte/MD5 verification; not submitting")
        print("verified", fn, nb); files.append(fn)
fh = [("files", (os.path.basename(f), open(f, "rb"), "application/gzip")) for f in files]
data = {"marker": "ITS1", "fwd_primer": "CTTGGTCATTTAGAGGAAGTAA", "rev_primer": "GCTGCGTTCTTCATCGATGC",
        "location": "Kerala mangrove sediment (Kannur, Ernakulam) - ENA PRJNA889370 demonstrator",
        "lat": "10.0", "lon": "76.3", "submitter": "platform demonstrator (public ENA data)", "pairing": "paired"}
r = requests.post(base + "/api/amplicon", files=fh, data=data, timeout=1800); r.raise_for_status()
jid = r.json()["job_id"]; print("submitted job", jid)
while True:
    j = requests.get(f"{base}/api/jobs/{jid}", timeout=60).json()
    if j.get("status") in ("done", "failed"):
        break
    time.sleep(10)
print(j["status"], json.dumps((j.get("result") or {}).get("summary"), indent=1)); print(f"result page: {base}/jobs/{jid}")
