"""Dashboard track page tests: serve tests/stub_app.py on 127.0.0.1:8012 and check each page.

Run from the repo root:  python tests/test_dashboard_pages.py
Writes tests/test_dashboard_pages_results.json.
"""
from __future__ import annotations

import csv
import json
import sys
import threading
import time
from pathlib import Path

import requests
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.stub_app import app  # noqa: E402

HOST, PORT = "127.0.0.1", 8012
BASE = f"http://{HOST}:{PORT}"
CAVEAT = "Results are automated and provisional; no negative controls are enforced; verify taxonomic assignments before publication"
CITES = ["Martin 2011", "Rognes et al. 2016", "Abarenkov et al. 2024", "Edgar 2016", "Plotly"]
CFG = json.loads((ROOT / "platform_config.json").read_text(encoding="utf-8"))
SID = CFG["ledger"]["spreadsheet_id"]
HEADERS = CFG["ledger_headers"]


class _HttpClient:
    """requests against the real uvicorn socket server."""
    mode = "uvicorn socket 127.0.0.1:8012"

    def get(self, path, **kw):
        kw.setdefault("timeout", 30)
        return requests.get(BASE + path, **kw)


class _AsgiClient:
    """Fallback: in-process ASGI client (no sockets). Same app, routers, templates and headers."""
    mode = "in-process fastapi.testclient.TestClient (loopback sockets blocked in sandbox)"

    def __init__(self):
        from fastapi.testclient import TestClient
        self.c = TestClient(app, base_url=BASE)

    def get(self, path, **kw):
        kw.pop("timeout", None)
        return self.c.get(path, **kw)


def start_server():
    """Return (client, server|None). Tries the real socket server first, then the ASGI fallback."""
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(30):
        try:
            if requests.get(BASE + "/health", timeout=1).ok:
                return _HttpClient(), server
        except requests.RequestException:
            time.sleep(0.1)
    server.should_exit = True
    return _AsgiClient(), None


results = []
CLIENT = None


def check(name, cond, detail=""):
    results.append({"check": name, "pass": bool(cond), "detail": detail})
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def page_checks(path, must_contain, name):
    client = CLIENT
    r = client.get(path)
    check(f"{name}: HTTP 200", r.status_code == 200, str(r.status_code))
    check(f"{name}: no X-Frame-Options", "x-frame-options" not in {k.lower() for k in r.headers}, str(dict(r.headers)))
    check(f"{name}: no frame-ancestors CSP", "frame-ancestors" not in r.headers.get("content-security-policy", ""))
    check(f"{name}: no Set-Cookie", "set-cookie" not in {k.lower() for k in r.headers})
    check(f"{name}: viewport meta", 'name="viewport"' in r.text)
    check(f"{name}: caveat footer", CAVEAT in r.text)
    check(f"{name}: citations", all(c in r.text for c in CITES), str([c for c in CITES if c not in r.text]))
    for frag in must_contain:
        check(f"{name}: contains {frag!r}", frag in r.text)
    return r


