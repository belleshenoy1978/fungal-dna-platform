"""
fungal-dna-platform FastAPI application.

    uvicorn app.main:app --host 0.0.0.0 --port 8080

Routers: amplicon (this track), barcode and pages (other tracks; imported with
try/except so the API runs before those files exist). Static files are served
from app/static. No X-Frame-Options header is set anywhere so Google Sites can
embed the pages in an iframe.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import config, jobs

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    from app.storage import storage  # noqa: F401  (instantiates Drive client or LOCAL_MODE)

    jobs.start_worker()
    log.info("started; LOCAL_MODE=%s DATA_DIR=%s", config.LOCAL_MODE, config.DATA_DIR)
    yield


app = FastAPI(title="Fungal DNA Platform", version="0.1.0", lifespan=lifespan,
              description="Fungal eDNA amplicon and DNA-barcoding service for belleshenoy.in. "
                          + config.CAVEAT_FOOTER)

static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

from app.routers import amplicon  # noqa: E402

app.include_router(amplicon.router)

try:
    from app.routers import barcode  # noqa: E402

    app.include_router(barcode.router)
except ImportError as exc:  # barcoding track not assembled yet
    log.warning("barcode router not loaded: %s", exc)

try:
    from app.routers import pages  # noqa: E402

    app.include_router(pages.router)
except ImportError as exc:  # dashboard track not assembled yet
    log.warning("pages router not loaded: %s", exc)

    @app.get("/", include_in_schema=False)
    def _root_placeholder():
        return {"service": "fungal-dna-platform", "api": ["/api/amplicon", "/api/jobs/{job_id}", "/health"],
                "caveat": config.CAVEAT_FOOTER}
