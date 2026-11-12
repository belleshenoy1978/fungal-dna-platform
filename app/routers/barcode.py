"""
Barcoding endpoints.

POST /api/barcode                      multipart: files[] (FASTA / .ab1 / FASTQ) and/or
                                       `sequences` (pasted FASTA or raw sequence text),
                                       `submitter`, optional `marker`, `location`, `lat`, `lon`
                                       -> {"job_id": ...}
GET  /api/barcode/{job_id}/report.html standalone HTML report once the job is done
GET  /api/barcode/{job_id}/hits.tsv    tab-separated hit table once the job is done

Inputs are saved under <DATA_DIR>/uploads/barcode/<upload_id>/ before the job is
created; the paths travel in params['input_dir'] so the runner does not depend on
the worker's job_dir convention. Job creation / lookup use app.jobs per the
shared contract (create_job(job_type, params) -> job_id; get_job(job_id) -> dict).
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from app.pipeline.barcoding import ABI_EXT, FASTA_EXT, FASTQ_EXT

router = APIRouter()

MAX_UPLOAD_BYTES = 25 * 1024 * 1024       # per file
MAX_FILES = 50
ALLOWED_EXT = FASTA_EXT | ABI_EXT | FASTQ_EXT | {".gz"}


def _data_dir() -> Path:
    try:
        from app import config  # type: ignore
        d = getattr(config, "DATA_DIR", None)
        if d:
            return Path(d)
    except Exception:
        pass
    return Path(os.environ.get("DATA_DIR", "/data"))


def _safe_name(name: str) -> str:
    name = os.path.basename(name or "upload")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "upload"
    return name[:120]


def _job_dir_candidates(job_id: str, job: dict) -> list[Path]:
    cands = []
    for key in ("job_dir", "dir", "workdir"):
        if job.get(key):
            cands.append(Path(job[key]))
    d = _data_dir()
    cands += [d / "jobs" / job_id, d / "results" / job_id]
    return cands


@router.post("/api/barcode")
async def submit_barcode(
    files: list[UploadFile] | None = File(None),
    sequences: str | None = Form(None),
    submitter: str = Form(""),
    marker: str = Form("ITS"),
    location: str | None = Form(None),
    lat: float | None = Form(None),
    lon: float | None = Form(None),
):
    from app.jobs import create_job  # contract: written by the Amplicon track

    files = [f for f in (files or []) if f is not None and f.filename]
    if not files and not (sequences and sequences.strip()):
        raise HTTPException(status_code=400, detail="Provide at least one FASTA/.ab1 file or pasted sequences.")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"At most {MAX_FILES} files per submission.")

    upload_id = uuid.uuid4().hex[:12]
    input_dir = _data_dir() / "uploads" / "barcode" / upload_id
    input_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for f in files:
        name = _safe_name(f.filename)
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_EXT:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {name} (FASTA, .ab1 or FASTQ expected)")
        data = await f.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"{name} exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        target = input_dir / name
        k = 1
        while target.exists():
            k += 1
            target = input_dir / f"{Path(name).stem}_{k}{Path(name).suffix}"
        target.write_bytes(data)
        saved.append(str(target))

    params = {
        "submitter": (submitter or "").strip()[:200],
        "marker": (marker or "ITS").strip()[:20],
        "location": (location or "").strip()[:200] or None,
        "lat": lat, "lon": lon,
        "input_dir": str(input_dir),
        "input_files": saved,
        "sequences": sequences or "",
        "submitted_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    job_id = create_job("barcode", params)
    return {"job_id": job_id, "status": "queued", "n_files": len(saved),
            "poll": f"/api/jobs/{job_id}", "report": f"/api/barcode/{job_id}/report.html"}


def _serve_job_file(job_id: str, filename: str, media_type: str):
    from app.jobs import get_job  # contract
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", job_id):
        raise HTTPException(status_code=404, detail="unknown job")
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown job")
    for d in _job_dir_candidates(job_id, job):
        p = d / filename
        if p.is_file():
            return FileResponse(str(p), media_type=media_type, filename=filename,
                                content_disposition_type="inline")
    result = job.get("result") or {}
    url = (result.get("files") or {}).get(filename)
    if url and str(url).startswith("http"):
        return RedirectResponse(url, status_code=307)
    status = job.get("status", "unknown")
    if status in ("queued", "running", "pending"):
        return JSONResponse({"job_id": job_id, "status": status, "detail": "job not finished yet"}, status_code=202)
    raise HTTPException(status_code=404, detail=f"{filename} not available for job {job_id} (status {status})")


@router.get("/api/barcode/{job_id}/report.html")
def barcode_report(job_id: str):
    return _serve_job_file(job_id, "report.html", "text/html")


@router.get("/api/barcode/{job_id}/hits.tsv")
def barcode_hits(job_id: str):
    return _serve_job_file(job_id, "hits.tsv", "text/tab-separated-values")
