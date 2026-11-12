"""
Central configuration for the fungal-dna-platform backend.

Everything is read from environment variables, with defaults for the Google
Drive / Sheets identifiers taken from platform_config.json in the repository
root. The module is import-safe without any credentials: LOCAL_MODE becomes
true automatically when no service-account JSON is configured, and the storage
layer then writes to DATA_DIR instead of Drive/Sheets.

Environment variables
---------------------
DRIVE_RESULTS_FOLDER_ID, DRIVE_UPLOADS_FOLDER_ID, DRIVE_REFERENCE_FOLDER_ID
LEDGER_SPREADSHEET_ID
GOOGLE_SERVICE_ACCOUNT_JSON   path to a service-account key file OR the inline JSON
VSEARCH_BIN                   default 'vsearch' (resolved on PATH)
UNITE_SINTAX_DB               default <DATA_DIR>/ref/unite_sintax.fasta.gz
DATA_DIR                      default /data (Cloud Run: mount or ephemeral disk)
LOCAL_MODE                    '1'/'true' forces local mode; auto-true without credentials
PIPELINE_THREADS              default 2
"""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLATFORM_CONFIG_PATH = Path(os.environ.get("PLATFORM_CONFIG", REPO_ROOT / "platform_config.json"))


def _load_platform_config() -> dict:
    try:
        with open(PLATFORM_CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


PLATFORM = _load_platform_config()
_drive = PLATFORM.get("drive", {})
_ledger = PLATFORM.get("ledger", {})

LEDGER_HEADERS: dict[str, list[str]] = PLATFORM.get("ledger_headers", {})
DRIVE_LINKS: dict[str, str] = PLATFORM.get("drive_links", {})
LEDGER_URL: str = _ledger.get("url", "")


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "on"}


DRIVE_RESULTS_FOLDER_ID = os.environ.get("DRIVE_RESULTS_FOLDER_ID", _drive.get("results", ""))
DRIVE_UPLOADS_FOLDER_ID = os.environ.get("DRIVE_UPLOADS_FOLDER_ID", _drive.get("uploads", ""))
DRIVE_REFERENCE_FOLDER_ID = os.environ.get("DRIVE_REFERENCE_FOLDER_ID", _drive.get("reference", ""))
LEDGER_SPREADSHEET_ID = os.environ.get("LEDGER_SPREADSHEET_ID", _ledger.get("spreadsheet_id", ""))

# Either a service-account key or an OAuth "authorized_user" token (scripts/authorize_google.py);
# GOOGLE_CREDENTIALS_JSON is an alias accepted for the latter.
GOOGLE_SERVICE_ACCOUNT_JSON = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
                               or os.environ.get("GOOGLE_CREDENTIALS_JSON", "")).strip()

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
VSEARCH_BIN = os.environ.get("VSEARCH_BIN", "vsearch")
UNITE_SINTAX_DB = os.environ.get("UNITE_SINTAX_DB", str(DATA_DIR / "ref" / "unite_sintax.fasta.gz"))
PIPELINE_THREADS = int(os.environ.get("PIPELINE_THREADS", "2"))

# LOCAL_MODE: explicit env wins; otherwise true whenever no service account is configured.
if "LOCAL_MODE" in os.environ:
    LOCAL_MODE = _truthy(os.environ["LOCAL_MODE"])
else:
    LOCAL_MODE = not bool(GOOGLE_SERVICE_ACCOUNT_JSON)

# Public base URL used to build file links in LOCAL_MODE (Cloud Run sets its own).
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

JOBS_DB_PATH = Path(os.environ.get("JOBS_DB_PATH", DATA_DIR / "jobs.sqlite3"))
JOBS_DIR = DATA_DIR / "jobs"

CAVEAT_FOOTER = (
    "Results are automated and provisional; no negative controls are enforced; "
    "verify taxonomic assignments before publication"
)
CITATIONS = [
    "cutadapt: Martin M. (2011) EMBnet.journal 17(1):10-12",
    "VSEARCH: Rognes T. et al. (2016) PeerJ 4:e2584",
    "UNITE: Abarenkov K. et al. (2024) Nucleic Acids Research 52(D1):D791-D797",
    "SINTAX: Edgar R.C. (2016) bioRxiv 074161",
    "Plotly.js (MIT licence)",
]


def service_account_info() -> dict | None:
    """Return the parsed service-account dict, or None when not configured."""
    raw = GOOGLE_SERVICE_ACCOUNT_JSON
    if not raw:
        return None
    if raw.lstrip().startswith("{"):
        return json.loads(raw)
    with open(raw, encoding="utf-8") as fh:
        return json.load(fh)


def ensure_dirs() -> None:
    for p in (DATA_DIR, JOBS_DIR, DATA_DIR / "results", DATA_DIR / "ledger", DATA_DIR / "ref"):
        p.mkdir(parents=True, exist_ok=True)


def describe() -> dict:
    """Non-secret configuration summary for /health."""
    return {
        "local_mode": LOCAL_MODE,
        "data_dir": str(DATA_DIR),
        "vsearch_bin": VSEARCH_BIN,
        "unite_sintax_db": UNITE_SINTAX_DB,
        "unite_db_present": os.path.exists(UNITE_SINTAX_DB),
        "drive_results_folder_id": DRIVE_RESULTS_FOLDER_ID,
        "ledger_spreadsheet_id": LEDGER_SPREADSHEET_ID,
        "service_account_configured": bool(GOOGLE_SERVICE_ACCOUNT_JSON),
    }
