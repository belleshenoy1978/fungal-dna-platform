#!/usr/bin/env python
"""Fetch the UNITE v10 general FASTA release (all eukaryotes) and convert it to the
SINTAX format used by VSEARCH.

Source: Abarenkov et al. (2025) UNITE general FASTA release for eukaryotes,
DOI 10.15156/BIO/3301231, CC BY 4.0. Direct file resolved from the PlutoF API.

Order of attempts:
  1. a pre-built SINTAX file already on the volume (nothing to do)
  2. a pre-built SINTAX file in the Drive reference/ folder (UNITE_SINTAX_DRIVE_URL env)
  3. download the UNITE release tgz (50 MB), verify size, convert with make_sintax_db.py
"""
import argparse, gzip, os, shutil, subprocess, sys, tarfile, tempfile, urllib.request

UNITE_URL = "https://s3.hpc.ut.ee/plutof-public/original/e861a3d6-54f4-42dc-882a-5f129beac39a.tgz"
UNITE_BYTES = 50350085           # Content-Length observed 2026-11-12
UNITE_DOI = "10.15156/BIO/3301231"
HERE = os.path.dirname(os.path.abspath(__file__))


def download(url, dest, expected=None):
    print(f"[fetch_reference] downloading {url}")
    with urllib.request.urlopen(url, timeout=600) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, length=1 << 20)
    size = os.path.getsize(dest)
    if expected and size != expected:
        raise SystemExit(f"size mismatch: got {size}, expected {expected}; refusing to use a truncated download")
    print(f"[fetch_reference] {size} bytes OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="destination .fasta.gz in SINTAX format")
    ap.add_argument("--keep-release", action="store_true")
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if os.path.exists(out) and os.path.getsize(out) > 1_000_000:
        print(f"[fetch_reference] already present: {out}")
        return
    drive_url = os.environ.get("UNITE_SINTAX_DRIVE_URL")
    if drive_url:
        try:
            download(drive_url, out)
            with gzip.open(out, "rt") as fh:
                assert fh.readline().startswith(">")
            print("[fetch_reference] using pre-built SINTAX db from Drive")
            return
        except Exception as e:  # noqa: BLE001
            print(f"[fetch_reference] Drive copy unusable ({e}); falling back to UNITE download")
    with tempfile.TemporaryDirectory() as td:
        tgz = os.path.join(td, "unite.tgz")
        download(UNITE_URL, tgz, UNITE_BYTES)
        with tarfile.open(tgz) as tf:
            members = [m for m in tf.getmembers() if m.name.endswith(".fasta")]
            if not members:
                raise SystemExit("no .fasta inside the UNITE archive")
            tf.extract(members[0], td)
            fasta = os.path.join(td, members[0].name)
        sintax = os.path.join(td, "unite_sintax.fasta")
        subprocess.run([sys.executable, os.path.join(HERE, "make_sintax_db.py"), fasta, sintax], check=True)
        with open(sintax, "rb") as fin, gzip.open(out + ".tmp", "wb", compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout)
        os.replace(out + ".tmp", out)
        if a.keep_release:
            shutil.copy(fasta, os.path.join(os.path.dirname(out), os.path.basename(fasta)))
    print(f"[fetch_reference] wrote {out} ({os.path.getsize(out)} bytes); source DOI {UNITE_DOI}")


if __name__ == "__main__":
    main()
