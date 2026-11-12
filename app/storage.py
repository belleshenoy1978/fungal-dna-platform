"""
Storage layer: Google Drive (result files) + Google Sheets (public ledger),
with a LOCAL_MODE fallback that writes to DATA_DIR.

    storage.create_job_folder(job_id)              -> folder_url
    storage.upload_result_file(job_id, local_path) -> public_url
    storage.append_ledger(sheet, rows)             -> None

LOCAL_MODE layout
    DATA_DIR/results/<job_id>/<filename>
    DATA_DIR/ledger/<sheet>.csv          header row = platform_config.json ledger_headers[sheet]

Drive mode
    Service account (google-api-python-client). Each job gets a sub-folder of
    DRIVE_RESULTS_FOLDER_ID; every uploaded file gets an 'anyone with the link'
    reader permission so the public dashboard can fetch it. Ledger rows are
    appended with values().append (USER_ENTERED).

Any Drive/Sheets failure is logged and, for uploads, falls back to the local
path so a finished analysis is never lost because of a network error.
"""
from __future__ import annotations

import csv
import logging
import mimetypes
import os
import shutil
import threading
from pathlib import Path

from app import config

log = logging.getLogger("storage")

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


class Storage:
    def __init__(self) -> None:
        self.local_mode = config.LOCAL_MODE
        self._lock = threading.Lock()
        self._drive = None
        self._sheets = None
        self._job_folders: dict[str, str] = {}
        self._uploaded: dict[tuple[str, str], str] = {}  # (job_id, filename) -> Drive file id
        config.ensure_dirs()
        if not self.local_mode:
            try:
                self._connect()
            except Exception as exc:  # noqa: BLE001 - any credential problem => local mode
                log.warning("Google credentials unusable (%s); falling back to LOCAL_MODE", exc)
                self.local_mode = True

    # ------------------------------------------------------------------ setup
    def _connect(self) -> None:
        from google.oauth2 import service_account  # imported lazily: optional in LOCAL_MODE
        from googleapiclient.discovery import build

        info = config.service_account_info()
        if not info:
            raise RuntimeError("no Google credentials configured")
        if info.get("type") == "authorized_user" or ("refresh_token" in info and "private_key" not in info):
            # OAuth user credentials (client_id/client_secret/refresh_token) produced by
            # scripts/authorize_google.py. Preferred for a personal Google account: service
            # accounts created after April 2025 have zero Drive storage quota and cannot create
            # files in a My Drive folder (403 storageQuotaExceeded); acting as the account owner
            # avoids that and keeps every file under the owner's quota.
            from google.oauth2.credentials import Credentials as UserCredentials

            creds = UserCredentials.from_authorized_user_info(info, scopes=SCOPES)
            who = "OAuth user (refresh token)"
        else:
            creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
            who = info.get("client_email", "service account")
        self._drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        self._sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
        log.info("Storage connected to Google Drive/Sheets as %s", who)

    # ------------------------------------------------------------- local paths
    def local_results_dir(self, job_id: str) -> Path:
        p = config.DATA_DIR / "results" / job_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _local_url(self, job_id: str, name: str) -> str:
        return f"{config.PUBLIC_BASE_URL}/api/jobs/{job_id}/files/{name}"

    # ------------------------------------------------------------- public API
    def create_job_folder(self, job_id: str) -> str:
        if self.local_mode:
            return f"{config.PUBLIC_BASE_URL}/jobs/{job_id}"
        with self._lock:
            if job_id in self._job_folders:
                fid = self._job_folders[job_id]
            else:
                meta = {
                    "name": job_id,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [config.DRIVE_RESULTS_FOLDER_ID],
                }
                folder = self._drive.files().create(body=meta, fields="id", supportsAllDrives=True).execute()
                fid = folder["id"]
                self._make_public(fid)
                self._job_folders[job_id] = fid
        return f"https://drive.google.com/drive/folders/{fid}"

    def upload_result_file(self, job_id: str, local_path: str) -> str:
        local_path = str(local_path)
        name = os.path.basename(local_path)
        # Always keep a local copy so /api/jobs/{id}/files/{name} can serve it.
        dest = self.local_results_dir(job_id) / name
        if Path(local_path).resolve() != dest.resolve():
            shutil.copy2(local_path, dest)
        if self.local_mode:
            return self._local_url(job_id, name)
        try:
            from googleapiclient.http import MediaFileUpload

            self.create_job_folder(job_id)
            folder_id = self._job_folders[job_id]
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            media = MediaFileUpload(local_path, mimetype=mime, resumable=os.path.getsize(local_path) > 5_000_000)
            with self._lock:
                existing = self._uploaded.get((job_id, name))
                if existing:  # re-upload of the same file => new revision, not a duplicate
                    f = (
                        self._drive.files()
                        .update(fileId=existing, media_body=media, fields="id", supportsAllDrives=True)
                        .execute()
                    )
                else:
                    meta = {"name": name, "parents": [folder_id]}
                    f = (
                        self._drive.files()
                        .create(body=meta, media_body=media, fields="id", supportsAllDrives=True)
                        .execute()
                    )
                    self._make_public(f["id"])
                    self._uploaded[(job_id, name)] = f["id"]
            # Direct-download form; works for JSON/TSV fetched by the dashboard.
            return f"https://drive.google.com/uc?export=download&id={f['id']}"
        except Exception as exc:  # noqa: BLE001
            log.error("Drive upload failed for %s (%s); serving local copy", name, exc)
            return self._local_url(job_id, name)

    def append_ledger(self, sheet: str, rows: list[list]) -> None:
        if not rows:
            return
        headers = config.LEDGER_HEADERS.get(sheet)
        if headers is None:
            raise ValueError(f"unknown ledger sheet '{sheet}'")
        for r in rows:
            if len(r) != len(headers):
                raise ValueError(
                    f"ledger row for sheet '{sheet}' has {len(r)} cells, expected {len(headers)} ({headers})"
                )
        rows = [["" if v is None else v for v in r] for r in rows]
        # Local CSV is always written (audit trail even in Drive mode).
        self._append_local_csv(sheet, headers, rows)
        if self.local_mode:
            return
        try:
            with self._lock:
                self._sheets.spreadsheets().values().append(
                    spreadsheetId=config.LEDGER_SPREADSHEET_ID,
                    range=f"'{sheet}'!A1",
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": [[str(v) for v in r] for r in rows]},
                ).execute()
        except Exception as exc:  # noqa: BLE001
            log.error("Sheets append failed for '%s' (%s); rows kept in local CSV", sheet, exc)

    # ---------------------------------------------------------------- helpers
    def _append_local_csv(self, sheet: str, headers: list[str], rows: list[list]) -> None:
        path = config.DATA_DIR / "ledger" / f"{sheet}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            new = not path.exists() or path.stat().st_size == 0
            with open(path, "a", encoding="utf-8", newline="") as fh:
                wr = csv.writer(fh)
                if new:
                    wr.writerow(headers)
                wr.writerows(rows)

    def _make_public(self, file_id: str) -> None:
        try:
            self._drive.permissions().create(
                fileId=file_id, body={"type": "anyone", "role": "reader"}, supportsAllDrives=True
            ).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning("could not set public permission on %s: %s", file_id, exc)


storage = Storage()
