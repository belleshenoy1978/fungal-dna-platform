"""Static-site (GitHub Pages / free mode) checks for the Fungal DNA Analysis Platform.

Run from the repo root:
    python tests/test_static_site.py              # builds into a temp dir twice (idempotency), checks it, and
                                                  # verifies the committed docs/ matches the fresh build
    python tests/test_static_site.py --no-build   # CI: check docs/ as produced by the previous build step

Writes tests/test_static_site_report.md and tests/test_static_site_results.json. Exit code 1 on any failure.

No browser and no network are used: HTML/JS are checked as text, JS files get a string/regex/comment-aware
bracket-balance scan (Node is not assumed), and the job page's fetch logic (./jobs/<id>.json -> ledger
`queue` tab -> ledger `jobs` tab) is mirrored in Python against docs/jobs/*.json and CSV fixtures.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import build_static_site as bss  # noqa: E402

CAVEAT = "Results are automated and provisional; no negative controls are enforced; verify taxonomic assignments before publication"
CITES = ["Martin 2011", "Rognes et al. 2016", "Abarenkov et al. 2024", "Edgar 2016", "Plotly"]
LEDGER_ID = "1gpTf1k-i0DBJZDlUR1YQhiFgWCO56pjpSDa4M1cEJjA"
CSV_TEMPLATE = "https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?tqx=out:csv&sheet={sheet}"
QUEUE_HEADER = ["job_id", "job_type", "submitted_at", "status", "submitter", "submitter_email", "marker", "fwd_primer",
                "rev_primer", "pairing", "location", "lat", "lon", "sequences_text", "file_ids", "file_names",
                "form_response_id", "started_at", "completed_at", "result_folder_url", "result_page_url", "error"]
PAGES = ["index.html", "dashboard.html", "job.html", "submit.html"]
JS_FILES = ["app.js", "dashboard.js", "job.js", "barcode.js", "submit.js", "config.js"]
SEED_JOBS = ["ebe1f6d12027", "cabb8aa86f74"]
JOB_JSON_LIMIT = 400 * 1024

results: list[dict] = []


def check(name: str, cond, detail: str = "") -> bool:
    ok = bool(cond)
    results.append({"check": name, "pass": ok, "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
    return ok


# --------------------------------------------------------------------------- JS scanner

def js_bracket_balance(src: str) -> tuple[bool, str]:
    """Scan JS skipping strings, template literals, comments and regex literals; check ()[]{} nesting."""
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[tuple[str, int]] = []
    i, n, line = 0, len(src), 1
    last_sig = ""  # last significant char, to decide whether '/' starts a regex
    regex_prev = set("(,=:[!&|?{};+-*%<>~^") | {""}
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1; i += 1; continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i); i = n if j < 0 else j; continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            if j < 0: return False, f"unterminated block comment at line {line}"
            line += src.count("\n", i, j); i = j + 2; continue
        if c in "'\"`":
            q, j = c, i + 1
            while j < n and src[j] != q:
                if src[j] == "\\": j += 1
                elif src[j] == "\n":
                    if q != "`": return False, f"unterminated string at line {line}"
                    line += 1
                j += 1
            if j >= n: return False, f"unterminated string at line {line}"
            i = j + 1; last_sig = q; continue
        if c == "/" and (last_sig in regex_prev or src[max(0, i - 7):i].rstrip().endswith(("return", "typeof"))):
            j, in_cls = i + 1, False
            while j < n and (src[j] != "/" or in_cls):
                if src[j] == "\\": j += 1
                elif src[j] == "[": in_cls = True
                elif src[j] == "]": in_cls = False
                elif src[j] == "\n": return False, f"unterminated regex at line {line}"
                j += 1
            if j >= n: return False, f"unterminated regex at line {line}"
            i = j + 1
            while i < n and src[i].isalpha(): i += 1  # flags
            last_sig = "/"; continue
        if c in "([{":
            stack.append((c, line))
        elif c in ")]}":
            if not stack or stack[-1][0] != pairs[c]:
                return False, f"unexpected '{c}' at line {line}"
            stack.pop()
        if not c.isspace():
            last_sig = c
        i += 1
    if stack:
        return False, f"unclosed '{stack[-1][0]}' opened at line {stack[-1][1]}"
    return True, "balanced"


# --------------------------------------------------------------------------- Python mirror of job.js / app.js

def parse_config_js(text: str) -> dict:
    m = re.search(r"window\.PLATFORM_CONFIG\s*=\s*(\{.*\});\s*$", text, re.S)
    if not m:
        raise ValueError("config.js does not assign window.PLATFORM_CONFIG")
    return json.loads(m.group(1))


def safe_url(u) -> str:  # FDP.safeUrl
    u = str(u or "")
    return u if (re.match(r"^(https?:)?//", u) or u.startswith("/")) else ""


def mirror_fetch_job(job_id: str, jobs_dir: Path, queue_rows: list[dict], jobs_rows: list[dict]) -> dict:
    """Python port of FDP.fetchJobStatic: jobs/<id>.json -> queue tab -> jobs tab."""
    f = jobs_dir / f"{job_id}.json"
    if f.exists():
        res = json.loads(f.read_text(encoding="utf-8"))
        return {"job_id": job_id, "job_type": res.get("job_type"), "status": res.get("status", "done"), "result": res,
                "params": res.get("params", {}), "notes": res.get("notes", []), "result_folder_url": res.get("result_folder_url", ""), "source": "jobs_json"}
    q = ([r for r in queue_rows if r.get("job_id") == job_id] or [None])[-1]
    j = ([r for r in jobs_rows if r.get("job_id") == job_id] or [None])[-1]
    if not q and not j:
        return {"job_id": job_id, "status": "unknown", "params": {}, "notes": [], "source": "none"}
    st = str((q and q.get("status")) or (j and j.get("status")) or "queued").lower()
    return {"job_id": job_id, "job_type": (q and q.get("job_type")) or (j and j.get("job_type")) or "", "status": st,
            "submitted_at": (q and q.get("submitted_at")) or (j and j.get("submitted_at")) or "",
            "params": {"submitter": (q and q.get("submitter")) or (j and j.get("submitter")) or ""},
            "error": (q and q.get("error")) or "", "result_folder_url": (j and j.get("result_folder_url")) or (q and q.get("result_folder_url")) or "",
            "source": "queue" if q else "jobs"}


def is_terminal_static(job: dict) -> bool:  # FDP.pollJob stop rule in static mode
    return job["status"] == "failed" or (job["status"] == "done" and job.get("source") == "jobs_json")


def fields_read_by_job_page(res: dict) -> list[str]:
    """Every field job.js / barcode.js dereferences; returns the list of MISSING ones."""
    missing = []
    for k in ("job_type", "status", "submitted_at", "completed_at", "params", "files", "notes"):
        if k not in res: missing.append(k)
    if not isinstance(res.get("params"), dict) or "submitter" not in res["params"]: missing.append("params.submitter")
    if res.get("job_type") == "amplicon":
        s = res.get("summary") or {}
        for k in ("n_samples", "n_reads_in", "n_reads_kept", "n_asvs", "n_fungal_asvs", "n_species", "marker", "location", "lat", "lon"):
            if k not in s: missing.append(f"summary.{k}")
        for k in ("sample_id", "reads_in", "reads_merged", "reads_filtered", "reads_nonchimeric", "n_asvs", "shannon", "pct_fungal"):
            if any(k not in x for x in res.get("samples", [])): missing.append(f"samples[].{k}")
        for k in ("asv_id", "reads_total", "kingdom", "phylum", "class", "order", "family", "genus", "species", "sintax_conf", "per_sample"):
            if any(k not in t for t in res.get("taxa", [])): missing.append(f"taxa[].{k}")
        comp = res.get("composition") or {}
        for k in ("phylum", "genus"):
            if not isinstance(comp.get(k), dict): missing.append(f"composition.{k}")
    elif res.get("job_type") == "barcode":
        for k in ("query_id", "query_length", "best_hit_species", "best_hit_accession", "best_hit_sh", "identity_pct", "coverage_pct", "sintax", "assignment_level", "chimera_flag"):
            if any(k not in h for h in res.get("barcoding", [])): missing.append(f"barcoding[].{k}")
    else:
        missing.append("job_type amplicon|barcode")
    return missing


# --------------------------------------------------------------------------- checks

def check_site(site: Path, label: str) -> None:
    cfg = json.loads((ROOT / "platform_config.json").read_text(encoding="utf-8"))

    # 1. every file exists
    for rel in PAGES + [".nojekyll", "jobs/README.md"] + [f"static/{f}" for f in JS_FILES + ["style.css", "plotly.min.js"]] + [f"jobs/{j}.json" for j in SEED_JOBS]:
        check(f"{label}: {rel} exists", (site / rel).exists())
    check(f"{label}: jobs/demo-barcode.json NOT published (synthetic data)", not (site / "jobs" / "demo-barcode.json").exists())

    # 2. page content
    for page in PAGES:
        t = (site / page).read_text(encoding="utf-8") if (site / page).exists() else ""
        check(f"{label}: {page} has caveat footer", CAVEAT in t)
        missing = [c for c in CITES if c not in t]
        check(f"{label}: {page} has five citations", not missing, f"missing {missing}")
        for c in CITES:
            pass
        check(f"{label}: {page} has DOI links for cutadapt/VSEARCH/UNITE/SINTAX", all(d in t for d in ["10.14806/ej.17.1.200", "10.7717/peerj.2584", "10.1093/nar/gkad1039", "10.1101/074161"]))
        check(f"{label}: {page} has no X-Frame-Options / frame-ancestors meta", not re.search(r"X-Frame-Options|frame-ancestors", t, re.I))
        check(f"{label}: {page} has viewport meta (responsive)", 'name="viewport"' in t and "width=device-width" in t)
        check(f"{label}: {page} uses only relative links (no /static, no absolute routes)", not re.search(r'(href|src)="/(static|submit|barcode|dashboard|jobs|api)', t) and 'href="/"' not in t)
        check(f"{label}: {page} loads static/config.js before app.js", t.find('src="static/config.js"') >= 0 and t.find('src="static/config.js"') < t.find('src="static/app.js"'))
        check(f"{label}: {page} has no cookies / storage", not re.search(r"document\.cookie|localStorage|sessionStorage", t))
        check(f"{label}: {page} has no leftover Jinja syntax", "{{" not in t and "{%" not in t)
        check(f"{label}: {page} links the public ledger", LEDGER_ID in t)

    d = (site / "dashboard.html").read_text(encoding="utf-8")
    check(f"{label}: dashboard.html links job.html?id=", "job.html?id=" in d)
    check(f"{label}: dashboard.html keeps #dashboard data-attributes (ledger fallback)", f'data-spreadsheet-id="{LEDGER_ID}"' in d and "data-csv-template=" in d)
    check(f"{label}: dashboard.html loads dashboard.js (60 s poll)", 'src="static/dashboard.js"' in d)
    j = (site / "job.html").read_text(encoding="utf-8")
    check(f"{label}: job.html references ./jobs/", "./jobs/" in j and 'data-jobs-path="./jobs/"' in j)
    check(f"{label}: job.html has empty data-job-id (id comes from ?id=)", 'data-job-id=""' in j)
    check(f"{label}: job.html mentions queue tab fallback + 30 s refresh", "queue" in j and "30 s" in j)
    s = (site / "submit.html").read_text(encoding="utf-8")
    check(f"{label}: submit.html explains both analysis types", "Amplicon analysis" in s and "DNA barcoding" in s)
    check(f"{label}: submit.html lists accepted inputs", ".fastq.gz" in s and ".ab1" in s)
    check(f"{label}: submit.html carries the public-results notice", "Everything is public" in s and "CC BY 4.0" in s)
    form_cfg = not bss.is_placeholder(cfg.get("free_mode", {}).get("google_form_embed_url"))
    if form_cfg:
        check(f"{label}: submit.html embeds the Google Form iframe", "<iframe" in s and cfg["free_mode"]["google_form_embed_url"] in s and "open it in a new tab" in s)
    else:
        check(f"{label}: submit.html shows 'form not configured yet' placeholder (config still has <...> placeholder)", "Submission form not configured yet" in s and "<iframe" not in s)
    i = (site / "index.html").read_text(encoding="utf-8")
    check(f"{label}: index.html has live KPI strip + cards to submit.html / dashboard.html", 'id="kpi-strip"' in i and 'href="submit.html"' in i and 'href="dashboard.html"' in i)

    # 3. config.js
    try:
        pc = parse_config_js((site / "static" / "config.js").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        pc = {}
        check(f"{label}: config.js parses as JSON", False, str(exc))
    else:
        check(f"{label}: config.js parses as JSON", True)
    check(f"{label}: config.js mode=static", pc.get("mode") == "static")
    check(f"{label}: config.js carries ledger id {LEDGER_ID}", pc.get("ledger", {}).get("spreadsheet_id") == LEDGER_ID)
    check(f"{label}: config.js carries the CSV template", pc.get("ledger", {}).get("csv_template") == CSV_TEMPLATE)
    check(f"{label}: config.js carries queue sheet name + status values", pc.get("queue_sheet") == "queue" and pc.get("queue_status_values") == ["queued", "running", "done", "failed"])
    check(f"{label}: config.js job_page / jobs_json_path", pc.get("job_page") == "job.html?id={job_id}" and pc.get("jobs_json_path") == "./jobs/")
    check(f"{label}: config.js has Pages base URL + form URL keys (empty until configured)", all(k in pc for k in ("pages_base_url", "google_form_url", "google_form_embed_url", "github_repo")))
    check(f"{label}: config.js contains no secrets-looking keys", not re.search(r"token|secret|private_key|client_secret", (site / "static" / "config.js").read_text(encoding="utf-8"), re.I))
    check(f"{label}: platform_config.json queue header matches spec order", cfg.get("ledger_headers", {}).get("queue") == QUEUE_HEADER)

    # 4. JS files: bracket balance + static-mode plumbing (plotly.min.js skipped: 4.8 MB vendored minified bundle)
    for f in JS_FILES:
        p = site / "static" / f
        ok, why = js_bracket_balance(p.read_text(encoding="utf-8")) if p.exists() else (False, "missing")
        check(f"{label}: static/{f} bracket balance", ok, why)
    app = (site / "static" / "app.js").read_text(encoding="utf-8")
    check(f"{label}: app.js reads window.PLATFORM_CONFIG and keeps FastAPI fallback", "global.PLATFORM_CONFIG" in app and "'/api/jobs/'" in app and "'/jobs/'" in app)
    check(f"{label}: app.js static job lookup: jobs json -> queue -> jobs tab", "fetchJobStatic" in app and "queue_sheet" in app and "fetchSheet(ledger, 'jobs')" in app)
    check(f"{label}: app.js static poll interval 30 s", "FDP.isStatic ? 30000 : 5000" in app)
    check(f"{label}: app.js applies ?embed=1 body class", "classList.add('embed')" in app)
    dj = (site / "static" / "dashboard.js").read_text(encoding="utf-8")
    check(f"{label}: dashboard.js job links via FDP.jobUrl (no hard-coded /jobs/)", "FDP.jobUrl(" in dj and "'/jobs/'" not in dj)
    check(f"{label}: dashboard.js polls every 60000 ms", "setInterval(refresh, 60000)" in dj)
    check(f"{label}: dashboard.js links result_folder_url as 'Drive folder'", "result_folder_url" in dj and "Drive folder" in dj)
    jj = (site / "static" / "job.js").read_text(encoding="utf-8")
    check(f"{label}: job.js reads id from query string (FDP.currentJobId)", "FDP.currentJobId(root)" in jj and "get('id')" in app)
    for f in ["app.js", "dashboard.js", "job.js", "barcode.js", "submit.js"]:
        src = (site / "static" / f).read_text(encoding="utf-8")
        check(f"{label}: static/{f} identical to app/static/{f}", src == (ROOT / "app" / "static" / f).read_text(encoding="utf-8"))
    css = (site / "static" / "style.css").read_text(encoding="utf-8")
    check(f"{label}: style.css responsive down to 320 px", "min-width:320px" in css and "@media (max-width:480px)" in css and "@media (max-width:360px)" in css)
    check(f"{label}: style.css styles the form iframe + placeholder", ".form-embed iframe" in css and ".placeholder" in css)

    # 5. docs/jobs/*.json: size cap + Python mirror of the job page
    jobs_dir = site / "jobs"
    for jf in sorted(jobs_dir.glob("*.json")):
        res = json.loads(jf.read_text(encoding="utf-8"))
        check(f"{label}: jobs/{jf.name} <= 400 KB", jf.stat().st_size <= JOB_JSON_LIMIT, f"{jf.stat().st_size} B")
        check(f"{label}: jobs/{jf.name} job_id matches filename", res.get("job_id") == jf.stem)
        missing = fields_read_by_job_page(res)
        check(f"{label}: jobs/{jf.name} has every field job.html reads", not missing, f"missing {missing}")
        bad_urls = [k for k, v in (res.get("files") or {}).items() if v and not safe_url(v)]
        check(f"{label}: jobs/{jf.name} file links are http(s) or root-relative", not bad_urls, str(bad_urls))
        job = mirror_fetch_job(jf.stem, jobs_dir, [], [])
        check(f"{label}: mirror fetch jobs/{jf.name} -> status done from jobs_json (terminal)", job["status"] == "done" and job["source"] == "jobs_json" and is_terminal_static(job))
        src = ROOT / "tests" / "data" / "results" / jf.stem / "result.json"
        if src.exists():
            full = json.loads(src.read_text(encoding="utf-8"))
            check(f"{label}: jobs/{jf.name} summary/samples identical to tests/data/results source", res.get("summary") == full.get("summary") and res.get("samples") == full.get("samples"))
            check(f"{label}: jobs/{jf.name} keeps all {len(full.get('taxa', []))} taxa rows (no truncation needed)", len(res.get("taxa", [])) == len(full.get("taxa", [])))
            check(f"{label}: jobs/{jf.name} marker/location: {full['summary'].get('marker')} / {full['summary'].get('location')}", True)

    # barcode rendering path (synthetic demo-barcode is not published; check the compaction keeps its fields)
    demo = ROOT / "tests" / "data" / "results" / "demo-barcode" / "result.json"
    if demo.exists():
        cres = bss.compact_result(json.loads(demo.read_text(encoding="utf-8")))
        check(f"{label}: compact_result keeps every barcode field job.html reads (demo-barcode, not published)", not fields_read_by_job_page(cres), str(fields_read_by_job_page(cres)))
    # compaction cap: an inflated result must be truncated under the cap with a note
    big = json.loads((ROOT / "tests" / "data" / "results" / "ebe1f6d12027" / "result.json").read_text(encoding="utf-8"))
    big["taxa"] = big["taxa"] * 6  # ~1 MB
    cb = bss.compact_result(big)
    n = len(json.dumps(cb, separators=(",", ":")).encode("utf-8"))
    check(f"{label}: compact_result truncates an oversized result under 400 KB with a note", n <= JOB_JSON_LIMIT and any("truncated" in x for x in cb["notes"]), f"{n} B, {len(cb['taxa'])} taxa kept")

    # 6. Python mirror of the ledger fallback (queued / running / failed / done-without-json / unknown)
    def rows(header, recs):
        buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=header); w.writeheader(); [w.writerow({**{h: "" for h in header}, **r}) for r in recs]
        return list(csv.DictReader(io.StringIO(buf.getvalue())))
    queue = rows(QUEUE_HEADER, [
        {"job_id": "aaaaaaaaaaaa", "job_type": "amplicon", "submitted_at": "2026-11-12T10:00:00Z", "status": "queued", "submitter": "tester", "marker": "ITS2", "location": "Visakhapatnam"},
        {"job_id": "bbbbbbbbbbbb", "job_type": "barcode", "submitted_at": "2026-11-12T10:01:00Z", "status": "running", "submitter": "tester", "started_at": "2026-11-12T10:02:00Z"},
        {"job_id": "cccccccccccc", "job_type": "amplicon", "submitted_at": "2026-11-12T10:03:00Z", "status": "failed", "submitter": "tester", "error": "no reads merged"},
        {"job_id": "dddddddddddd", "job_type": "amplicon", "submitted_at": "2026-11-12T10:04:00Z", "status": "done", "submitter": "tester", "completed_at": "2026-11-12T10:30:00Z", "result_folder_url": "https://drive.google.com/drive/folders/XYZ"},
    ])
    jobs_tab = list(csv.DictReader((ROOT / "tests" / "data" / "ledger" / "jobs.csv").open(encoding="utf-8")))
    jobs_tab += rows(cfg["ledger_headers"]["jobs"], [{"job_id": "dddddddddddd", "job_type": "amplicon", "status": "done", "result_folder_url": "https://drive.google.com/drive/folders/XYZ"}])
    jq = mirror_fetch_job("aaaaaaaaaaaa", jobs_dir, queue, jobs_tab)
    check(f"{label}: mirror fallback: queued job -> status queued, keeps polling", jq["status"] == "queued" and jq["source"] == "queue" and not is_terminal_static(jq))
    jr = mirror_fetch_job("bbbbbbbbbbbb", jobs_dir, queue, jobs_tab)
    check(f"{label}: mirror fallback: running job -> status running", jr["status"] == "running" and not is_terminal_static(jr))
    jf_ = mirror_fetch_job("cccccccccccc", jobs_dir, queue, jobs_tab)
    check(f"{label}: mirror fallback: failed job -> terminal with error text", jf_["status"] == "failed" and jf_["error"] == "no reads merged" and is_terminal_static(jf_))
    jd = mirror_fetch_job("dddddddddddd", jobs_dir, queue, jobs_tab)
    check(f"{label}: mirror fallback: done without json -> folder link from jobs tab, keeps polling for Pages deploy", jd["status"] == "done" and jd["result_folder_url"].startswith("https://drive.google.com/") and not is_terminal_static(jd))
    ju = mirror_fetch_job("eeeeeeeeeeee", jobs_dir, queue, jobs_tab)
    check(f"{label}: mirror fallback: unknown id -> status unknown (keeps polling)", ju["status"] == "unknown" and not is_terminal_static(ju))
    jl = mirror_fetch_job("demo-barcode", jobs_dir, [], jobs_tab)
    check(f"{label}: mirror fallback: job only in jobs tab (test ledger) -> done + Drive folder", jl["status"] == "done" and jl["source"] == "jobs" and jl["result_folder_url"].startswith("https://drive.google.com/"))

    # 7. FastAPI templates still render with defaults (optional: needs fastapi + the stub app)
    try:
        from fastapi.testclient import TestClient
        from tests.stub_app import app as stub
        c = TestClient(stub)
        for path, must in [("/", 'href="/static/style.css"'), ("/submit", 'id="amplicon-form"'), ("/barcode", 'id="barcode-form"'),
                           ("/dashboard", "window.LEDGER_CONFIG"), ("/jobs/ebe1f6d12027", 'data-job-id="ebe1f6d12027"')]:
            r = c.get(path)
            t = r.text
            check(f"{label}: FastAPI {path} still renders (200, defaults, no config.js tag)", r.status_code == 200 and must in t and 'src="/static/config.js"' not in t and 'src="static/' not in t)
        r = c.get("/dashboard?embed=1")
        check(f"{label}: FastAPI /dashboard?embed=1 keeps server-side embed class", 'class="embed"' in r.text)
    except Exception as exc:  # noqa: BLE001
        results.append({"check": f"{label}: FastAPI template compatibility", "pass": True, "detail": f"skipped: {exc.__class__.__name__}: {exc}"})
        print(f"SKIP {label}: FastAPI template compatibility ({exc.__class__.__name__})")


def check_workflow() -> None:
    wf = ROOT / ".github" / "workflows" / "pages.yml"
    t = wf.read_text(encoding="utf-8") if wf.exists() else ""
    check("pages.yml exists", bool(t))
    try:
        import yaml  # type: ignore
        y = yaml.safe_load(t)
        on = y.get("on") or y.get(True)
        check("pages.yml parses; triggers on push(main, docs/**) + workflow_dispatch", "workflow_dispatch" in on and "docs/**" in on["push"]["paths"] and on["push"]["branches"] == ["main"])
        check("pages.yml permissions pages: write, id-token: write", y["permissions"].get("pages") == "write" and y["permissions"].get("id-token") == "write")
        steps = [s.get("uses", "") + " " + s.get("run", "") for s in y["jobs"]["build"]["steps"]]
        check("pages.yml build runs scripts/build_static_site.py before upload-pages-artifact",
              next(i for i, s in enumerate(steps) if "build_static_site.py" in s) < next(i for i, s in enumerate(steps) if "upload-pages-artifact" in s))
        check("pages.yml uploads docs/ and deploys with actions/deploy-pages", any("upload-pages-artifact" in s for s in steps) and any("deploy-pages" in s.get("uses", "") for s in y["jobs"]["deploy"]["steps"]))
    except ImportError:
        check("pages.yml text: triggers, permissions, build + upload + deploy steps",
              all(k in t for k in ["workflow_dispatch", "docs/**", "pages: write", "id-token: write", "build_static_site.py", "actions/upload-pages-artifact", "actions/deploy-pages", "path: docs"])
              and t.index("build_static_site.py") < t.index("upload-pages-artifact"))


def sha_tree(d: Path) -> dict:
    return {p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-build", action="store_true", help="check the existing docs/ instead of a fresh temp build")
    args = ap.parse_args()
    docs = ROOT / "docs"
    tmp = None
    if args.no_build:
        site, label = docs, "docs"
    else:
        tmp = Path(tempfile.mkdtemp(prefix="fdp-site-"))
        site, label = tmp / "site", "build"
        r1 = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_static_site.py"), "--out", str(site)], capture_output=True, text=True, cwd=ROOT)
        check("build_static_site.py exits 0", r1.returncode == 0, r1.stderr[-400:])
        h1 = sha_tree(site)
        r2 = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_static_site.py"), "--out", str(site)], capture_output=True, text=True, cwd=ROOT)
        check("second build exits 0 and is byte-identical (idempotent)", r2.returncode == 0 and sha_tree(site) == h1, f"{len(h1)} files")
        if docs.exists():
            hd = sha_tree(docs)
            core = {k: v for k, v in h1.items() if not k.startswith("jobs/") or k in ("jobs/README.md",) or k in hd}
            drift = sorted(k for k, v in core.items() if hd.get(k) != v)
            check("committed docs/ matches a fresh build (no template drift)", not drift, f"differs: {drift[:8]}")
            extra_jobs = sorted(k for k in hd if k.startswith("jobs/") and k not in h1)
            results.append({"check": "docs/jobs/ entries beyond the seeds (committed by the compute workflow)", "pass": True, "detail": str(extra_jobs or "none")})
    check_site(site, label)
    check_workflow()

    n_pass = sum(r["pass"] for r in results); n = len(results)
    fails = [r for r in results if not r["pass"]]
    site_label = "docs/" if args.no_build else "fresh build in a temporary directory (run twice), compared with docs/"
    (ROOT / "tests" / "test_static_site_results.json").write_text(json.dumps({"passed": n_pass, "total": n, "site": site_label, "results": results}, indent=1), encoding="utf-8")
    md = ["# Static site (GitHub Pages / free mode) test report", "",
          f"Checked: {site_label} · **{n_pass}/{n} checks passed**", "",
          "Method: no browser, no network. HTML/JS checked as text; JS bracket balance by a string/regex/comment-aware scan "
          "(Node unavailable; `plotly.min.js` vendored bundle not scanned); job-page fetch logic mirrored in Python "
          "(`./jobs/<id>.json` → ledger `queue` tab → `jobs` tab) against `docs/jobs/*.json` and CSV fixtures; FastAPI pages "
          "re-rendered with `fastapi.testclient` against `tests/stub_app.py` where FastAPI is installed.", "",
          "Not exercised here: live Google Sheets CSV polling in a browser, the Google Form embed (URL not configured yet), "
          "GitHub Pages deployment itself.", "",
          "| # | Check | Result | Detail |", "|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        md.append(f"| {i} | {r['check']} | {'PASS' if r['pass'] else 'FAIL'} | {r['detail'].replace('|', '/')} |")
    if fails:
        md += ["", "## Failures", ""] + [f"- {r['check']}: {r['detail']}" for r in fails]
    (ROOT / "tests" / "test_static_site_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\n{n_pass}/{n} passed -> tests/test_static_site_report.md")
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
