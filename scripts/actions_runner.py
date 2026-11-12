#!/usr/bin/env python
"""
Free-mode compute runner for the Fungal DNA Analysis Platform.

Runs inside GitHub Actions (.github/workflows/run-job.yml) but has no GitHub
dependency itself: it is a plain CLI that

  1. reads the `queue` tab of the public ledger spreadsheet
     (platform_config.json -> ledger.spreadsheet_id, header = ledger_headers.queue),
  2. picks the requested job_id, or every row whose status is `queued`
     (oldest first, at most --max-jobs per run; `running` rows older than
     --requeue-stale-hours are taken as well - they were left behind by a
     cancelled run),
  3. for each job: marks it `running`, downloads the submitter's Drive files
     (uploaded through the Google Form) into DATA_DIR/jobs/<job_id>/input/,
     builds the same params dict the FastAPI routers build, calls the EXISTING
     pipeline runner (app.pipeline.amplicon.run_job / app.pipeline.barcoding.run_job)
     with the EXISTING app.storage.Storage (uploads to Drive results/<job_id>/,
     appends the ledger sheets), writes result.json like app.jobs' worker does,
     stores a compact copy under docs/jobs/<job_id>.json for GitHub Pages and
     updates the queue row (done/failed, timestamps, result URLs, error text).

A failure in one job never stops the others; the process exits 0 unless the
queue itself could not be read (exit 2) or a selected job_id does not exist
(exit 3, after waiting for the Apps Script append to become visible).

Google credentials: GOOGLE_SERVICE_ACCOUNT_JSON (OAuth 'authorized_user' token
from scripts/authorize_google.py, or a service-account key) exactly as for the
FastAPI app; the credential is used only through app.storage and is never logged.

Local test mode (no Google account needed):
    python scripts/actions_runner.py --local-queue queue.csv --local-files
      --local-queue   read/write the queue from a CSV file with the queue header
      --local-files   interpret file_ids as local paths (copied into input/)
Combined with LOCAL_MODE (no GOOGLE_SERVICE_ACCOUNT_JSON) the Storage writes
results and ledger CSVs under DATA_DIR, so the whole runner can be exercised
offline (tests/test_actions_runner.py).

Environment (all optional, defaults from app.config / platform_config.json):
    DATA_DIR, VSEARCH_BIN, UNITE_SINTAX_DB, PIPELINE_THREADS, PUBLIC_BASE_URL,
    LEDGER_SPREADSHEET_ID, DRIVE_RESULTS_FOLDER_ID, DOCS_JOBS_DIR (default
    <repo>/docs/jobs), MAX_JOBS_PER_RUN (default 5).
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import config  # noqa: E402  (needs sys.path above)

log = logging.getLogger("actions_runner")

QUEUE_SHEET = (config.PLATFORM.get("free_mode", {}) or {}).get("queue_sheet", "queue")
QUEUE_HEADERS: list[str] = config.LEDGER_HEADERS.get("queue") or [
    "job_id", "job_type", "submitted_at", "status", "submitter", "submitter_email", "marker", "fwd_primer",
    "rev_primer", "pairing", "location", "lat", "lon", "sequences_text", "file_ids", "file_names",
    "form_response_id", "started_at", "completed_at", "result_folder_url", "result_page_url", "error",
]
STATUS_QUEUED, STATUS_RUNNING, STATUS_DONE, STATUS_FAILED = "queued", "running", "done", "failed"
COMPACT_MAX_BYTES = 400 * 1024
COMPACT_TOP_TAXA = 500
SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
# Google Forms renames uploads to "<original name> - <respondent name>"; the
# original extension sits before the " - ".
FORM_SUFFIX_RE = re.compile(
    r"^(?P<base>.+?\.(?:fastq|fq|fasta|fa|fna|fas|seq|txt|ab1|abi)(?:\.gz)?)\s+-\s+.+$", re.IGNORECASE)
SEQ_CHARS_RE = re.compile(r"^[ACGTRYKMSWBDHVNacgtrykmswbdhvn\s]+$")


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _col_letter(n: int) -> str:
    """1-based column index -> spreadsheet column letters."""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _parse_ts(s: str) -> datetime | None:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            d = datetime.strptime(s, fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- queue backends
class QueueRow(dict):
    """A queue row as a dict (QUEUE_HEADERS keys) plus its 1-based sheet row number."""

    row_number: int


class CsvQueue:
    """Queue stored in a local CSV file (test mode)."""

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)

    def _read_all(self) -> tuple[list[str], list[list[str]]]:
        with open(self.path, encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))
        if not rows:
            return list(QUEUE_HEADERS), []
        return rows[0], rows[1:]

    def rows(self) -> list[QueueRow]:
        header, body = self._read_all()
        if header != QUEUE_HEADERS:
            raise RuntimeError(f"queue CSV header does not match platform_config ledger_headers.queue: {header}")
        out = []
        for i, r in enumerate(body, start=2):
            r = (r + [""] * len(header))[: len(header)]
            q = QueueRow(zip(header, r))
            q.row_number = i
            out.append(q)
        return out

    def update(self, row: QueueRow, **fields) -> None:
        header, body = self._read_all()
        idx = row.row_number - 2
        while len(body) <= idx:
            body.append([""] * len(header))
        row.update(fields)
        body[idx] = [str(row.get(h, "") if row.get(h) is not None else "") for h in header]
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(body)
        os.replace(tmp, self.path)


class SheetsQueue:
    """Queue stored in the `queue` tab of the ledger spreadsheet (Sheets API v4)."""

    def __init__(self, sheets_service, spreadsheet_id: str, sheet: str = QUEUE_SHEET) -> None:
        if not spreadsheet_id:
            raise RuntimeError("LEDGER_SPREADSHEET_ID is empty")
        self.svc = sheets_service
        self.sid = spreadsheet_id
        self.sheet = sheet
        self.last_col = _col_letter(len(QUEUE_HEADERS))

    def _get(self, rng: str) -> list[list[str]]:
        resp = self.svc.spreadsheets().values().get(spreadsheetId=self.sid, range=rng).execute()
        return resp.get("values", [])

    def rows(self) -> list[QueueRow]:
        values = self._get(f"'{self.sheet}'!A1:{self.last_col}")
        if not values:
            log.warning("queue tab '%s' is empty; writing the header row", self.sheet)
            self.svc.spreadsheets().values().update(
                spreadsheetId=self.sid, range=f"'{self.sheet}'!A1", valueInputOption="RAW",
                body={"values": [QUEUE_HEADERS]}).execute()
            return []
        header = [h.strip() for h in values[0]]
        if header != QUEUE_HEADERS:
            raise RuntimeError(
                f"queue tab header mismatch.\n  sheet : {header}\n  config: {QUEUE_HEADERS}\n"
                "Fix the header row in the spreadsheet (apps_script/Code.gs setup() writes it).")
        out = []
        for i, r in enumerate(values[1:], start=2):
            r = (list(r) + [""] * len(header))[: len(header)]
            if not any(c.strip() for c in r):
                continue
            q = QueueRow(zip(header, [str(c) for c in r]))
            q.row_number = i
            out.append(q)
        return out

    def update(self, row: QueueRow, **fields) -> None:
        row.update(fields)
        vals = [["" if row.get(h) is None else str(row.get(h, "")) for h in QUEUE_HEADERS]]
        rng = f"'{self.sheet}'!A{row.row_number}:{self.last_col}{row.row_number}"
        # Guard against the row having moved (should not happen: Apps Script only appends).
        current = self._get(f"'{self.sheet}'!A{row.row_number}")
        if current and current[0] and current[0][0] != row["job_id"]:
            raise RuntimeError(f"queue row {row.row_number} now holds {current[0][0]!r}, expected {row['job_id']!r}")
        self.svc.spreadsheets().values().update(
            spreadsheetId=self.sid, range=rng, valueInputOption="RAW", body={"values": vals}).execute()


# --------------------------------------------------------------------------- input files
def safe_filename(name: str) -> str:
    """Original upload name: strip the Google Forms ' - Respondent' suffix, then the
    same sanitising the FastAPI amplicon router applies."""
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    m = FORM_SUFFIX_RE.match(base)
    if m:
        base = m.group("base")
    base = SAFE_NAME.sub("_", base).strip("._")
    return base or "upload.bin"


def unique_path(directory: Path, name: str) -> Path:
    target = directory / name
    k = 1
    stem, suffix = name, ""
    m = re.match(r"^(.*?)((?:\.[A-Za-z0-9]{1,6}){1,2})$", name)
    if m:
        stem, suffix = m.group(1), m.group(2)
    while target.exists():
        k += 1
        target = directory / f"{stem}_{k}{suffix}"
    return target


def split_list(s: str) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def download_drive_file(drive, file_id: str, dest: Path) -> int:
    """files().get_media -> dest; verify the byte count against files().get(size)."""
    from googleapiclient.http import MediaIoBaseDownload

    meta = drive.files().get(fileId=file_id, fields="id,name,size,mimeType", supportsAllDrives=True).execute()
    expected = int(meta.get("size") or 0)
    req = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(dest, "wb") as fh:
        dl = MediaIoBaseDownload(fh, req, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk(num_retries=3)
    got = dest.stat().st_size
    if expected and got != expected:
        raise RuntimeError(f"Drive download of {meta.get('name')} ({file_id}) is {got} bytes, expected {expected}")
    return got


def stage_inputs(row: QueueRow, input_dir: Path, drive, local_files: bool) -> list[str]:
    """Copy/download every file of the job into input_dir; return the saved names."""
    input_dir.mkdir(parents=True, exist_ok=True)
    ids = split_list(row.get("file_ids", ""))
    names = split_list(row.get("file_names", ""))
    saved: list[str] = []
    for i, fid in enumerate(ids):
        raw_name = names[i] if i < len(names) and names[i] else None
        if local_files:
            src = Path(fid)
            if not src.is_file():
                raise FileNotFoundError(f"local input file not found: {src}")
            dest = unique_path(input_dir, safe_filename(raw_name or src.name))
            shutil.copy2(src, dest)
        else:
            if drive is None:
                raise RuntimeError("Drive client not available (no Google credentials) but file_ids are set")
            if raw_name is None:
                raw_name = drive.files().get(fileId=fid, fields="name", supportsAllDrives=True).execute().get("name", fid)
            dest = unique_path(input_dir, safe_filename(raw_name))
            n = download_drive_file(drive, fid, dest)
            log.info("downloaded %s (%d bytes)", dest.name, n)
        saved.append(dest.name)
    return saved


def write_pasted_fasta(text: str, input_dir: Path) -> Path | None:
    """sequences_text -> input/pasted.fasta (bare sequences get a FASTA header)."""
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return None
    if not text.lstrip().startswith(">"):
        # one or more bare sequences separated by blank lines / newlines
        chunks = [c for c in re.split(r"\n\s*\n", text) if c.strip()]
        recs = []
        for k, chunk in enumerate(chunks, start=1):
            seq = "".join(chunk.split())
            if not SEQ_CHARS_RE.match(seq):
                # not a plain sequence: keep it as one record anyway, the pipeline will reject it with a note
                pass
            recs.append(f">pasted_{k}\n{seq}\n")
        text = "".join(recs)
    else:
        text += "\n"
    input_dir.mkdir(parents=True, exist_ok=True)
    p = input_dir / "pasted.fasta"
    p.write_text(text, encoding="utf-8")
    return p


# --------------------------------------------------------------------------- params
def _float_or_none(v) -> float | None:
    try:
        s = str(v).strip()
        return float(s) if s else None
    except (TypeError, ValueError):
        return None


def build_params(row: QueueRow, job_dir: Path, saved: list[str]) -> dict:
    """Mirror app/routers/amplicon.py and app/routers/barcode.py param dicts."""
    jt = (row.get("job_type") or "").strip().lower()
    threads = config.PIPELINE_THREADS
    if jt == "amplicon":
        pairing = (row.get("pairing") or "paired").strip().lower()
        return {
            "marker": (row.get("marker") or "ITS1").strip(),
            "fwd_primer": (row.get("fwd_primer") or "").strip(),
            "rev_primer": (row.get("rev_primer") or "").strip(),
            "paired": pairing not in {"single", "single-end", "se", "unpaired", "false", "0"},
            "location": (row.get("location") or "").strip(),
            "lat": (row.get("lat") or "").strip() or None,
            "lon": (row.get("lon") or "").strip() or None,
            "submitter": (row.get("submitter") or "").strip(),
            "substrate": "",
            "threads": threads,
        }
    if jt == "barcode":
        input_dir = job_dir / "input"
        return {
            "submitter": (row.get("submitter") or "").strip()[:200],
            "marker": ((row.get("marker") or "ITS").strip() or "ITS")[:20],
            "location": (row.get("location") or "").strip()[:200] or None,
            "lat": _float_or_none(row.get("lat")),
            "lon": _float_or_none(row.get("lon")),
            "input_dir": str(input_dir),
            "input_files": [str(input_dir / n) for n in saved],
            # sequences_text is materialised as input/pasted.fasta by stage_job(); passing it here as
            # well would make barcoding.run_job read every pasted record twice.
            "sequences": "",
            "submitted_at": (row.get("submitted_at") or "").strip() or utcnow(),
            "threads": threads,
        }
    raise ValueError(f"unknown job_type {row.get('job_type')!r} (expected amplicon | barcode)")


def register_in_jobs_db(job_id: str, job_type: str, params: dict, submitted_at: str) -> None:
    """Insert the job into the SQLite table app.jobs uses, so amplicon.run_job's
    jobs.get_job(job_id) lookup returns the real submission time."""
    try:
        from app import jobs

        with jobs._db_lock, jobs._connect() as con:  # noqa: SLF001 - same process, same contract
            con.execute(
                "INSERT OR REPLACE INTO jobs(job_id, job_type, status, params, submitted_at, started_at) "
                "VALUES (?,?,?,?,?,?)",
                (job_id, job_type, STATUS_RUNNING, json.dumps(params, default=str), submitted_at, utcnow()))
    except Exception as exc:  # noqa: BLE001 - cosmetic only
        log.warning("could not register job %s in the local jobs db: %s", job_id, exc)


# --------------------------------------------------------------------------- results
def compact_result(result: dict) -> dict:
    """Copy of result.json small enough to live in the repo (<= COMPACT_MAX_BYTES)."""
    r = json.loads(json.dumps(result, default=str))
    taxa = r.get("taxa") or []
    if len(taxa) > COMPACT_TOP_TAXA:
        r["taxa"] = taxa[:COMPACT_TOP_TAXA]
        r.setdefault("notes", []).append(f"compact copy: taxa truncated to the top {COMPACT_TOP_TAXA} ASVs")
    if len(json.dumps(r, default=str).encode("utf-8")) > COMPACT_MAX_BYTES:
        for t in r.get("taxa") or []:
            t.pop("per_sample", None)
        r.setdefault("notes", []).append("compact copy: per-sample ASV counts dropped (see asv_table.tsv in the result folder)")
    if len(json.dumps(r, default=str).encode("utf-8")) > COMPACT_MAX_BYTES:
        for b in r.get("barcoding") or []:
            if isinstance(b.get("hits"), list):
                b["hits"] = b["hits"][:3]
        r.setdefault("notes", []).append("compact copy: barcoding hit lists truncated to the top 3 (see hits.tsv)")
    return r


def write_compact_copy(result: dict, docs_jobs_dir: Path) -> Path:
    docs_jobs_dir.mkdir(parents=True, exist_ok=True)
    out = docs_jobs_dir / f"{result['job_id']}.json"
    out.write_text(json.dumps(compact_result(result), indent=1, default=str), encoding="utf-8")
    return out


def failed_result(job_id: str, job_type: str, submitted_at: str, params: dict, exc: BaseException, tb: str) -> dict:
    """Same shape app.jobs._run_one writes for a failed job."""
    return {
        "job_id": job_id, "job_type": job_type, "status": STATUS_FAILED,
        "submitted_at": submitted_at, "completed_at": utcnow(), "params": params,
        "summary": {}, "samples": [], "taxa": [], "composition": {}, "files": {},
        "notes": [f"ERROR: {exc}", tb[-2000:]],
    }


# --------------------------------------------------------------------------- one job
def run_one(row: QueueRow, queue, storage, drive, args, docs_jobs_dir: Path) -> str:
    job_id = row["job_id"].strip()
    job_type = (row.get("job_type") or "").strip().lower()
    submitted_at = (row.get("submitted_at") or "").strip() or utcnow()
    job_dir = config.JOBS_DIR / job_id
    input_dir = job_dir / "input"
    job_dir.mkdir(parents=True, exist_ok=True)
    started = utcnow()
    queue.update(row, status=STATUS_RUNNING, started_at=started, error="")
    log.info("job %s (%s) started", job_id, job_type)
    t0 = time.time()
    params: dict = {}
    try:
        saved = stage_inputs(row, input_dir, drive, args.local_files)
        if job_type == "barcode":
            p = write_pasted_fasta(row.get("sequences_text", ""), input_dir)
            if p is not None:
                saved.append(p.name)
        if not saved:
            raise RuntimeError("the submission contains no input files and no pasted sequences")
        params = build_params(row, job_dir, saved)
        if job_type == "barcode":
            params["input_files"] = [str(input_dir / n) for n in saved]
        register_in_jobs_db(job_id, job_type, params, submitted_at)

        if job_type == "amplicon":
            from app.pipeline import amplicon as runner_mod
        else:
            from app.pipeline import barcoding as runner_mod
        result = runner_mod.run_job(job_id, str(job_dir), params, storage)
        if not isinstance(result, dict):
            raise RuntimeError("runner did not return a dict")
        # exactly what app.jobs._run_one does after run_job returns
        result.setdefault("job_id", job_id)
        result.setdefault("job_type", job_type)
        result.setdefault("submitted_at", submitted_at)
        result.setdefault("completed_at", utcnow())
        result.setdefault("status", STATUS_DONE)
        status = result["status"]
    except Exception as exc:  # noqa: BLE001 - every failure is recorded, never propagated
        tb = traceback.format_exc()
        log.error("job %s failed: %s\n%s", job_id, exc, tb)
        result = failed_result(job_id, job_type or "unknown", submitted_at, params, exc, tb)
        status = STATUS_FAILED

    rp = job_dir / "result.json"
    rp.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    try:
        url = storage.upload_result_file(job_id, str(rp))
        result.setdefault("files", {})["result.json"] = url
        rp.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.error("could not upload result.json for %s: %s", job_id, exc)

    folder_url = result.get("result_folder_url") or ""
    if not folder_url:
        try:
            folder_url = storage.create_job_folder(job_id) or ""
        except Exception as exc:  # noqa: BLE001
            log.warning("create_job_folder failed for %s: %s", job_id, exc)
    result["result_folder_url"] = folder_url
    page_url = f"{config.PUBLIC_BASE_URL}/job.html?id={job_id}" if config.PUBLIC_BASE_URL else f"job.html?id={job_id}"
    result["result_page_url"] = page_url
    try:
        out = write_compact_copy(result, docs_jobs_dir)
        log.info("wrote %s (%d bytes)", out, out.stat().st_size)
    except Exception as exc:  # noqa: BLE001
        log.error("could not write docs/jobs copy for %s: %s", job_id, exc)

    error = "" if status == STATUS_DONE else "; ".join(str(n) for n in (result.get("notes") or [])[:1])[:1000]
    queue.update(row, status=status, completed_at=result.get("completed_at") or utcnow(),
                 result_folder_url=folder_url, result_page_url=page_url, error=error)
    log.info("job %s finished with status %s in %.0fs", job_id, status, time.time() - t0)
    return status


# --------------------------------------------------------------------------- selection
def select_jobs(rows: list[QueueRow], job_id: str | None, max_jobs: int, stale_hours: float) -> list[QueueRow]:
    now = datetime.now(timezone.utc)
    picked: list[QueueRow] = []
    seen: set[str] = set()
    if job_id:
        for r in rows:
            if r["job_id"].strip() == job_id:
                if r.get("status", "").strip().lower() in (STATUS_QUEUED, "", STATUS_RUNNING):
                    picked.append(r)
                    seen.add(job_id)
                else:
                    log.info("job %s already has status %r; nothing to do for it", job_id, r.get("status"))
                    seen.add(job_id)
                break
    candidates = []
    for r in rows:
        if r["job_id"].strip() in seen:
            continue
        st = (r.get("status") or "").strip().lower()
        if st == STATUS_QUEUED or st == "":
            candidates.append(r)
        elif st == STATUS_RUNNING and stale_hours > 0:
            ts = _parse_ts(r.get("started_at", "")) or _parse_ts(r.get("submitted_at", ""))
            if ts is None or (now - ts).total_seconds() > stale_hours * 3600:
                log.warning("job %s has been 'running' for > %.1f h; treating it as queued", r["job_id"], stale_hours)
                candidates.append(r)
    candidates.sort(key=lambda r: (r.get("submitted_at") or "", r.row_number))
    for r in candidates:
        if len(picked) >= max_jobs:
            break
        picked.append(r)
    return picked


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job-id", default="", help="queue job_id to run first (empty: all queued rows)")
    ap.add_argument("--max-jobs", type=int, default=int(os.environ.get("MAX_JOBS_PER_RUN", "5")))
    ap.add_argument("--requeue-stale-hours", type=float, default=6.0,
                    help="'running' rows older than this are re-run (0 disables)")
    ap.add_argument("--docs-jobs-dir", default=os.environ.get("DOCS_JOBS_DIR", str(REPO_ROOT / "docs" / "jobs")))
    ap.add_argument("--local-queue", default="", help="TEST: read/write the queue from this CSV instead of Sheets")
    ap.add_argument("--local-files", action="store_true", help="TEST: file_ids are local paths, not Drive ids")
    ap.add_argument("--wait-for-row", type=int, default=3, help="retries (10 s) when --job-id is not yet visible")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from app.storage import Storage

    config.ensure_dirs()
    storage = Storage()
    drive = None if storage.local_mode else storage._drive  # noqa: SLF001
    if args.local_queue:
        queue = CsvQueue(args.local_queue)
    else:
        if storage.local_mode:
            log.error("no usable Google credentials: cannot read the queue sheet (use --local-queue for tests)")
            return 2
        queue = SheetsQueue(storage._sheets, config.LEDGER_SPREADSHEET_ID)  # noqa: SLF001
    log.info("storage mode: %s; queue: %s; data dir: %s; docs/jobs: %s",
             "LOCAL" if storage.local_mode else "Google Drive/Sheets",
             args.local_queue or f"sheet '{QUEUE_SHEET}' of {config.LEDGER_SPREADSHEET_ID}",
             config.DATA_DIR, args.docs_jobs_dir)

    job_id = args.job_id.strip() or None
    try:
        rows = queue.rows()
        if job_id and not any(r["job_id"].strip() == job_id for r in rows):
            for _ in range(max(args.wait_for_row, 0)):
                log.info("job %s not in the queue yet; waiting 10 s", job_id)
                time.sleep(10)
                rows = queue.rows()
                if any(r["job_id"].strip() == job_id for r in rows):
                    break
    except Exception as exc:  # noqa: BLE001
        log.error("could not read the queue: %s", exc)
        return 2
    picked = select_jobs(rows, job_id, args.max_jobs, args.requeue_stale_hours)
    if job_id and not any(r["job_id"].strip() == job_id for r in rows):
        log.error("job %s is not in the queue (the Apps Script row was not written?)", job_id)
        if not picked:
            return 3
    if not picked:
        log.info("nothing to do: %d queue rows, none queued", len(rows))
        return 0
    log.info("running %d job(s): %s", len(picked), [r["job_id"] for r in picked])
    results = {}
    for row in picked:
        try:
            results[row["job_id"]] = run_one(row, queue, storage, drive, args, Path(args.docs_jobs_dir))
        except Exception as exc:  # noqa: BLE001 - e.g. the queue update itself failed
            log.error("unrecoverable error for job %s: %s\n%s", row["job_id"], exc, traceback.format_exc())
            results[row["job_id"]] = "error"
            try:
                queue.update(row, status=STATUS_FAILED, completed_at=utcnow(), error=str(exc)[:1000])
            except Exception as exc2:  # noqa: BLE001
                log.error("could not even mark job %s failed: %s", row["job_id"], exc2)
    log.info("summary: %s", json.dumps(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