def main():
    global CLIENT
    client, server = start_server()
    CLIENT = client
    print("client mode:", client.mode)
    results.append({"check": "client mode", "pass": True, "detail": client.mode})
    try:
        page_checks("/", ['href="/submit"', 'href="/barcode"', 'href="/dashboard"', 'id="kpi-strip"', f'data-spreadsheet-id="{SID}"'], "landing")
        page_checks("/submit", ['id="amplicon-form"', 'name="marker"', 'value="ITS1"', 'value="ITS2"', 'value="18S-V4"', 'value="18S-V9"', 'value="other"',
                                'name="fwd_primer"', 'name="rev_primer"', 'name="location"', 'name="lat"', 'name="lon"', 'name="submitter"', 'name="pairing"', '/static/submit.js'], "submit")
        page_checks("/barcode", ['id="barcode-form"', 'name="sequences"', 'name="files"', 'name="submitter"', '/static/barcode.js'], "barcode")
        r = page_checks("/dashboard", ['id="dashboard"', f'data-spreadsheet-id="{SID}"', 'gviz/tq?tqx=out:csv&amp;sheet={sheet}', 'id="plot-submissions"', 'id="plot-map"', 'id="plot-genera"',
                                       'id="plot-barcode-genera"', 'id="plot-phyla"', 'id="jobs-table"', 'id="last-updated"', 'kpi-barcodes', '/static/plotly.min.js', 'cdn.plot.ly/plotly-2.35.2.min.js'], "dashboard")
        check("dashboard: LEDGER_CONFIG injected from platform_config.json", f'"spreadsheet_id": "{SID}"' in r.text)
        page_checks("/dashboard?embed=1", ['class="embed"'], "dashboard-embed")
        for jid in ["ebe1f6d12027", "cabb8aa86f74", "demo-barcode", "stub-queued", "stub-failed"]:
            page_checks(f"/jobs/{jid}", [f'data-job-id="{jid}"', 'id="amplicon-view"', 'id="barcode-view"', 'id="asv-table"', '/static/job.js'], f"job {jid}")
        # static assets
        for a in ["/static/style.css", "/static/app.js", "/static/dashboard.js", "/static/job.js", "/static/submit.js", "/static/barcode.js", "/static/plotly.min.js"]:
            rr = client.get(a)
            check(f"static {a}: 200", rr.status_code == 200 and len(rr.content) > 100, str(rr.status_code))
        # submit.js primer presets
        js = client.get("/static/submit.js").text
        for primer in ["CTTGGTCATTTAGAGGAAGTAA", "GCTGCGTTCTTCATCGATGC", "GCATCGATGAAGAACGCAGC", "TCCTCCGCTTATTGATATGC", "GTGAATCATCGAATCTTTGAA",
                       "CCAGCASCYGCGGTAATTCC", "ACTTTCGTTCTTGATYRA", "GTACACACCGCCCGTC", "TGATCCTTCTGCAGGTTCACCTAC"]:
            check(f"submit.js primer preset {primer}", primer in js)
        # API stub + result schema
        for jid, jtype in [("ebe1f6d12027", "amplicon"), ("cabb8aa86f74", "amplicon"), ("demo-barcode", "barcode")]:
            j = client.get(f"/api/jobs/{jid}").json()
            res = j["result"]
            check(f"api {jid}: status done & type", j["status"] == "done" and j["job_type"] == jtype)
            need = ["job_id", "job_type", "status", "submitted_at", "completed_at", "params", "summary", "samples", "files", "notes"] + (["taxa", "composition"] if jtype == "amplicon" else ["barcoding"])
            check(f"api {jid}: result schema keys", all(k in res for k in need), str([k for k in need if k not in res]))
            if jtype == "amplicon":
                check(f"api {jid}: taxa <= 500", len(res["taxa"]) <= 500, str(len(res["taxa"])))
        # ledger CSVs: exact header order, parse, non-empty
        for sheet, hdr in HEADERS.items():
            rr = client.get(f"/test-ledger/{sheet}.csv")
            rows = list(csv.reader(rr.text.splitlines()))
            check(f"ledger {sheet}: header order exact", rows[0] == hdr, str(rows[0]))
            check(f"ledger {sheet}: has data rows", len(rows) > 1, str(len(rows)))
            re_ = client.get(f"/test-ledger-empty/{sheet}.csv")
            erows = list(csv.reader(re_.text.splitlines()))
            check(f"empty ledger {sheet}: headers only", erows == [hdr])
        taxa_rows = list(csv.DictReader(client.get("/test-ledger/taxa.csv").text.splitlines()))
        per_job = {}
        for t in taxa_rows:
            per_job.setdefault(t["job_id"], set()).add(t["asv_id"])
        check("ledger taxa: <= 200 ASVs per job", all(len(v) <= 200 for v in per_job.values()), str({k: len(v) for k, v in per_job.items()}))
        hits = list(csv.DictReader(client.get("/test-ledger/barcoding_hits.csv").text.splitlines()))
        check("ledger barcoding_hits: 3 demo-barcode rows", len(hits) == 3 and all(h["job_id"] == "demo-barcode" for h in hits))
        check("404 for unknown job", client.get("/api/jobs/nope").status_code == 404)
    finally:
        if server is not None:
            server.should_exit = True
            time.sleep(0.5)
    n_pass = sum(r["pass"] for r in results)
    out = {"n_checks": len(results), "n_pass": n_pass, "n_fail": len(results) - n_pass, "checks": results}
    (ROOT / "tests" / "test_dashboard_pages_results.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\n{n_pass}/{len(results)} checks passed")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
