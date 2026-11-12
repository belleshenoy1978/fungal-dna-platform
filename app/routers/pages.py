"""Dashboard track: HTML pages (landing, submit, barcode, dashboard, job result).

All pages are public and embeddable: no X-Frame-Options / frame-ancestors header is set and
no cookies are used, so Google Sites (belleshenoy.in) can embed them by URL.
The ledger location (Google Sheets CSV export) is read from platform_config.json at the repo
root, overridable with LEDGER_SPREADSHEET_ID (via app.config when present).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

_APP_DIR = Path(__file__).resolve().parents[1]          # .../app
_REPO_ROOT = _APP_DIR.parent
_TEMPLATE_DIR = _APP_DIR / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))   # == 'app/templates' relative to the repo root

_DEFAULT_CSV_TEMPLATE = "https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?tqx=out:csv&sheet={sheet}"


def _load_platform_config() -> dict:
    p = Path(os.environ.get("PLATFORM_CONFIG_JSON", _REPO_ROOT / "platform_config.json"))
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _spreadsheet_id(cfg: dict) -> str:
    try:  # the Amplicon track's config module wins when it exists
        from app import config as app_config  # type: ignore
        sid = getattr(app_config, "LEDGER_SPREADSHEET_ID", None)
        if sid:
            return str(sid)
    except Exception:  # noqa: BLE001 - optional dependency between tracks
        pass
    return os.environ.get("LEDGER_SPREADSHEET_ID") or cfg.get("ledger", {}).get("spreadsheet_id", "")


def page_context(request: Request, active: str = "", **extra) -> dict:
    cfg = _load_platform_config()
    sid = _spreadsheet_id(cfg)
    csv_template = cfg.get("ledger", {}).get("csv_export_template", _DEFAULT_CSV_TEMPLATE)
    ledger_url = cfg.get("ledger", {}).get("url") or (f"https://docs.google.com/spreadsheets/d/{sid}/edit" if sid else "")
    ctx = {
        "request": request,
        "active": active,
        "spreadsheet_id": sid,
        "csv_template": csv_template,
        "ledger_config": {"spreadsheet_id": sid, "csv_template": csv_template},
        "ledger_url": ledger_url,
        "drive_links": cfg.get("drive_links", {}),
        "site_url": cfg.get("website", {}).get("url", "https://www.belleshenoy.in"),
    }
    ctx.update(extra)
    return ctx


def _render(name: str, request: Request, **ctx) -> HTMLResponse:
    # Starlette >= 0.29 signature: TemplateResponse(request, name, context)
    resp = templates.TemplateResponse(request, name, page_context(request, **ctx))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request):
    return _render("index.html", request, active="home")


@router.get("/submit", response_class=HTMLResponse, include_in_schema=False)
async def submit(request: Request):
    return _render("submit.html", request, active="submit")


@router.get("/barcode", response_class=HTMLResponse, include_in_schema=False)
async def barcode(request: Request):
    return _render("barcode.html", request, active="barcode")


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(request: Request, embed: int = 0):
    """Live dashboard. `?embed=1` hides the site header (for a tight Google Sites embed);
    `?source=<url template with {sheet}>` points the JS at an alternative CSV source (used by tests)."""
    return _render("dashboard.html", request, active="dashboard", embed=bool(embed))


@router.get("/jobs/{job_id}", response_class=HTMLResponse, include_in_schema=False)
async def job_page(request: Request, job_id: str):
    safe = "".join(ch for ch in job_id if ch.isalnum() or ch in "-_")[:64]
    return _render("job.html", request, active="", job_id=safe)
