#!/usr/bin/env python
"""Smoke test a deployed or local instance: /health, every page, one barcoding job.
Usage: python scripts/smoke_test.py https://<service-url>
"""
import json, os, sys, time, urllib.parse, urllib.request

base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080").rstrip("/")
fa = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "data", "barcoding_test_queries.fasta")
TEST_SEQ = ">" + open(fa).read().split(">")[1]          # exact UNITE Aspergillus flavus record


def get(path, timeout=120):
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def check(name, ok, extra=""):
    print(f"[{'OK ' if ok else 'FAIL'}] {name} {extra}")
    return bool(ok)


results, h, s = [], {}, 0
for _ in range(30):                       # first start builds the UNITE database (2-3 min)
    try:
        s, body = get("/health"); h = json.loads(body)
        if h.get("unite_db_present"):
            break
        print("  waiting for reference database ...")
    except Exception as e:                # noqa: BLE001
        print("  service not ready:", e)
    time.sleep(10)
results.append(check("GET /health", s == 200 and h.get("unite_db_present"), json.dumps(h)[:160]))
results.append(check("Google credentials configured", h.get("service_account_configured") is True,
                     "(false = LOCAL_MODE: results stay in the container)"))
for p in ["/", "/submit", "/barcode", "/dashboard"]:
    s, body = get(p); results.append(check(f"GET {p}", s == 200 and "<html" in body.lower()))
data = urllib.parse.urlencode({"sequences": TEST_SEQ, "submitter": "smoke-test"}).encode()
with urllib.request.urlopen(urllib.request.Request(base + "/api/barcode", data=data, method="POST"), timeout=120) as r:
    jid = json.loads(r.read())["job_id"]
print("barcode job", jid)
status, j = None, {}
for _ in range(60):
    s, body = get(f"/api/jobs/{jid}"); j = json.loads(body); status = j.get("status")
    if status in ("done", "failed"):
        break
    time.sleep(5)
results.append(check("barcode job finished", status == "done", f"status={status}"))
if status == "done":
    b = (j.get("result") or {}).get("barcoding", [])
    results.append(check("Aspergillus flavus at species level",
                         bool(b) and b[0].get("assignment_level") == "species" and "Aspergillus" in str(b[0].get("best_hit_species")),
                         f"{b[0].get('best_hit_species')} {b[0].get('identity_pct')}%" if b else "no hits"))
    files = (j.get("result") or {}).get("files", {})
    results.append(check("files published to Drive", any("drive.google.com" in str(u) for u in files.values()), "(LOCAL_MODE if false)"))
s, body = get(f"/jobs/{jid}"); results.append(check("GET /jobs/{id}", s == 200))
print("\nALL PASSED" if all(results) else "\nSOME CHECKS FAILED")
sys.exit(0 if all(results) else 1)
