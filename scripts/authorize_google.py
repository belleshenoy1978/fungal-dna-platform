#!/usr/bin/env python
"""One-time authorisation: let the platform write to YOUR Google Drive and Sheets.

Why not a service account?  Google service accounts created after April 2025 have zero
Drive storage quota, so they cannot create files inside a My Drive folder (the API answers
403 storageQuotaExceeded).  Acting as the account owner through OAuth avoids that.

Steps (once, on your own computer):
  1. Google Cloud console -> APIs & Services -> Credentials -> Create credentials ->
     OAuth client ID -> Application type "Desktop app"; download the JSON as client_secret.json.
     (Also enable the Google Drive API and Google Sheets API for the project, and add your
     own Google address as a test user on the OAuth consent screen.)
  2. pip install google-auth-oauthlib
  3. python scripts/authorize_google.py client_secret.json
     -> a browser window asks you to approve Drive + Sheets access; google-token.json is written.
  4. Store google-token.json in Secret Manager (deploy.sh does this when the file exists).
"""
import json, sys

SCOPES = ["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]

def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    out = sys.argv[2] if len(sys.argv) > 2 else "google-token.json"
    info = json.loads(creds.to_json())
    # app/storage.py selects OAuth user credentials only when type == "authorized_user";
    # newer google-auth versions omit the field from Credentials.to_json().
    info.setdefault("type", "authorized_user")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(info, fh)
    assert info.get("refresh_token"), "no refresh token returned - rerun and make sure you approve with prompt=consent"
    print(f"wrote {out} (type={info.get('type')}). Keep it private; it grants write access to your Drive.")

if __name__ == "__main__":
    main()
