"""
Amplicon (ITS / 18S) metabarcoding runner for the fungal-dna-platform job queue.

Wraps the standalone edna_fungal_pipeline.py (cutadapt -> VSEARCH merge ->
quality filter -> dereplicate -> UNOISE3 -> UCHIME3 de novo -> ASV table ->
SINTAX against UNITE) as

    run_job(job_id, job_dir, params, storage) -> result dict

Input files live in <job_dir>/input/. R1/R2 mates are detected from the
filenames (_1/_2, _R1/_R2, .R1/.R2, with optional _001 suffix). Every sample in
a job shares ONE marker (params['marker']); sub-regions are never pooled, so a
submitter who has ITS1 and ITS2 libraries must submit two jobs.

params (all optional except marker)
    marker        ITS1 | ITS2 | 18S-V4 | 18S-V9 | other
    fwd_primer    5' forward primer as ordered (IUPAC ok); empty => skip trimming
    rev_primer    5' reverse primer as ordered; empty => skip trimming
    paired        bool, default True (ignored when no mate files are found)
    min_merge_len int, default per marker (ITS1/ITS2 100, 18S-V4 200, 18S-V9 80)
    max_ee        float, default 1.0
    minsize       int, UNOISE3 minimum abundance, default 8
    sintax_cutoff float, default 0.8
    location, lat, lon, submitter, substrate

Outputs written into <job_dir>/ and uploaded with storage.upload_result_file:
    asv_table.tsv, asv_taxonomy.tsv, asvs.fasta, read_tracking.tsv,
    diversity_summary.tsv, composition_phylum.tsv, composition_genus.tsv,
    pipeline_log.txt
Ledger rows appended with storage.append_ledger: 'jobs', 'samples', 'taxa'.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from app import config

log = logging.getLogger("pipeline.amplicon")

MARKERS = ("ITS1", "ITS2", "18S-V4", "18S-V9", "other")
DEFAULT_MINLEN = {"ITS1": 100, "ITS2": 100, "18S-V4": 200, "18S-V9": 80, "other": 80}
TOP_TAXA_JSON = 500
TOP_TAXA_LEDGER = 200

COMP = {"A": "T", "C": "G", "G": "C", "T": "A", "R": "Y", "Y": "R", "S": "S", "W": "W",
        "K": "M", "M": "K", "B": "V", "V": "B", "D": "H", "H": "D", "N": "N", "I": "N"}
RANKS = {"d": "kingdom", "p": "phylum", "c": "class", "o": "order", "f": "family", "g": "genus", "s": "species"}
RANK_ORDER = ["kingdom", "phylum", "class", "order", "family", "genus", "species"]

MATE_RE = re.compile(r"^(?P<stem>.+?)(?:[._-](?:R|r)?(?P<mate>[12]))(?:_001)?$")
FASTQ_EXT_RE = re.compile(r"\.(fastq|fq)(\.gz)?$", re.IGNORECASE)


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rc(seq: str) -> str:
    return "".join(COMP.get(b, "N") for b in reversed(seq.upper()))


def clean_id(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("._-")
    return s or "sample"


def resolve_vsearch(vsearch_bin: str) -> str:
    """Return an executable path for VSEARCH or raise a clear error (OS-agnostic)."""
    if os.path.isfile(vsearch_bin) and os.access(vsearch_bin, os.X_OK):
        return vsearch_bin
    found = shutil.which(vsearch_bin)
    if found:
        return found
    raise RuntimeError(f"VSEARCH binary not found: {vsearch_bin!r} (set VSEARCH_BIN)")


# ------------------------------------------------------------------ pairing
def detect_pairs(input_dir: str) -> list[dict]:
    """Group FASTQ(.gz) files into samples; returns [{'sample_id','r1','r2'|None}]."""
    files = sorted(
        p for p in Path(input_dir).iterdir()
        if p.is_file() and FASTQ_EXT_RE.search(p.name)
    )
    groups: dict[str, dict] = {}
    for p in files:
        base = FASTQ_EXT_RE.sub("", p.name)
        m = MATE_RE.match(base)
        if m:
            stem, mate = m.group("stem"), m.group("mate")
        else:
            stem, mate = base, None
        sid = clean_id(stem)
        g = groups.setdefault(sid, {"sample_id": sid, "r1": None, "r2": None})
        if mate == "2":
            g["r2"] = str(p)
        elif g["r1"] is None:
            g["r1"] = str(p)
        else:  # second un-mated file with same stem: treat as R2
            g["r2"] = str(p)
    samples = []
    for sid, g in groups.items():
        if g["r1"] is None and g["r2"] is not None:  # only an R2 => treat as single
            g["r1"], g["r2"] = g["r2"], None
        samples.append(g)
    return samples


# ------------------------------------------------------------------ runner
class Runner:
    def __init__(self, vsearch: str, logpath: str):
        self.vsearch = vsearch
        self.log = open(logpath, "w", encoding="utf-8")
        self.timings: list[tuple[str, float]] = []

    def _record(self, tag: str, cmd: list[str], res: subprocess.CompletedProcess, t0: float) -> None:
        dt = time.time() - t0
        self.timings.append((tag, round(dt, 2)))
        self.log.write(f"\n===== {tag} ({dt:.1f}s) =====\n{' '.join(cmd)}\n{res.stderr}\n")
        self.log.flush()

    def vs(self, args: list[str], tag: str) -> str:
        cmd = [self.vsearch] + [str(a) for a in args]
        t0 = time.time()
        res = subprocess.run(cmd, capture_output=True, text=True)
        self._record(tag, cmd, res, t0)
        if res.returncode != 0:
            raise RuntimeError(f"VSEARCH step '{tag}' failed:\n{res.stderr[-1500:]}")
        return res.stderr

    def cutadapt(self, args: list[str], tag: str) -> None:
        cmd = [sys.executable, "-m", "cutadapt"] + [str(a) for a in args]
        t0 = time.time()
        res = subprocess.run(cmd, capture_output=True, text=True)
        self._record(tag, cmd, res, t0)
        if res.returncode != 0:
            raise RuntimeError(f"cutadapt step '{tag}' failed:\n{res.stderr[-1500:]}")

    def close(self) -> None:
        self.log.write("\n===== timings (s) =====\n")
        for tag, dt in self.timings:
            self.log.write(f"{tag}\t{dt}\n")
        self.log.close()


# ------------------------------------------------------------------ parsing
def parse_sintax(path: str) -> pd.DataFrame:
    """SINTAX tabbedout -> DataFrame indexed by ASV with rank columns + sintax_conf."""
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            f = line.rstrip("\n").split("\t")
            d = {v: None for v in RANKS.values()}
            conf_by_rank: dict[str, float] = {}
            if len(f) > 1 and f[1]:
                for el in f[1].split(","):
                    m = re.match(r"([a-z]):(.+)\(([\d.]+)\)$", el.strip())
                    if m and m.group(1) in RANKS:
                        conf_by_rank[RANKS[m.group(1)]] = float(m.group(3))
            if len(f) > 3 and f[3]:
                for el in f[3].split(","):
                    r, _, v = el.partition(":")
                    if r in RANKS and v:
                        d[RANKS[r]] = v
            deepest = [r for r in RANK_ORDER if d.get(r)]
            d["sintax_conf"] = conf_by_rank.get(deepest[-1]) if deepest else None
            d["assignment_level"] = deepest[-1] if deepest else "unassigned"
            rows.append(dict(asv_id=f[0].split(";")[0], **d))
    if not rows:
        return pd.DataFrame(columns=["asv_id"] + RANK_ORDER + ["sintax_conf", "assignment_level"]).set_index("asv_id")
    return pd.DataFrame(rows).set_index("asv_id")


def read_fasta(path: str) -> dict[str, str]:
    seqs: dict[str, str] = {}
    name = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith(">"):
                name = line[1:].strip().split(";")[0]
                seqs[name] = ""
            elif name is not None:
                seqs[name] += line.strip()
    return seqs


def count_fastq_reads(path: str) -> int:
    opener = gzip.open if path.endswith(".gz") else open
    n = 0
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        for _ in fh:
            n += 1
    return n // 4


def shannon(counts) -> float | None:
    x = np.asarray(counts, float)
    x = x[x > 0]
    if x.size == 0:
        return None
    p = x / x.sum()
    return round(float(-(p * np.log(p)).sum()), 4)


def _none_if_nan(v):
    if v is None:
        return None
    try:
        if isinstance(v, float) and np.isnan(v):
            return None
    except TypeError:
        pass
    return v


def _flt(v, default=None):
    try:
        if v is None or str(v).strip() == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ main entry
def run_job(job_id: str, job_dir: str, params: dict, storage) -> dict:
    t_start = time.time()
    job_dir = str(job_dir)
    input_dir = os.path.join(job_dir, "input")
    work = os.path.join(job_dir, "work")
    for sub in ("trim", "merge", "filt", "asv"):
        os.makedirs(os.path.join(work, sub), exist_ok=True)
    notes: list[str] = []

    # ---- parameters -------------------------------------------------------
    marker = str(params.get("marker") or "other").strip()
    if marker not in MARKERS:
        notes.append(f"Unknown marker {marker!r}; treated as 'other'.")
        marker = "other"
    fwd = re.sub(r"[^A-Za-z]", "", str(params.get("fwd_primer") or "")).upper()
    rev = re.sub(r"[^A-Za-z]", "", str(params.get("rev_primer") or "")).upper()
    trim = bool(fwd and rev)
    if (fwd and not rev) or (rev and not fwd):
        notes.append("Only one primer supplied; both are required for trimming, so trimming was skipped.")
    paired_req = params.get("paired", True)
    if isinstance(paired_req, str):
        paired_req = paired_req.strip().lower() in {"1", "true", "yes", "on", "paired"}
    minlen = int(_flt(params.get("min_merge_len"), DEFAULT_MINLEN[marker]))
    maxee = _flt(params.get("max_ee"), 1.0)
    minsize = int(_flt(params.get("minsize"), 8))
    cutoff = _flt(params.get("sintax_cutoff"), 0.8)
    threads = str(int(params.get("threads") or config.PIPELINE_THREADS))
    error_rate, min_overlap = "0.15", "10"

    vsearch = resolve_vsearch(params.get("vsearch_bin") or config.VSEARCH_BIN)
    db = params.get("sintax_db") or config.UNITE_SINTAX_DB
    if marker.startswith("18S"):
        pr2 = os.environ.get("PR2_SINTAX_DB", "")
        if pr2 and os.path.exists(pr2):
            db = pr2
            notes.append("18S marker classified against the PR2 SINTAX reference (PR2_SINTAX_DB).")
        else:
            notes.append("18S marker classified against the UNITE ITS reference because no PR2 SINTAX "
                         "database is configured; most 18S ASVs will remain unassigned.")
    if not os.path.exists(db):
        raise RuntimeError(f"SINTAX reference database not found: {db}")

    run = Runner(vsearch, os.path.join(job_dir, "pipeline_log.txt"))
    run.log.write(f"job {job_id}\nparams {json.dumps(params, default=str)}\nvsearch {vsearch}\ndb {db}\n")

    # ---- samples ----------------------------------------------------------
    samples = detect_pairs(input_dir)
    if not samples:
        raise RuntimeError("no FASTQ(.gz) files found in input/")
    paired = bool(paired_req) and all(s["r2"] for s in samples)
    if paired_req and not paired:
        unp = [s["sample_id"] for s in samples if not s["r2"]]
        notes.append(f"Paired mode requested but no R2 found for {unp}; all samples processed single-end "
                     "(R2 files, where present, were ignored).")
    if not paired:
        for s in samples:
            s["r2"] = None
    notes.append(f"Mode: {'paired-end merging' if paired else 'single-end'}; marker {marker}; "
                 f"primer trimming {'ON' if trim else 'OFF (no primers supplied: reads assumed pre-trimmed)'}; "
                 f"maxEE {maxee}; min length {minlen}; UNOISE3 minsize {minsize}; SINTAX cutoff {cutoff}.")

    track: list[dict] = []
    filt_files: list[str] = []
    for s in samples:
        sid = s["sample_id"]
        t1 = os.path.join(work, "trim", f"{sid}_R1.fastq.gz")
        t2 = os.path.join(work, "trim", f"{sid}_R2.fastq.gz")
        rep = os.path.join(work, "trim", f"{sid}.cutadapt.json")
        ca = ["--minimum-length", "50", "-j", "1", "--json", rep, "-o", t1]
        if trim:
            ca = ["-g", fwd, "-a", rc(rev), "-e", error_rate, "-O", min_overlap, "--discard-untrimmed"] + ca
            if paired:
                ca = ["-G", rev, "-A", rc(fwd)] + ca
        if paired:
            ca += ["-p", t2, s["r1"], s["r2"]]
        else:
            ca += [s["r1"]]
        run.cutadapt(ca, f"{'cutadapt' if trim else 'no-trim'} {sid}")
        with open(rep, encoding="utf-8") as fh:
            j = json.load(fh)
        raw, trimmed = j["read_counts"]["input"], j["read_counts"]["output"]

        if paired:
            mg = os.path.join(work, "merge", f"{sid}.fastq")
            e = run.vs(["--fastq_mergepairs", t1, "--reverse", t2, "--fastqout", mg,
                        "--fastq_minovlen", "12", "--fastq_maxdiffs", "10",
                        "--fastq_allowmergestagger", "--threads", threads], f"merge {sid}")
            m = re.search(r"(\d+)\s+Merged", e)
            merged = int(m.group(1)) if m else 0
        else:
            mg, merged = t1, trimmed

        ff = os.path.join(work, "filt", f"{sid}.fasta")
        e2 = run.vs(["--fastq_filter", mg, "--fastq_maxee", str(maxee), "--fastq_minlen", str(minlen),
                     "--fastq_maxns", "0", "--fastaout", ff, "--relabel", f"{sid}.",
                     "--threads", threads], f"filter {sid}")
        m2 = re.search(r"(\d+) sequences kept", e2)
        kept = int(m2.group(1)) if m2 else 0
        filt_files.append(ff)
        track.append(dict(sample_id=sid, reads_in=raw, reads_trimmed=trimmed, reads_merged=merged,
                          reads_filtered=kept))
        log.info("[%s] %s raw=%s trimmed=%s merged=%s filtered=%s", job_id, sid, raw, trimmed, merged, kept)

    # ---- denoise / chimera / table / classify (one marker per job) ----------
    A = lambda *p: os.path.join(work, "asv", *p)  # noqa: E731
    pooled = A("pooled.fasta")
    with open(pooled, "w", encoding="utf-8") as o:
        for ff in filt_files:
            with open(ff, encoding="utf-8") as fh:
                shutil.copyfileobj(fh, o)
    if os.path.getsize(pooled) == 0:
        raise RuntimeError("no reads survived primer trimming / merging / quality filtering")

    run.vs(["--derep_fulllength", pooled, "--sizeout", "--minuniquesize", "2",
            "--output", A("derep.fasta"), "--threads", threads], "derep")
    run.vs(["--cluster_unoise", A("derep.fasta"), "--minsize", str(minsize), "--unoise_alpha", "2.0",
            "--centroids", A("zotus_raw.fasta"), "--sizein", "--sizeout", "--threads", threads], "unoise3")
    asv_fa = os.path.join(job_dir, "asvs.fasta")
    run.vs(["--uchime3_denovo", A("zotus_raw.fasta"), "--nonchimeras", asv_fa,
            "--relabel", f"{marker}_ASV", "--sizein", "--xsize"], "uchime3")
    seqs = read_fasta(asv_fa)
    if not seqs:
        raise RuntimeError("UNOISE3/UCHIME3 produced zero ASVs (try a lower minsize or check primers)")
    tab_path = A("asv_table_raw.tsv")
    run.vs(["--usearch_global", pooled, "--db", asv_fa, "--id", "0.97", "--strand", "plus",
            "--otutabout", tab_path, "--threads", threads], "asv table")
    run.vs(["--sintax", asv_fa, "--db", db, "--tabbedout", A("sintax.tsv"), "--sintax_cutoff", str(cutoff),
            "--strand", "both", "--randseed", "42", "--threads", threads], "sintax")  # fixed seed => reproducible bootstrap

    tab = pd.read_csv(tab_path, sep="\t", index_col=0)
    tab.index.name = "asv_id"
    tab = tab.reindex(columns=[s["sample_id"] for s in samples], fill_value=0).fillna(0).astype(int)
    tax = parse_sintax(A("sintax.tsv")).reindex(tab.index)
    tax = tax.astype(object).where(pd.notna(tax), None)
    full = tax.copy()
    full["sequence"] = [seqs.get(i, "") for i in full.index]
    full["length"] = full["sequence"].str.len()
    full["reads_total"] = tab.sum(axis=1)
    full = full.join(tab).sort_values("reads_total", ascending=False)
    tab = tab.loc[full.index]
    is_fungal = (full["kingdom"] == "Fungi").fillna(False).astype(bool)
    fung = full[is_fungal]
    fung_tab = tab.loc[fung.index]

    # ---- tables -----------------------------------------------------------
    out_tab = os.path.join(job_dir, "asv_table.tsv")
    tab.to_csv(out_tab, sep="\t")
    out_tax = os.path.join(job_dir, "asv_taxonomy.tsv")
    full.to_csv(out_tax, sep="\t")

    comp: dict[str, dict[str, float]] = {}
    for rank, fname in (("phylum", "composition_phylum.tsv"), ("genus", "composition_genus.tsv")):
        if len(fung):
            g = fung_tab.groupby(fung[rank].fillna("Unresolved")).sum()
            tot = g.values.sum()
            pct = (100 * g.sum(axis=1) / tot).round(3).sort_values(ascending=False) if tot else pd.Series(dtype=float)
        else:
            pct = pd.Series(dtype=float)
        pct.index.name = rank
        pct.rename("pct_fungal_reads").to_frame().to_csv(os.path.join(job_dir, fname), sep="\t")
        comp[rank] = {str(k): float(v) for k, v in pct.items()}

    sample_rows = []
    for t in track:
        sid = t["sample_id"]
        col = tab[sid] if sid in tab.columns else pd.Series(dtype=int)
        fcol = fung_tab[sid] if sid in fung_tab.columns else pd.Series(dtype=int)
        mapped = int(col.sum())
        sample_rows.append(dict(
            sample_id=sid, reads_in=int(t["reads_in"]), reads_trimmed=int(t["reads_trimmed"]),
            reads_merged=int(t["reads_merged"]), reads_filtered=int(t["reads_filtered"]),
            reads_nonchimeric=mapped, n_asvs=int((col > 0).sum()), n_fungal_asvs=int((fcol > 0).sum()),
            shannon=shannon(fcol.values), shannon_all=shannon(col.values),
            pct_fungal=round(100 * float(fcol.sum()) / mapped, 2) if mapped else None,
        ))
    pd.DataFrame(sample_rows).to_csv(os.path.join(job_dir, "read_tracking.tsv"), sep="\t", index=False)
    pd.DataFrame(sample_rows)[["sample_id", "reads_nonchimeric", "n_asvs", "n_fungal_asvs", "shannon",
                               "shannon_all", "pct_fungal"]].to_csv(
        os.path.join(job_dir, "diversity_summary.tsv"), sep="\t", index=False)
    run.close()

    # ---- result JSON ------------------------------------------------------
    taxa_json = []
    for asv_id, r in full.head(TOP_TAXA_JSON).iterrows():
        taxa_json.append({
            "asv_id": asv_id, "reads_total": int(r["reads_total"]), "length": int(r["length"]),
            **{k: _none_if_nan(r[k]) for k in RANK_ORDER},
            "sintax_conf": _none_if_nan(r["sintax_conf"]), "assignment_level": r["assignment_level"] or "unassigned",
            "per_sample": {s: int(tab.at[asv_id, s]) for s in tab.columns if int(tab.at[asv_id, s]) > 0},
        })

    n_reads_in = int(sum(t["reads_in"] for t in track))
    n_reads_kept = int(tab.values.sum())
    species = sorted({s for s in fung["species"].dropna().astype(str) if s and not s.endswith("_sp")})
    try:
        from app import jobs as _jobs
        meta = _jobs.get_job(job_id)
        submitted_at = meta["submitted_at"] if meta else utcnow()
    except Exception:  # noqa: BLE001
        submitted_at = utcnow()
    completed_at = utcnow()
    location = str(params.get("location") or "")
    lat, lon = _flt(params.get("lat")), _flt(params.get("lon"))
    submitter = str(params.get("submitter") or "")
    substrate = str(params.get("substrate") or "")
    notes.append(f"Reference: {os.path.basename(db)}; VSEARCH {os.path.basename(vsearch)}; "
                 f"wall time {time.time() - t_start:.0f}s.")
    notes.append(config.CAVEAT_FOOTER)

    result = {
        "job_id": job_id, "job_type": "amplicon", "status": "done",
        "submitted_at": submitted_at, "completed_at": completed_at,
        "params": {"marker": marker, "fwd_primer": fwd, "rev_primer": rev, "paired": paired, "trim_primers": trim,
                   "min_merge_len": minlen, "max_ee": maxee, "minsize": minsize, "sintax_cutoff": cutoff,
                   "location": location, "lat": lat, "lon": lon, "submitter": submitter, "substrate": substrate,
                   "input_files": sorted(os.path.basename(p) for s in samples for p in (s["r1"], s["r2"]) if p)},
        "summary": {"n_samples": len(samples), "n_reads_in": n_reads_in, "n_reads_kept": n_reads_kept,
                    "n_asvs": int(len(full)), "n_fungal_asvs": int(len(fung)), "n_species": len(species),
                    "marker": marker, "location": location, "lat": lat, "lon": lon},
        "samples": sample_rows,
        "taxa": taxa_json,
        "composition": comp,
        "files": {},
        "notes": notes,
        "citations": config.CITATIONS,
    }

    # ---- upload files -----------------------------------------------------
    folder_url = storage.create_job_folder(job_id)
    for name in ("asv_table.tsv", "asv_taxonomy.tsv", "asvs.fasta", "read_tracking.tsv", "diversity_summary.tsv",
                 "composition_phylum.tsv", "composition_genus.tsv", "pipeline_log.txt"):
        p = os.path.join(job_dir, name)
        if os.path.exists(p):
            result["files"][name] = storage.upload_result_file(job_id, p)
    # result.json is uploaded here so its URL can go into the ledger; the worker
    # re-uploads the final copy (storage de-duplicates by job_id + filename).
    rp = os.path.join(job_dir, "result.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1, default=str)
    result["files"]["result.json"] = storage.upload_result_file(job_id, rp)
    result["result_folder_url"] = folder_url

    # ---- ledger -----------------------------------------------------------
    storage.append_ledger("jobs", [[
        job_id, "amplicon", submitted_at, completed_at, "done", submitter, marker, len(samples),
        n_reads_in, n_reads_kept, int(len(full)), int(len(fung)), len(species), location, lat, lon,
        folder_url, result["files"]["result.json"], " | ".join(notes[:2]),
    ]])
    storage.append_ledger("samples", [[
        job_id, r["sample_id"], r["reads_in"], r["reads_merged"], r["reads_filtered"], r["reads_nonchimeric"],
        r["n_asvs"], r["shannon"], r["pct_fungal"], location, lat, lon, substrate,
    ] for r in sample_rows])
    taxa_rows = []
    for asv_id, r in full.head(TOP_TAXA_LEDGER).iterrows():
        for sid in tab.columns:
            n = int(tab.at[asv_id, sid])
            if n > 0:
                taxa_rows.append([job_id, sid, asv_id, n] + [_none_if_nan(r[k]) for k in RANK_ORDER]
                                 + [_none_if_nan(r["sintax_conf"])])
    storage.append_ledger("taxa", taxa_rows)
    return result
