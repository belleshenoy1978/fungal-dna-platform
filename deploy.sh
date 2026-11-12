#!/usr/bin/env bash
# One-command deployment to Google Cloud Run. Full explanation: DEPLOY.md.
# Usage: PROJECT_ID=<gcp-project> ./deploy.sh      (expects google-token.json from scripts/authorize_google.py,
#        or a service-account key at sa-key.json for Google Workspace Shared-Drive setups)
set -euo pipefail
PROJECT_ID="${PROJECT_ID:?set PROJECT_ID=<your-gcp-project-id>}"
REGION="${REGION:-asia-south1}"
SERVICE="${SERVICE:-fungal-dna-platform}"
BUCKET="${BUCKET:-${PROJECT_ID}-fungal-platform-data}"
SECRET="fungal-platform-google-token"
CRED_FILE="${CRED_FILE:-google-token.json}"
[ -f "$CRED_FILE" ] || CRED_FILE=sa-key.json
[ -f "$CRED_FILE" ] || { echo "No google-token.json (run scripts/authorize_google.py) or sa-key.json found"; exit 1; }

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    drive.googleapis.com sheets.googleapis.com secretmanager.googleapis.com storage.googleapis.com

if gcloud secrets describe "$SECRET" >/dev/null 2>&1; then
  gcloud secrets versions add "$SECRET" --data-file="$CRED_FILE"
else
  gcloud secrets create "$SECRET" --data-file="$CRED_FILE"
fi
RUN_SA="$(gcloud projects describe "$PROJECT_ID" --format 'value(projectNumber)')-compute@developer.gserviceaccount.com"
gcloud secrets add-iam-policy-binding "$SECRET" --member "serviceAccount:$RUN_SA" --role roles/secretmanager.secretAccessor >/dev/null
gsutil ls -b "gs://$BUCKET" >/dev/null 2>&1 || gsutil mb -l "$REGION" "gs://$BUCKET"
gsutil iam ch "serviceAccount:$RUN_SA:objectAdmin" "gs://$BUCKET"

gcloud run deploy "$SERVICE" --source . --region "$REGION" --platform managed \
  --allow-unauthenticated --use-http2 \
  --cpu 2 --memory 4Gi --timeout 3600 --concurrency 20 \
  --min-instances 1 --max-instances 1 --no-cpu-throttling --execution-environment gen2 \
  --add-volume name=refdata,type=cloud-storage,bucket="$BUCKET" \
  --add-volume-mount volume=refdata,mount-path=/data/ref \
  --set-secrets GOOGLE_SERVICE_ACCOUNT_JSON=$SECRET:latest \
  --set-env-vars "DRIVE_RESULTS_FOLDER_ID=1otT7ewsBDb5y05BgTb5x71D-QJb4QQUy,DRIVE_UPLOADS_FOLDER_ID=19O9SW_KsbzeY8C5o4Rfjgsowe2z9IJBN,DRIVE_REFERENCE_FOLDER_ID=1FYgsu73qMZqMDqYGMadp_4ge38Flm47k,LEDGER_SPREADSHEET_ID=1gpTf1k-i0DBJZDlUR1YQhiFgWCO56pjpSDa4M1cEJjA,DATA_DIR=/data,UNITE_SINTAX_DB=/data/ref/unite_sintax.fasta.gz"

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')
gcloud run services update "$SERVICE" --region "$REGION" --update-env-vars "PUBLIC_BASE_URL=$URL" >/dev/null
echo; echo "Deployed: $URL"
echo "Google Sites (Insert > Embed > By URL): $URL/dashboard?embed=1  $URL/submit?embed=1  $URL/barcode?embed=1"
python3 scripts/smoke_test.py "$URL" || echo "smoke test reported a failure - see above"
