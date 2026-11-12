#!/usr/bin/env python3
"""
Render the Fungal DNA Analysis Platform pages as a static site for GitHub Pages ("free mode").

    python scripts/build_static_site.py                 # -> docs/
    python scripts/build_static_site.py --out /tmp/site # elsewhere (tests)
    python scripts/build_static_site.py compact-job tests/data/results/<id>/result.json --out docs/jobs/<id>.json

What it produces (all paths relative to --out, default docs/):
  index.html        landing page: live KPI strip (ledger CSV), cards -> submit.html / dashboard.html
  dashboard.html    the live dashboard (identical behaviour to /dashboard: polls the ledger CSV export every 60 s;
                    job links point to job.html?id=<job_id>; "Drive folder" links to result_folder_url)
  job.html          result page; reads ?id=<job_id>, fetches ./jobs/<id>.json same-origin, falls back to the
                    ledger `queue` tab (status, 30 s auto-refresh) and `jobs` tab (result_folder_url)
  submit.html       explains both analysis types and embeds the Google Form (free_mode.google_form_embed_url)
  static/           copy of app/static/* plus the generated static/config.js (window.PLATFORM_CONFIG)
  jobs/<id>.json    compact result JSON per job (seeded from tests/data/results/*; the compute workflow adds more)
  .nojekyll         so GitHub Pages serves files/folders starting with "_" or "." verbatim

The templates are the SAME Jinja templates the FastAPI app serves (app/templates); every static-mode
variable has a `| default(...)` so the server pages are unchanged. Configuration reaches the browser via
static/config.js, never via server-side variables, so the site is a plain directory of files.

Idempotent and offline: no network access, no timestamps; running it twice yields byte-identical output.
Only needs Python >= 3.9 and jinja2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
except ImportError as exc:  # pragma: no cover
    sys.exit(f"jinja2 is required (pip install jinja2): {exc}")

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO_ROOT / "app" / "templates"
STATIC_SRC = REPO_ROOT / "app" / "static"
DEFAULT_OUT = REPO_ROOT / "docs"
DEFAULT_CONFIG = REPO_ROOT / "platform_config.json"
SEED_RESULTS = REPO_ROOT / "tests" / "data" / "results"
# Real test results rebuilt from earlier project data (tests/make_test_data.py). "demo-barcode" is
# SYNTHETIC (invented hits) and is deliberately NOT published on the public site.
SEED_JOB_IDS = ("ebe1f6d12027", "cabb8aa86f74")
JOB_JSON_LIMIT = 400 * 1024  # bytes
DEFAULT_CSV_TEMPLATE = "https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?tqx=out:csv&sheet={sheet}"

PAGES = (  # (output file, template, active nav item)
    ("index.html", "index.html", "home"),
    ("dashboard.html", "dashboard.html", "dashboard"),
    ("job.html", "job.html", ""),
    ("submit.html", "submit_static.html", "submit"),
)


# ----------------------------------------------------------------------------- config

def is_placeholder(value) -> bool:
    """True for empty values and the '<...>' placeholders left by platform_config.json."""
    v = str(value or "").strip()
    return (not v) or v.startswith("<") or "<github-user>" in v or "<filled" in v


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def site_config(cfg: dict) -> dict:
    """The dict written to static/config.js (window.PLATFORM_CONFIG). Public values only."""
    ledger = cfg.get("ledger", {})
    free = cfg.get("free_mode", {})
    sid = ledger.get("spreadsheet_id", "")
    pages_url = "" if is_placeholder(free.get("github_pages_url")) else str(free["github_pages_url"]).rstrip("/") + "/"
    repo = "" if is_placeholder(free.get("github_repo")) else str(free["github_repo"]).strip("/")
    form_url = "" if is_placeholder(free.get("google_form_url")) else free["google_form_url"]
    form_embed = "" if is_placeholder(free.get("google_form_embed_url")) else free["google_form_embed_url"]
    return {
        "mode": "static",
        "config_source": "platform_config.json (scripts/build_static_site.py)",
        "ledger": {
            "spreadsheet_id": sid,
            "csv_template": ledger.get("csv_export_template", DEFAULT_CSV_TEMPLATE),
            "url": ledger.get("url") or (f"https://docs.google.com/spreadsheets/d/{sid}/edit" if sid else ""),
            "gids": ledger.get("gids", {}),
        },
        "queue_sheet": free.get("queue_sheet", "queue"),
        "queue_status_values": free.get("queue_status_values", ["queued", "running", "done", "failed"]),
        "pages_base_url": pages_url,
        "github_repo": repo,
        "github_repo_url": f"https://github.com/{repo}" if repo else "",
        "google_form_url": form_url,
        "google_form_embed_url": form_embed,
        "form_configured": bool(form_embed),
        "drive_links": cfg.get("drive_links", {}),
        "jobs_json_path": "./jobs/",
        "job_page": "job.html?id={job_id}",
        "poll_seconds": {"dashboard": 60, "job": 30},
    }


def render_config_js(site: dict) -> str:
    body = json.dumps(site, indent=2, ensure_ascii=False, sort_keys=True)
    return ("/* Generated by scripts/build_static_site.py from platform_config.json - do not edit by hand.\n"
            "   Read by app.js as window.PLATFORM_CONFIG; absent under the FastAPI deployment. */\n"
            f"window.PLATFORM_CONFIG = {body};\n")


# ----------------------------------------------------------------------------- rendering

def jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,  # catch any variable the static context forgets
        keep_trailing_newline=True,
    )
    return env


def page_context(cfg: dict, site: dict, active: str) -> dict:
    ledger = site["ledger"]
    return {
        "static_mode": True,
        "static_prefix": "static",
        "url_home": "index.html",
        "url_submit": "submit.html",
        "url_barcode": "submit.html#barcode",
        "url_dashboard": "dashboard.html",
        "active": active,
        "embed": False,  # handled client-side (?embed=1) by app.js
        "job_id": "",    # job.js reads ?id=
        "jobs_json_path": site["jobs_json_path"],
        "spreadsheet_id": ledger["spreadsheet_id"],
        "csv_template": ledger["csv_template"],
        "ledger_config": {"spreadsheet_id": ledger["spreadsheet_id"], "csv_template": ledger["csv_template"]},
        "ledger_url": ledger["url"],
        "drive_links": site["drive_links"],
        "site_url": cfg.get("website", {}).get("url", "https://www.belleshenoy.in"),
        "github_repo_url": site["github_repo_url"],
        "google_form_url": site["google_form_url"] or site["google_form_embed_url"],
        "google_form_embed_url": site["google_form_embed_url"],
        "form_configured": site["form_configured"],
        "max_read_pairs_note": "a few million read pairs per job (about 6 h of runner time)",
    }


def _write_if_changed(path: Path, data: bytes) -> bool:
    """Write only when content differs (keeps mtimes stable; makes the build idempotent)."""
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


# ----------------------------------------------------------------------------- job json

def compact_result(result: dict, limit: int = JOB_JSON_LIMIT) -> dict:
    """Return a compact copy of a pipeline result.json for docs/jobs/<id>.json (<= `limit` bytes serialised).

    Keeps every field the job page reads (job_id, job_type, status, timestamps, params, summary, samples,
    taxa, composition, barcoding, files, notes, result_folder_url). If the serialised size still exceeds the
    limit, the taxa list is truncated to the top-N ASVs by reads_total and a note is appended; raw per-hit
    lists in barcoding records are trimmed last.
    """
    keep = ("job_id", "job_type", "status", "submitted_at", "completed_at", "params", "summary", "samples",
            "taxa", "composition", "barcoding", "files", "notes", "result_folder_url", "result_json_url", "error")
    out = {k: result[k] for k in keep if k in result}
    out.setdefault("notes", [])

    def size(d: dict) -> int:
        return len(json.dumps(d, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))

    if size(out) <= limit:
        return out
    taxa = list(out.get("taxa") or [])
    taxa.sort(key=lambda t: -(float(t.get("reads_total") or 0)))
    n_full = len(taxa)
    n = n_full
    while n > 25 and size({**out, "taxa": taxa[:n]}) > limit:
        n = int(n * 0.8)
    out["taxa"] = taxa[:n]
    if n < n_full:
        out["notes"] = list(out["notes"]) + [f"Web copy truncated to the top {n} of {n_full} ASVs by reads (size cap {limit // 1024} KB); the full table is asv_table_with_taxonomy.tsv in the Drive folder."]
    if size(out) > limit and out.get("barcoding"):
        for h in out["barcoding"]:
            h.pop("hits", None)
        out["notes"] = list(out["notes"]) + ["Web copy omits the per-query top-10 hit lists (size cap); see hits.tsv in the Drive folder."]
    return out


def write_job_json(result: dict, dest: Path, limit: int = JOB_JSON_LIMIT) -> tuple[int, bool]:
    data = json.dumps(compact_result(result, limit), separators=(",", ":"), ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(data) > limit:
        raise ValueError(f"{dest.name}: {len(data)} bytes exceeds the {limit} byte cap even after compaction")
    changed = _write_if_changed(dest, data)
    return len(data), changed


def seed_jobs(out_dir: Path, limit: int = JOB_JSON_LIMIT) -> list[dict]:
    rows = []
    for jid in SEED_JOB_IDS:
        src = SEED_RESULTS / jid / "result.json"
        if not src.exists():
            rows.append({"job_id": jid, "status": "missing source", "bytes": 0})
            continue
        result = json.loads(src.read_text(encoding="utf-8"))
        n, changed = write_job_json(result, out_dir / "jobs" / f"{jid}.json", limit)
        rows.append({"job_id": jid, "status": "written" if changed else "unchanged", "bytes": n, "source_bytes": src.stat().st_size})
    return rows


# ----------------------------------------------------------------------------- build

def copy_static(out_dir: Path) -> int:
    dest = out_dir / "static"
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for src in sorted(STATIC_SRC.iterdir()):
        if src.is_file():
            _write_if_changed(dest / src.name, src.read_bytes())
            n += 1
    return n


def build(out_dir: Path, config_path: Path = DEFAULT_CONFIG, seed: bool = True, verbose: bool = True) -> dict:
    cfg = load_config(config_path)
    site = site_config(cfg)
    env = jinja_env()
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"out": str(out_dir), "pages": [], "static_files": 0, "jobs": [], "form_configured": site["form_configured"]}

    for out_name, tpl_name, active in PAGES:
        html = env.get_template(tpl_name).render(**page_context(cfg, site, active))
        _write_if_changed(out_dir / out_name, html.encode("utf-8"))
        report["pages"].append({"file": out_name, "template": tpl_name, "bytes": len(html.encode("utf-8"))})

    report["static_files"] = copy_static(out_dir)
    _write_if_changed(out_dir / "static" / "config.js", render_config_js(site).encode("utf-8"))
    _write_if_changed(out_dir / ".nojekyll", b"")
    (out_dir / "jobs").mkdir(exist_ok=True)
    _write_if_changed(out_dir / "jobs" / "README.md",
                      b"Compact result JSON per job (`<job_id>.json`, <= 400 KB), read same-origin by `../job.html?id=<job_id>`.\n"
                      b"Seeded by `scripts/build_static_site.py`; the GitHub Actions compute workflow commits one file per finished job.\n")
    if seed:
        report["jobs"] = seed_jobs(out_dir)

    report["sha256"] = {p.relative_to(out_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()[:16]
                        for p in sorted(out_dir.rglob("*")) if p.is_file() and p.name != "plotly.min.js"}
    if verbose:
        print(f"static site -> {out_dir}")
        for p in report["pages"]:
            print(f"  {p['file']:<16} {p['bytes']:>7} B  (from {p['template']})")
        print(f"  static/          {report['static_files']} files + config.js")
        for j in report["jobs"]:
            print(f"  jobs/{j['job_id']}.json  {j['bytes']} B  {j['status']}")
        print("  form embed:", "configured" if site["form_configured"] else "NOT configured (placeholder shown on submit.html)")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build", help="render the site (default)")
    b.add_argument("--out", type=Path, default=DEFAULT_OUT)
    b.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    b.add_argument("--no-seed", action="store_true", help="do not (re)write docs/jobs/ from tests/data/results")
    b.add_argument("--json", type=Path, help="write the build report here")
    c = sub.add_parser("compact-job", help="compact one result.json into docs/jobs/<id>.json (used by the compute workflow)")
    c.add_argument("result_json", type=Path)
    c.add_argument("--out", type=Path, help="destination file (default docs/jobs/<job_id>.json)")
    c.add_argument("--limit", type=int, default=JOB_JSON_LIMIT)
    # allow bare `build_static_site.py --out X`
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0].startswith("-"):
        argv = ["build", *argv]
    args = ap.parse_args(argv)

    if args.cmd == "compact-job":
        result = json.loads(Path(args.result_json).read_text(encoding="utf-8"))
        dest = args.out or (DEFAULT_OUT / "jobs" / f"{result['job_id']}.json")
        n, changed = write_job_json(result, dest, args.limit)
        print(f"{dest} {n} B {'written' if changed else 'unchanged'}")
        return 0

    report = build(args.out, args.config, seed=not args.no_seed)
    if args.json:
        args.json.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
