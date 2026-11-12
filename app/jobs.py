"""
SQLite-backed job queue with a single background worker thread.

    create_job(job_type, params) -> job_id (uuid4 hex[:12])
    get_job(job_id)              -> dict | None  (includes 'result' when done)
    start_worker()               -> starts the worker thread once (idempotent)
    job_dir(job_id)              -> Path  (<DATA_DIR>/jobs/<job_id>)

Runners are registered per job_type and must have the signature
    run_job(job_id: str, job_dir: str, params: dict, storage) -> dict
The worker writes the returned dict to <job_dir>/result.json, uploads it via
storage.upload_result_file, and marks the job 'done' (or 'failed' with the
error text). Jobs left 'running' by a previous process (container restart) are
re-queued on startup.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app import config

log = logging.getLogger("jobs")

RUNNERS: dict[str, Callable] = {}
_worker: threading.Thread | None = None
_wake = threading.Event()
_db_lock = threading.Lock()


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def register_runner(job_type: str, fn: Callable) -> None:
    RUNNERS[job_type] = fn


def _default_runners() -> None:
    """Register the two pipeline runners lazily; a missing module is not fatal."""
    try:
        from app.pipeline import amplicon

        RUNNERS.setdefault("amplicon", amplicon.run_job)
    except ImportError as exc:  # pragma: no cover
        log.warning("amplicon runner unavailable: %s", exc)
    try:
        from app.pipeline import barcoding

        RUNNERS.setdefault("barcode", barcoding.run_job)
    except ImportError as exc:  # pragma: no cover
        log.warning("barcode runner unavailable: %s", exc)


def _connect() -> sqlite3.Connection:
    config.JOBS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.JOBS_DB_PATH, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        """CREATE TABLE IF NOT EXISTS jobs (
               job_id TEXT PRIMARY KEY,
               job_type TEXT NOT NULL,
               status TEXT NOT NULL,
               params TEXT NOT NULL,
               submitted_at TEXT NOT NULL,
               started_at TEXT,
               completed_at TEXT,
               error TEXT,
               result_url TEXT
           )"""
    )
    return con


def job_dir(job_id: str) -> Path:
    p = config.JOBS_DIR / job_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def create_job(job_type: str, params: dict, queued: bool = True) -> str:
    """Insert a job and return its id.

    IMPORTANT for routers that save uploaded files into job_dir(job_id)/input
    AFTER calling create_job: pass queued=False, write the files, then call
    enqueue(job_id). Otherwise the worker may start before all uploads are on
    disk (the queue is live as soon as a job is 'queued').
    """
    if job_type not in ("amplicon", "barcode"):
        raise ValueError(f"unknown job_type {job_type!r}")
    job_id = uuid.uuid4().hex[:12]
    with _db_lock, _connect() as con:
        con.execute(
            "INSERT INTO jobs(job_id, job_type, status, params, submitted_at) VALUES (?,?,?,?,?)",
            (job_id, job_type, "queued" if queued else "uploading", json.dumps(params), utcnow()),
        )
    job_dir(job_id)
    if queued:
        _wake.set()
    return job_id


def enqueue(job_id: str) -> None:
    """Move an 'uploading' job into the live queue (see create_job)."""
    _set(job_id, status="queued")
    _wake.set()


def get_job(job_id: str) -> dict | None:
    with _db_lock, _connect() as con:
        row = con.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["params"] = json.loads(d["params"])
    if d["status"] in ("uploading", "queued", "running"):
        with _db_lock, _connect() as con:
            ahead = con.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='queued' AND submitted_at < ?", (d["submitted_at"],)
            ).fetchone()[0]
        d["queue_position"] = int(ahead) if d["status"] == "queued" else 0
    rp = config.JOBS_DIR / job_id / "result.json"
    if d["status"] in ("done", "failed") and rp.exists():
        try:
            with open(rp, encoding="utf-8") as fh:
                d["result"] = json.load(fh)
        except (OSError, json.JSONDecodeError):
            pass
    return d


def list_jobs(limit: int = 100) -> list[dict]:
    with _db_lock, _connect() as con:
        rows = con.execute(
            "SELECT job_id, job_type, status, submitted_at, completed_at, result_url FROM jobs "
            "ORDER BY submitted_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def _set(job_id: str, **fields) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    with _db_lock, _connect() as con:
        con.execute(f"UPDATE jobs SET {cols} WHERE job_id=?", (*fields.values(), job_id))


def _next_queued() -> dict | None:
    with _db_lock, _connect() as con:
        row = con.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY submitted_at LIMIT 1").fetchone()
    return dict(row) if row else None


def _run_one(job: dict) -> None:
    from app.storage import storage

    job_id, job_type = job["job_id"], job["job_type"]
    params = json.loads(job["params"])
    jd = job_dir(job_id)
    started = utcnow()
    _set(job_id, status="running", started_at=started)
    log.info("job %s (%s) started", job_id, job_type)
    runner = RUNNERS.get(job_type)
    try:
        if runner is None:
            raise RuntimeError(f"no runner registered for job_type {job_type!r}")
        result = runner(job_id, str(jd), params, storage)
        if not isinstance(result, dict):
            raise RuntimeError("runner did not return a dict")
        result.setdefault("job_id", job_id)
        result.setdefault("job_type", job_type)
        result.setdefault("submitted_at", job["submitted_at"])
        result.setdefault("completed_at", utcnow())
        result.setdefault("status", "done")
        status = result["status"]
    except Exception as exc:  # noqa: BLE001 - we must record any failure
        tb = traceback.format_exc()
        log.error("job %s failed: %s\n%s", job_id, exc, tb)
        result = {
            "job_id": job_id,
            "job_type": job_type,
            "status": "failed",
            "submitted_at": job["submitted_at"],
            "completed_at": utcnow(),
            "params": params,
            "summary": {},
            "samples": [],
            "taxa": [],
            "composition": {},
            "files": {},
            "notes": [f"ERROR: {exc}", tb[-2000:]],
        }
        status = "failed"
    rp = jd / "result.json"
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1, default=str)
    url = None
    try:
        url = storage.upload_result_file(job_id, str(rp))
        result.setdefault("files", {})["result.json"] = url
        with open(rp, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1, default=str)
    except Exception as exc:  # noqa: BLE001
        log.error("could not upload result.json for %s: %s", job_id, exc)
    _set(job_id, status=status, completed_at=result["completed_at"], result_url=url,
         error=None if status == "done" else "; ".join(result.get("notes", [])[:1]))
    log.info("job %s finished with status %s", job_id, status)


def _worker_loop() -> None:
    _default_runners()
    # Re-queue jobs interrupted by a previous process.
    with _db_lock, _connect() as con:
        con.execute("UPDATE jobs SET status='queued' WHERE status='running'")
    while True:
        job = _next_queued()
        if job is None:
            _wake.wait(timeout=5)
            _wake.clear()
            continue
        try:
            _run_one(job)
        except Exception:  # noqa: BLE001 - keep the worker alive no matter what
            log.exception("worker crashed on job %s", job.get("job_id"))
            time.sleep(1)


def start_worker() -> threading.Thread:
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_worker_loop, name="job-worker", daemon=True)
        _worker.start()
    return _worker
