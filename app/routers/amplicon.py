"""
Amplicon job API.

POST /api/amplicon            multipart: files[] (FASTQ / FASTQ.gz) + form fields
                              marker, fwd_primer, rev_primer, location, lat, lon,
                              submitter, pairing ('paired'|'single'), substrate,
                              max_ee, min_merge_len            -> {"job_id": ...}
GET  /api/jobs/{job_id}       job record; includes "result" once done/failed
GET  /api/jobs/{job_id}/files/{name}   download a result file (local copy)
GET  /api/jobs                 recent jobs (for the dashboard)
GET  /health
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app import config, jobs
from app.pipeline.amplicon import MARKERS

router = APIRouter()

FASTQ_RE = re.compile(r"\.(fastq|fq)(\.gz)?$", re.IGNORECASE)
MAX_FILES = 64
SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_filename(name: str) -> str:
    base = os.path.basename(name.replace("\\", "/"))
    return SAFE_NAME.sub("_", base).strip("._") or "reads.fastq"


@router.get("/health")
def health():
    return {"status": "ok", **config.describe()}


@router.post("/api/amplicon")
async def submit_amplicon(
    files: list[UploadFile] = File(..., description="FASTQ or FASTQ.gz files"),
    marker: str = Form("ITS1"),
    fwd_primer: str = Form(""),
    rev_primer: str = Form(""),
    location: str = Form(""),
    lat: str = Form(""),
    lon: str = Form(""),
    submitter: str = Form(""),
    pairing: str = Form("paired"),
    substrate: str = Form(""),
    max_ee: str = Form(""),
    min_merge_len: str = Form(""),
):
    if marker not in MARKERS:
        raise HTTPException(400, f"marker must be one of {list(MARKERS)}")
    files = [f for f in files if f.filename]
    if not files:
        raise HTTPException(400, "no files uploaded")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"at most {MAX_FILES} files per job")
    bad = [f.filename for f in files if not FASTQ_RE.search(f.filename)]
    if bad:
        raise HTTPException(400, f"not FASTQ(.gz): {bad}")

    params = {
        "marker": marker,
        "fwd_primer": fwd_primer.strip(),
        "rev_primer": rev_primer.strip(),
        "paired": pairing.strip().lower() not in {"single", "se", "unpaired", "false", "0"},
        "location": location.strip(),
        "lat": lat.strip() or None,
        "lon": lon.strip() or None,
        "submitter": submitter.strip(),
        "substrate": substrate.strip(),
    }
    if max_ee.strip():
        params["max_ee"] = max_ee.strip()
    if min_merge_len.strip():
        params["min_merge_len"] = min_merge_len.strip()

    job_id = jobs.create_job("amplicon", params, queued=False)  # enqueue only after uploads are on disk
    in_dir = Path(jobs.job_dir(job_id)) / "input"
    in_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        dest = in_dir / _safe_filename(f.filename)
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out, length=1024 * 1024)
        saved.append(dest.name)
    jobs.enqueue(job_id)
    return {"job_id": job_id, "status": "queued", "files": saved, "poll": f"/api/jobs/{job_id}"}


@router.get("/api/jobs")
def recent_jobs(limit: int = 50):
    return {"jobs": jobs.list_jobs(limit=min(max(limit, 1), 500))}


@router.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    job["caveat"] = config.CAVEAT_FOOTER
    return JSONResponse(job)


@router.get("/api/jobs/{job_id}/files/{name}")
def job_file(job_id: str, name: str):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", job_id) or "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "bad path")
    for base in (config.DATA_DIR / "results" / job_id, config.JOBS_DIR / job_id):
        p = base / name
        if p.is_file():
            media = "application/json" if name.endswith(".json") else (
                "text/tab-separated-values" if name.endswith(".tsv") else "text/plain")
            return FileResponse(str(p), media_type=media, filename=name)
    raise HTTPException(404, "file not found")
