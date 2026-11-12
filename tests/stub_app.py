"""Minimal FastAPI stub for testing the Dashboard track without the pipeline tracks.

Serves the real page router (app/routers/pages.py) + app/static, and fakes:
  GET /api/jobs/{job_id}            from tests/data/results/<job_id>/result.json
                                     (+ synthetic 'stub-queued', 'stub-running', 'stub-failed')
  GET /api/jobs/{job_id}/files/{n}  files from the same directory
  GET /test-ledger/{sheet}.csv      tests/data/ledger/<sheet>.csv   (populated test ledger)
  GET /test-ledger-empty/{sheet}.csv tests/data/ledger/empty/<sheet>.csv (headers only)
  GET /api/barcode/{job_id}/report.html  placeholder report
Run:  python -m uvicorn tests.stub_app:app --port 8012   (from the repo root)
Point the dashboard at the test ledger with  /dashboard?source=/test-ledger/{sheet}.csv
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.routers import pages

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "tests" / "data" / "results"
LEDGER = ROOT / "tests" / "data" / "ledger"

app = FastAPI(title="fungal-dna-platform dashboard stub")
app.mount("/static", StaticFiles(directory=str(ROOT / "app" / "static")), name="static")
app.include_router(pages.router)

_NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
_SYNTHETIC = {
    "stub-queued": {"job_id": "stub-queued", "job_type": "amplicon", "status": "queued", "submitted_at": _NOW, "params": {"submitter": "stub"}, "notes": ["waiting for worker"]},
    "stub-running": {"job_id": "stub-running", "job_type": "amplicon", "status": "running", "stage": "vsearch cluster_unoise3", "submitted_at": _NOW, "params": {"submitter": "stub"}, "notes": ["cutadapt done", "merging done"]},
    "stub-failed": {"job_id": "stub-failed", "job_type": "amplicon", "status": "failed", "submitted_at": _NOW, "completed_at": _NOW, "error": "vsearch exited with code 1", "params": {"submitter": "stub"}, "notes": ["cutadapt done", "fastq_mergepairs: no reads merged — check pairing mode"]},
}


@app.get("/health")
async def health():
    return {"ok": True, "stub": True}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    if job_id in _SYNTHETIC:
        return _SYNTHETIC[job_id]
    p = RESULTS / job_id / "result.json"
    if not p.exists():
        raise HTTPException(404, "job not found")
    result = json.loads(p.read_text(encoding="utf-8"))
    job = {k: result.get(k) for k in ("job_id", "job_type", "status", "submitted_at", "completed_at", "params")}
    job["result"] = result
    return job


@app.get("/api/jobs/{job_id}/files/{name}")
async def get_job_file(job_id: str, name: str):
    p = RESULTS / job_id / Path(name).name
    if not p.exists():
        raise HTTPException(404, "file not found")
    return FileResponse(str(p))


@app.get("/api/barcode/{job_id}/report.html", response_class=HTMLResponse)
async def barcode_report(job_id: str):
    return HTMLResponse(f"<html><body><h1>Stub barcoding report for {job_id}</h1></body></html>")


def _csv(path: Path):
    if not path.exists():
        raise HTTPException(404, "sheet not found")
    return FileResponse(str(path), media_type="text/csv", headers={"Access-Control-Allow-Origin": "*"})


@app.get("/test-ledger/{sheet}.csv")
async def test_ledger(sheet: str):
    return _csv(LEDGER / f"{Path(sheet).name}.csv")


@app.get("/test-ledger-empty/{sheet}.csv")
async def test_ledger_empty(sheet: str):
    return _csv(LEDGER / "empty" / f"{Path(sheet).name}.csv")


@app.get("/api/amplicon")
@app.get("/api/barcode")
async def not_in_stub():
    return JSONResponse({"detail": "pipeline endpoints are not part of the dashboard stub"}, status_code=501)
