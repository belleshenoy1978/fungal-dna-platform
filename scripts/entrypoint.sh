#!/bin/sh
# Entrypoint: make sure the UNITE SINTAX database exists, then start the API.
# hypercorn serves HTTP/2 cleartext (h2c), which Cloud Run needs (--use-http2) to accept
# request bodies above 32 MiB - i.e. FASTQ uploads.
set -e
DB="${UNITE_SINTAX_DB:-/data/ref/unite_sintax.fasta.gz}"
if [ ! -s "$DB" ]; then
  echo "[entrypoint] reference database missing at $DB - fetching UNITE v10 (runs once per volume)"
  python scripts/fetch_reference.py --out "$DB" &
else
  echo "[entrypoint] reference database present: $DB"
fi
exec hypercorn app.main:app --bind "0.0.0.0:${PORT:-8080}" --workers 1 --keep-alive 75
