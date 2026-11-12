"""
Barcoding service: single-sequence (Sanger / FASTA) identification of fungal
ITS/LSU/SSU sequences against the UNITE SINTAX-formatted reference database.

Pipeline per job
  1. read inputs   : FASTA (any number of records) or .ab1 Sanger traces
                     (Biopython SeqIO 'abi'); .ab1 and FASTQ are quality-trimmed
                     with a Mott-style algorithm at Q20, minimum 150 bp kept.
  2. orient        : both strands of every query are searched; the strand whose
                     best hit has the higher identity is kept.
  3. usearch_global: VSEARCH --usearch_global --id 0.7 --maxaccepts 50
                     --maxrejects 200, top 10 hits by identity are reported.
  4. sintax        : VSEARCH --sintax (cutoff 0.8) for rank-wise confidence.
  5. uchime_ref    : VSEARCH --uchime_ref against UNITE, chimera flag.
  6. assignment    : identity / coverage rule (constants below).
  7. outputs       : report.html + hits.tsv in job_dir, uploaded through the
                     Storage object; ledger rows 'jobs' and 'barcoding_hits'.

OS-agnostic: the VSEARCH binary is resolved from config.VSEARCH_BIN (or the
VSEARCH_BIN environment variable), never hard-coded.

Citations: VSEARCH (Rognes et al. 2016, PeerJ 4:e2584); SINTAX (Edgar 2016,
bioRxiv 074161); UNITE (Abarenkov et al. 2024, Nucleic Acids Res 52:D791).
"""
from __future__ import annotations

import csv
import datetime as _dt
import gzip
import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

# --------------------------------------------------------------------------
# Thresholds (module constants).
# Species-level calls follow the UNITE species-hypothesis (SH) convention: the
# UNITE SH system clusters reference sequences at 98.5% (the "1.5%" SH
# distance threshold adopted as UNITE's default; Kõljalg et al. 2013 Mol Ecol 22(21):5271-5277 doi:10.1111/mec.12481,
# Abarenkov et al. 2024). Between 97% and 98.5% the ITS identity is compatible
# with congeneric species, so only genus is reported; 90-97% supports a
# family/order placement only; below 90% no confident assignment is made.
# --------------------------------------------------------------------------
SPECIES_MIN_IDENTITY = 98.5   # % identity, UNITE SH 98.5% threshold -> species
GENUS_MIN_IDENTITY = 97.0     # % identity -> genus (species uncertain)
FAMILY_MIN_IDENTITY = 90.0    # % identity -> family/order level only
MIN_QUERY_COVERAGE = 90.0     # % of query aligned required for species level
SEARCH_MIN_IDENTITY = 0.70    # VSEARCH --id for usearch_global (fraction)
SEARCH_MAXACCEPTS = 50
SEARCH_MAXREJECTS = 200
TOP_N_HITS = 10
SINTAX_CUTOFF = 0.8
TRIM_QUALITY = 20             # Phred threshold for Mott-style trimming
TRIM_WINDOW = 10              # sliding-window size for the Q20 check
MIN_TRIMMED_LENGTH = 150      # bp; shorter trimmed traces are rejected

LEVEL_SPECIES = "species"
LEVEL_GENUS = "genus (species uncertain)"
LEVEL_FAMILY = "family/order level only"
LEVEL_NONE = "no confident assignment"
LEVEL_CHIMERA = "chimera suspected (not assigned)"

RANK_CODES = {"d": "kingdom", "k": "kingdom", "p": "phylum", "c": "class",
              "o": "order", "f": "family", "g": "genus", "s": "species"}
RANK_ORDER = ["kingdom", "phylum", "class", "order", "family", "genus", "species"]

FASTA_EXT = {".fa", ".fasta", ".fna", ".fas", ".txt", ".seq"}
ABI_EXT = {".ab1", ".abi"}
FASTQ_EXT = {".fq", ".fastq"}

CAVEAT = ("Results are automated and provisional; no negative controls are "
          "enforced; verify taxonomic assignments before publication")
CITATIONS = [
    "cutadapt: Martin M (2011) EMBnet.journal 17(1):10-12, doi:10.14806/ej.17.1.200",
    "VSEARCH: Rognes T, Flouri T, Nichols B, Quince C, Mahé F (2016) PeerJ 4:e2584, doi:10.7717/peerj.2584",
    "UNITE: Abarenkov K et al. (2024) Nucleic Acids Research 52(D1):D791-D797, doi:10.1093/nar/gkad1039",
    "SINTAX: Edgar RC (2016) bioRxiv 074161, doi:10.1101/074161",
    "Plotly (MIT licence)",
]

_COMP = str.maketrans("ACGTRYKMBVDHNacgtrykmbvdhn", "TGCAYRMKVBHDNtgcayrmkvbhdn")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def revcomp(seq: str) -> str:
    return seq.translate(_COMP)[::-1]


# --------------------------------------------------------------------------
# Configuration helpers (code against the app.config contract, but work
# standalone from environment variables so tests do not need the other track).
# --------------------------------------------------------------------------
def _cfg(name: str, default):
    try:
        from app import config as _config  # type: ignore
        val = getattr(_config, name, None)
        if val not in (None, ""):
            return val
    except Exception:
        pass
    return os.environ.get(name, default)


def resolve_vsearch(vsearch_bin: str | None = None) -> str:
    """Return an executable path for VSEARCH from config/env; raise if absent."""
    cand = vsearch_bin or _cfg("VSEARCH_BIN", "vsearch")
    found = shutil.which(str(cand))
    if found is None and Path(str(cand)).is_file():
        found = str(cand)
    if found is None:
        raise FileNotFoundError(
            f"VSEARCH binary not found (VSEARCH_BIN={cand!r}); install vsearch or set VSEARCH_BIN")
    return found


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def ledger_headers() -> dict:
    """Column order for the ledger sheets, read from platform_config.json."""
    for p in (_repo_root() / "platform_config.json", Path("platform_config.json")):
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))["ledger_headers"]
            except Exception:
                pass
    return {
        "jobs": ["job_id", "job_type", "submitted_at", "completed_at", "status", "submitter",
                 "marker", "n_samples", "n_reads_in", "n_reads_kept", "n_asvs", "n_fungal_asvs",
                 "n_species", "location", "lat", "lon", "result_folder_url", "result_json_url", "notes"],
        "barcoding_hits": ["job_id", "query_id", "query_length", "submitted_at", "best_hit_accession",
                           "best_hit_species", "best_hit_sh", "identity_pct", "coverage_pct",
                           "sintax_genus", "sintax_species", "sintax_conf", "assignment_level",
                           "chimera_flag", "report_url"],
    }


# --------------------------------------------------------------------------
# Input handling
# --------------------------------------------------------------------------
def mott_trim(quals: list[int], cutoff: int = TRIM_QUALITY, window: int = TRIM_WINDOW) -> tuple[int, int]:
    """Mott-style trimming: find the contiguous segment maximising the sum of
    (Q - cutoff), then tighten the ends with a sliding window whose mean
    quality must reach `cutoff`. Returns (start, end) half-open; (0, 0) if
    nothing passes."""
    n = len(quals)
    if n == 0:
        return 0, 0
    best_sum, best_start, best_end = 0.0, 0, 0
    run_sum, run_start = 0.0, 0
    for i, q in enumerate(quals):
        run_sum += q - cutoff
        if run_sum <= 0:
            run_sum, run_start = 0.0, i + 1
        elif run_sum > best_sum:
            best_sum, best_start, best_end = run_sum, run_start, i + 1
    if best_end - best_start == 0:
        return 0, 0
    s, e = best_start, best_end
    w = min(window, e - s)
    while e - s >= w and sum(quals[s:s + w]) / w < cutoff:
        s += 1
    while e - s >= w and sum(quals[e - w:e]) / w < cutoff:
        e -= 1
    return (s, e) if e > s else (0, 0)


def _clean_id(raw: str, used: set) -> str:
    rid = re.sub(r"[^A-Za-z0-9._:-]+", "_", raw.strip().split()[0] if raw.strip() else "query").strip("_") or "query"
    base, k = rid, 1
    while rid in used:
        k += 1
        rid = f"{base}_{k}"
    used.add(rid)
    return rid


def read_query_sequences(paths: Iterable[str | os.PathLike], pasted_text: str | None = None) -> list[dict]:
    """Return [{'query_id','sequence','raw_length','trimmed_length','source','rejected':str|None}]."""
    from Bio import SeqIO
    out: list[dict] = []
    used: set = set()

    def add(raw_id, seq, src, quals=None):
        seq = re.sub(r"[^A-Za-z]", "", str(seq)).upper()
        raw_len = len(seq)
        rejected = None
        if quals is not None:
            s, e = mott_trim(list(quals))
            seq = seq[s:e]
            if len(seq) < MIN_TRIMMED_LENGTH:
                rejected = f"trimmed length {len(seq)} bp < {MIN_TRIMMED_LENGTH} bp after Q{TRIM_QUALITY} Mott trimming"
        elif raw_len < MIN_TRIMMED_LENGTH:
            rejected = f"sequence length {raw_len} bp < {MIN_TRIMMED_LENGTH} bp"
        if raw_len == 0:
            rejected = "empty sequence"
        out.append({"query_id": _clean_id(raw_id, used), "sequence": seq, "raw_length": raw_len,
                    "trimmed_length": len(seq), "source": src, "rejected": rejected})

    for p in paths:
        p = Path(p)
        ext = p.suffix.lower()
        if ext in ABI_EXT:
            rec = SeqIO.read(str(p), "abi")
            add(rec.id if rec.id and rec.id != "<unknown id>" else p.stem, rec.seq, p.name,
                rec.letter_annotations.get("phred_quality"))
        elif ext in FASTQ_EXT or ext == ".gz" and p.name.lower().endswith((".fq.gz", ".fastq.gz")):
            opener = gzip.open if ext == ".gz" else open
            with opener(str(p), "rt") as fh:
                for rec in SeqIO.parse(fh, "fastq"):
                    add(rec.id, rec.seq, p.name, rec.letter_annotations.get("phred_quality"))
        else:  # FASTA or anything else: try FASTA, fall back to raw sequence text
            text = p.read_text(encoding="utf-8", errors="replace")
            for rid, seq in _parse_fasta_text(text, default_prefix=p.stem):
                add(rid, seq, p.name)
    if pasted_text and pasted_text.strip():
        for rid, seq in _parse_fasta_text(pasted_text, default_prefix="pasted"):
            add(rid, seq, "pasted")
    return out


def _parse_fasta_text(text: str, default_prefix: str = "seq") -> list[tuple[str, str]]:
    text = text.replace("\r", "")
    recs: list[tuple[str, str]] = []
    if ">" not in text:
        seq = re.sub(r"[^A-Za-z]", "", text)
        return [(default_prefix, seq)] if seq else []
    cur_id, buf = None, []
    for line in text.split("\n"):
        if line.startswith(">"):
            if cur_id is not None:
                recs.append((cur_id, "".join(buf)))
            cur_id, buf = (line[1:].strip() or f"{default_prefix}_{len(recs) + 1}"), []
        elif cur_id is not None:
            buf.append(line.strip())
    if cur_id is not None:
        recs.append((cur_id, "".join(buf)))
    return recs


# --------------------------------------------------------------------------
# UNITE SINTAX header parsing
# --------------------------------------------------------------------------
_SH_RE = re.compile(r"(SH\d+\.\d+FU)")
_ACC_RE = re.compile(r"\b([A-Z]{1,2}\d{5,8}(?:\.\d+)?|[A-Z]{2}_\d+(?:\.\d+)?)\b")


def parse_unite_label(label: str) -> dict:
    """Parse 'SH1227328.10FU;tax=d:Fungi,p:...,s:Genus_species;' (or labels of
    the form 'ACCESSION|SH...;tax=...') into accession, sh, species and ranks."""
    label = label.lstrip(">").strip()
    head, _, tax = label.partition(";tax=")
    head_tokens = [t for t in re.split(r"[|;\s]+", head) if t]
    sh_m = _SH_RE.search(head)
    sh = sh_m.group(1) if sh_m else ""
    acc = ""
    for t in head_tokens:
        if _SH_RE.fullmatch(t):
            continue
        if _ACC_RE.fullmatch(t):
            acc = t
            break
    if not acc:
        acc = sh or (head_tokens[0] if head_tokens else label)
    ranks = {}
    for part in tax.strip(";").split(","):
        if ":" in part:
            code, _, name = part.partition(":")
            rank = RANK_CODES.get(code.strip().lower())
            if rank:
                ranks[rank] = re.sub(r"\(\d*\.?\d+\)$", "", name.strip()).replace("_", " ")
    species = ranks.get("species", "")
    g = ranks.get("genus", "")
    if not species:
        species = f"{g} sp." if g else "unidentified"
    elif re.fullmatch(r"\S+ sp\.?", species):  # 'Genus_sp' -> 'Genus sp.'
        species = species.rstrip(".") + "."
    return {"accession": acc, "sh": sh, "species": species, "ranks": ranks}


# --------------------------------------------------------------------------
# VSEARCH wrappers
# --------------------------------------------------------------------------
def _run(cmd: list[str], log_path: Path | None = None) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if log_path is not None:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write("$ " + " ".join(cmd) + "\n" + res.stdout + res.stderr + "\n")
    if res.returncode != 0:
        raise RuntimeError(f"VSEARCH failed ({res.returncode}): {' '.join(cmd)}\n{res.stderr[-2000:]}")
    return res


def _write_fasta(path: Path, records: Iterable[tuple[str, str]]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for rid, seq in records:
            fh.write(f">{rid}\n{seq}\n")


def _parse_userout(path: Path) -> dict[str, list[dict]]:
    """userfields query+target+id+alnlen+qcov+tcov -> {query: [hit,...]} sorted by identity desc."""
    hits: dict[str, list[dict]] = {}
    if not path.is_file():
        return hits
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if len(f) < 6 or f[1] == "*":
                continue
            lab = parse_unite_label(f[1])
            hits.setdefault(f[0], []).append({
                "accession": lab["accession"], "species": lab["species"], "sh": lab["sh"],
                "identity_pct": round(float(f[2]), 2), "aln_len": int(f[3]),
                "coverage_pct": round(float(f[4]), 2), "target_coverage_pct": round(float(f[5]), 2),
                "ranks": lab["ranks"], "target_label": f[1]})
    for q in hits:
        hits[q].sort(key=lambda h: (-h["identity_pct"], -h["coverage_pct"], h["accession"]))
    return hits


def _parse_sintax(path: Path) -> dict[str, dict]:
    """tabbedout -> {query: {rank: [name, conf]}} using the full (uncut) column
    for confidences; names below SINTAX_CUTOFF are still reported with their
    confidence so the caller/report can show them greyed out."""
    out: dict[str, dict] = {}
    if not path.is_file():
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if len(f) < 2:
                continue
            ranks: dict = {}
            for part in f[1].split(","):
                m = re.match(r"([a-z]):(.+)\((\d*\.?\d+)\)$", part.strip())
                if m and RANK_CODES.get(m.group(1)):
                    ranks[RANK_CODES[m.group(1)]] = [m.group(2).replace("_", " "), float(m.group(3))]
            out[f[0]] = ranks
    return out


def _parse_uchimeout(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.is_file():
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if len(f) < 18:
                continue
            out[f[1].split(";")[0]] = {"score": float(f[0]) if f[0] not in ("", "*") else 0.0,
                                      "parent_a": f[2].split(";")[0], "parent_b": f[3].split(";")[0],
                                      "verdict": f[17].strip()}
    return out


def assignment_level(identity_pct: float | None, coverage_pct: float | None) -> tuple[str, str | None]:
    """Apply the identity / coverage rule. Returns (level, note)."""
    if identity_pct is None:
        return LEVEL_NONE, "no hit at >= 70% identity"
    note = None
    if identity_pct >= SPECIES_MIN_IDENTITY:
        level = LEVEL_SPECIES
    elif identity_pct >= GENUS_MIN_IDENTITY:
        level = LEVEL_GENUS
    elif identity_pct >= FAMILY_MIN_IDENTITY:
        level = LEVEL_FAMILY
    else:
        level = LEVEL_NONE
    if coverage_pct is not None and coverage_pct < MIN_QUERY_COVERAGE and level == LEVEL_SPECIES:
        level = LEVEL_GENUS
        note = f"query coverage {coverage_pct:.1f}% < {MIN_QUERY_COVERAGE:.0f}%: species call downgraded"
    return level, note


def barcode_sequences(fasta_path: str | os.PathLike, workdir: str | os.PathLike, db_path: str | os.PathLike,
                      vsearch_bin: str | None = None, threads: int = 2) -> list[dict]:
    """Pure function: identify every sequence in `fasta_path` against the UNITE
    SINTAX database. Intermediate files are written to `workdir`. Returns the
    `barcoding` list of the result schema (one dict per query)."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    vsearch = resolve_vsearch(vsearch_bin)
    db_path = str(db_path)
    if not Path(db_path).is_file():
        raise FileNotFoundError(f"UNITE SINTAX database not found: {db_path}")
    log = workdir / "vsearch.log"
    thr = str(max(1, int(threads)))

    queries = [(rid, seq) for rid, seq in _parse_fasta_text(Path(fasta_path).read_text(encoding="utf-8"))]
    queries = [(rid.split()[0] if rid.split() else "query", re.sub(r"[^A-Za-z]", "", seq).upper())
               for rid, seq in queries if seq.strip()]
    if not queries:
        return []
    # internal ids avoid label-parsing issues inside VSEARCH outputs
    internal = {f"q{i + 1}": (rid, seq) for i, (rid, seq) in enumerate(queries)}

    # 1. orientation: search both strands, keep the strand with the better best hit
    both = workdir / "queries_both_strands.fasta"
    _write_fasta(both, [(f"{k}__fwd", s) for k, (_, s) in internal.items()] +
                       [(f"{k}__rev", revcomp(s)) for k, (_, s) in internal.items()])
    both_out = workdir / "orient_hits.tsv"
    _run([vsearch, "--usearch_global", str(both), "--db", db_path, "--id", str(SEARCH_MIN_IDENTITY),
          "--strand", "plus", "--maxaccepts", str(SEARCH_MAXACCEPTS), "--maxrejects", str(SEARCH_MAXREJECTS),
          "--userout", str(both_out), "--userfields", "query+target+id+alnlen+qcov+tcov",
          "--threads", thr, "--quiet"], log)
    both_hits = _parse_userout(both_out)

    oriented: list[tuple[str, str]] = []
    strand_of: dict[str, str] = {}
    hits_of: dict[str, list[dict]] = {}
    for k, (_, s) in internal.items():
        fwd, rev = both_hits.get(f"{k}__fwd", []), both_hits.get(f"{k}__rev", [])
        f_best = (fwd[0]["identity_pct"], fwd[0]["coverage_pct"]) if fwd else (-1, -1)
        r_best = (rev[0]["identity_pct"], rev[0]["coverage_pct"]) if rev else (-1, -1)
        if r_best > f_best:
            strand_of[k], hits_of[k] = "-", rev
            oriented.append((k, revcomp(s)))
        else:
            strand_of[k], hits_of[k] = "+", fwd
            oriented.append((k, s))
    ofa = workdir / "queries_oriented.fasta"
    _write_fasta(ofa, oriented)

    # 2. SINTAX on the oriented queries
    sintax_out = workdir / "sintax.tsv"
    _run([vsearch, "--sintax", str(ofa), "--db", db_path, "--tabbedout", str(sintax_out),
          "--sintax_cutoff", str(SINTAX_CUTOFF), "--strand", "plus", "--threads", thr, "--quiet"], log)
    sintax = _parse_sintax(sintax_out)

    # 3. reference-based chimera check
    uch_out = workdir / "uchime_ref.tsv"
    _run([vsearch, "--uchime_ref", str(ofa), "--db", db_path, "--strand", "plus",
          "--uchimeout", str(uch_out), "--threads", thr, "--quiet"], log)
    chim = _parse_uchimeout(uch_out)

    results: list[dict] = []
    for k, (rid, s) in internal.items():
        hits = hits_of.get(k, [])
        best = hits[0] if hits else None
        level, note = assignment_level(best["identity_pct"] if best else None,
                                       best["coverage_pct"] if best else None)
        notes = [note] if note else []
        ch = chim.get(k, {})
        chimera_flag = ch.get("verdict") == "Y"
        if chimera_flag:
            notes.append(f"uchime_ref: chimera of {ch.get('parent_a', '?').split(';')[0]} and "
                         f"{ch.get('parent_b', '?').split(';')[0]} (score {ch.get('score', 0):.2f})")
            level = LEVEL_CHIMERA
        elif ch.get("verdict") == "?":
            notes.append("uchime_ref: borderline chimera signal ('?')")
        sx = sintax.get(k, {})
        if best and sx.get("genus") and sx["genus"][1] >= SINTAX_CUTOFF and \
                best["ranks"].get("genus") and sx["genus"][0] != best["ranks"]["genus"]:
            notes.append(f"SINTAX genus ({sx['genus'][0]}, {sx['genus'][1]:.2f}) disagrees with best hit genus "
                         f"({best['ranks']['genus']})")
        if strand_of[k] == "-":
            notes.append("query was reverse-complemented (minus strand matched better)")
        results.append({
            "query_id": rid,
            "query_length": len(s),
            "strand": strand_of[k],
            "best_hit_accession": best["accession"] if best else None,
            "best_hit_species": best["species"] if best else None,
            "best_hit_sh": best["sh"] if best else None,
            "best_hit_taxonomy": best["ranks"] if best else {},
            "identity_pct": best["identity_pct"] if best else None,
            "coverage_pct": best["coverage_pct"] if best else None,
            "sintax": {r: sx[r] for r in RANK_ORDER if r in sx},
            "assignment_level": level,
            "chimera_flag": chimera_flag,
            "hits": [{"accession": h["accession"], "species": h["species"], "sh": h["sh"],
                      "identity_pct": h["identity_pct"], "coverage_pct": h["coverage_pct"]}
                     for h in hits[:TOP_N_HITS]],
            "notes": notes,
        })
    return results


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------
_BADGE = {
    LEVEL_SPECIES: ("#1b7f3b", "species"),
    LEVEL_GENUS: ("#b8860b", "genus"),
    LEVEL_FAMILY: ("#d2691e", "family/order"),
    LEVEL_NONE: ("#6c757d", "unassigned"),
    LEVEL_CHIMERA: ("#b22222", "chimera"),
}


def write_hits_tsv(path: str | os.PathLike, barcoding: list[dict], job_id: str) -> None:
    cols = ["job_id", "query_id", "query_length", "strand", "rank", "accession", "sh", "species",
            "identity_pct", "coverage_pct", "assignment_level", "chimera_flag",
            "sintax_genus", "sintax_genus_conf", "sintax_species", "sintax_species_conf"]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(cols)
        for q in barcoding:
            sg = q["sintax"].get("genus", ["", ""])
            ss = q["sintax"].get("species", ["", ""])
            if not q["hits"]:
                w.writerow([job_id, q["query_id"], q["query_length"], q["strand"], 0, "", "", "", "", "",
                            q["assignment_level"], q["chimera_flag"], sg[0], sg[1], ss[0], ss[1]])
            for i, h in enumerate(q["hits"], 1):
                w.writerow([job_id, q["query_id"], q["query_length"], q["strand"], i, h["accession"], h["sh"],
                            h["species"], h["identity_pct"], h["coverage_pct"], q["assignment_level"],
                            q["chimera_flag"], sg[0], sg[1], ss[0], ss[1]])


def render_report_html(result: dict) -> str:
    e = html.escape
    rows = []
    for q in result.get("barcoding", []):
        colour, short = _BADGE.get(q["assignment_level"], ("#6c757d", q["assignment_level"]))
        sx_parts = []
        for r in RANK_ORDER:
            if r in q["sintax"]:
                name, conf = q["sintax"][r]
                style = "" if conf >= SINTAX_CUTOFF else ' style="color:#999"'
                sx_parts.append(f"<span{style}>{e(r[0])}: {e(name)} ({conf:.2f})</span>")
        hit_rows = "".join(
            f"<tr><td>{i}</td><td>{e(str(h['accession']))}</td><td>{e(str(h['sh']))}</td>"
            f"<td><i>{e(str(h['species']))}</i></td><td>{h['identity_pct']:.1f}</td><td>{h['coverage_pct']:.1f}</td></tr>"
            for i, h in enumerate(q["hits"], 1)) or "<tr><td colspan=6>no hits at &ge;70% identity</td></tr>"
        notes = "".join(f"<li>{e(n)}</li>" for n in q.get("notes", []))
        ident = f"{q['identity_pct']:.1f}%" if q["identity_pct"] is not None else "–"
        cov = f"{q['coverage_pct']:.1f}%" if q["coverage_pct"] is not None else "–"
        chim = '<span class="badge" style="background:#b22222">chimera</span>' if q["chimera_flag"] else "no"
        rows.append(f"""
<section class="query">
<h2>{e(q['query_id'])} <span class="badge" style="background:{colour}">{e(short)}</span></h2>
<table class="kv">
<tr><th>Assignment level</th><td>{e(q['assignment_level'])}</td></tr>
<tr><th>Best hit</th><td><i>{e(str(q['best_hit_species']))}</i> — {e(str(q['best_hit_sh']))} ({e(str(q['best_hit_accession']))})</td></tr>
<tr><th>Identity / query coverage</th><td>{ident} / {cov}</td></tr>
<tr><th>Query length (strand)</th><td>{q['query_length']} bp ({e(q.get('strand', '+'))})</td></tr>
<tr><th>Chimera flag (uchime_ref)</th><td>{chim}</td></tr>
<tr><th>SINTAX (cutoff {SINTAX_CUTOFF})</th><td>{' &middot; '.join(sx_parts) or '–'}</td></tr>
</table>
<details open><summary>Top {len(q['hits'])} hits (UNITE)</summary>
<table class="hits"><thead><tr><th>#</th><th>Accession</th><th>SH</th><th>Species</th><th>Identity %</th><th>Query cov %</th></tr></thead>
<tbody>{hit_rows}</tbody></table></details>
{('<ul class="notes">' + notes + '</ul>') if notes else ''}
</section>""")
    s = result.get("summary", {})
    cites = "".join(f"<li>{e(c)}</li>" for c in CITATIONS)
    job_notes = "".join(f"<li>{e(n)}</li>" for n in result.get("notes", []))
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Barcoding report {e(result.get('job_id', ''))}</title>
<style>
body{{font-family:system-ui,Segoe UI,Helvetica,Arial,sans-serif;margin:1.5rem;color:#222;max-width:1100px}}
h1{{font-size:1.4rem}} h2{{font-size:1.1rem;margin:.2rem 0 .6rem}}
.badge{{display:inline-block;color:#fff;border-radius:.8rem;padding:.1rem .6rem;font-size:.8rem;vertical-align:middle}}
table{{border-collapse:collapse;margin:.4rem 0}} th,td{{border:1px solid #ddd;padding:.3rem .5rem;text-align:left;font-size:.9rem}}
table.kv th{{width:14rem;background:#f6f6f6}} table.hits th{{background:#f0f0f0}}
section.query{{border:1px solid #e3e3e3;border-radius:6px;padding:.8rem 1rem;margin:1rem 0;background:#fcfcfc}}
.legend span{{margin-right:.6rem}} footer{{margin-top:2rem;font-size:.8rem;color:#555;border-top:1px solid #ddd;padding-top:.6rem}}
ul.notes{{font-size:.85rem;color:#844}}
</style></head><body>
<h1>Fungal DNA barcoding report — job {e(result.get('job_id', ''))}</h1>
<p>Submitted {e(str(result.get('submitted_at', '')))} · completed {e(str(result.get('completed_at', '')))} ·
submitter {e(str(result.get('params', {}).get('submitter', '') or 'anonymous'))} ·
{s.get('n_reads_in', 0)} sequence(s) submitted, {s.get('n_reads_kept', 0)} analysed, {s.get('n_species', 0)} species-level assignment(s).</p>
<p class="legend">Thresholds (best-hit identity to UNITE): <span class="badge" style="background:#1b7f3b">species</span> &ge;{SPECIES_MIN_IDENTITY}% and query coverage &ge;{MIN_QUERY_COVERAGE:.0f}% ·
<span class="badge" style="background:#b8860b">genus</span> {GENUS_MIN_IDENTITY}–{SPECIES_MIN_IDENTITY}% ·
<span class="badge" style="background:#d2691e">family/order</span> {FAMILY_MIN_IDENTITY:.0f}–{GENUS_MIN_IDENTITY}% ·
<span class="badge" style="background:#6c757d">unassigned</span> &lt;{FAMILY_MIN_IDENTITY:.0f}% ·
<span class="badge" style="background:#b22222">chimera</span> flagged by uchime_ref. The {SPECIES_MIN_IDENTITY}% species threshold follows the UNITE species-hypothesis convention.</p>
{''.join(rows) or '<p>No analysable sequences.</p>'}
{('<h3>Job notes</h3><ul>' + job_notes + '</ul>') if job_notes else ''}
<footer><p><b>{e(CAVEAT)}.</b></p><p>Methods: VSEARCH --usearch_global (--id {SEARCH_MIN_IDENTITY}, --maxaccepts {SEARCH_MAXACCEPTS}, --maxrejects {SEARCH_MAXREJECTS}),
--sintax (cutoff {SINTAX_CUTOFF}) and --uchime_ref against the UNITE v10 SINTAX-formatted database.</p><ul>{cites}</ul></footer>
</body></html>"""


# --------------------------------------------------------------------------
# Job runner (contract: run_job(job_id, job_dir, params, storage) -> result dict)
# --------------------------------------------------------------------------
def _collect_input_paths(job_dir: Path, params: dict) -> list[Path]:
    cands: list[Path] = []
    for p in params.get("input_files", []) or []:
        if Path(p).is_file():
            cands.append(Path(p))
    for d in [params.get("input_dir"), job_dir / "input"]:
        if d and Path(d).is_dir():
            for p in sorted(Path(d).iterdir()):
                if p.is_file() and p not in cands:
                    cands.append(p)
    return cands


def run_job(job_id: str, job_dir: str, params: dict, storage) -> dict:
    job_dir_p = Path(job_dir)
    job_dir_p.mkdir(parents=True, exist_ok=True)
    submitted_at = params.get("submitted_at") or _now()
    notes: list[str] = []
    db_path = params.get("db_path") or _cfg("UNITE_SINTAX_DB", "/data/ref/unite_sintax.fasta.gz")
    threads = int(params.get("threads") or _cfg("VSEARCH_THREADS", 2))

    # inputs -> job_dir/input for provenance
    input_dir = job_dir_p / "input"
    input_dir.mkdir(exist_ok=True)
    paths = _collect_input_paths(job_dir_p, params)
    for p in paths:
        if p.parent.resolve() != input_dir.resolve():
            shutil.copy2(p, input_dir / p.name)
    paths = sorted(p for p in input_dir.iterdir() if p.is_file() and p.name != "pasted_sequences.fasta")
    pasted = params.get("sequences") or ""
    if pasted.strip():
        (input_dir / "pasted_sequences.fasta").write_text(
            pasted if pasted.lstrip().startswith(">") else f">pasted_1\n{pasted}\n", encoding="utf-8")
    queries = read_query_sequences(paths, pasted)
    accepted = [q for q in queries if not q["rejected"]]
    for q in queries:
        if q["rejected"]:
            notes.append(f"{q['query_id']}: rejected ({q['rejected']})")
        elif q["trimmed_length"] != q["raw_length"]:
            notes.append(f"{q['query_id']}: quality-trimmed {q['raw_length']} -> {q['trimmed_length']} bp")

    workdir = job_dir_p / "work"
    qfa = workdir / "queries.fasta"
    workdir.mkdir(exist_ok=True)
    _write_fasta(qfa, [(q["query_id"], q["sequence"]) for q in accepted])
    barcoding = barcode_sequences(qfa, workdir, db_path, params.get("vsearch_bin"), threads) if accepted else []
    meta = {q["query_id"]: q for q in queries}
    for b in barcoding:
        m = meta.get(b["query_id"], {})
        b["raw_length"] = m.get("raw_length", b["query_length"])
        b["trimmed_length"] = m.get("trimmed_length", b["query_length"])
        b["input_file"] = m.get("source", "")
    for q in queries:  # rejected queries still appear in the table, unassigned
        if q["rejected"]:
            barcoding.append({"query_id": q["query_id"], "query_length": q["trimmed_length"], "strand": "+",
                              "best_hit_accession": None, "best_hit_species": None, "best_hit_sh": None,
                              "best_hit_taxonomy": {}, "identity_pct": None, "coverage_pct": None, "sintax": {},
                              "assignment_level": LEVEL_NONE, "chimera_flag": False, "hits": [],
                              "notes": [q["rejected"]], "raw_length": q["raw_length"],
                              "trimmed_length": q["trimmed_length"], "input_file": q["source"]})

    completed_at = _now()
    species_level = {b["best_hit_species"] for b in barcoding if b["assignment_level"] == LEVEL_SPECIES}
    n_fungal = sum(1 for b in barcoding if (b.get("best_hit_taxonomy") or {}).get("kingdom") == "Fungi")
    result = {
        "job_id": job_id, "job_type": "barcode", "status": "done",
        "submitted_at": submitted_at, "completed_at": completed_at,
        "params": {k: v for k, v in params.items() if k != "sequences"} | {"n_pasted_chars": len(pasted)},
        "summary": {"n_samples": len(queries), "n_reads_in": len(queries), "n_reads_kept": len(accepted),
                    "n_asvs": len(accepted), "n_fungal_asvs": n_fungal, "n_species": len(species_level),
                    "marker": params.get("marker", "ITS"), "location": params.get("location"),
                    "lat": params.get("lat"), "lon": params.get("lon")},
        "samples": [], "taxa": [], "composition": {},
        "barcoding": barcoding, "files": {}, "notes": notes,
    }

    # files
    hits_tsv = job_dir_p / "hits.tsv"
    write_hits_tsv(hits_tsv, barcoding, job_id)
    report = job_dir_p / "report.html"
    report.write_text(render_report_html(result), encoding="utf-8")
    to_upload = [report, hits_tsv]
    ofa = workdir / "queries_oriented.fasta"
    if ofa.is_file():
        shutil.copy2(ofa, job_dir_p / "queries_oriented.fasta")
        to_upload.append(job_dir_p / "queries_oriented.fasta")
    folder_url = ""
    try:
        if hasattr(storage, "create_job_folder"):
            folder_url = storage.create_job_folder(job_id) or ""
    except Exception as ex:  # folder creation is not critical
        notes.append(f"create_job_folder failed: {ex}")
    for f in to_upload:
        try:
            result["files"][f.name] = storage.upload_result_file(job_id, str(f))
        except Exception as ex:
            notes.append(f"upload failed for {f.name}: {ex}")
            result["files"][f.name] = ""
    report_url = result["files"].get("report.html", "")

    # ledger rows
    hdr = ledger_headers()
    jobs_row_d = {
        "job_id": job_id, "job_type": "barcode", "submitted_at": submitted_at, "completed_at": completed_at,
        "status": "done", "submitter": params.get("submitter", ""), "marker": params.get("marker", "ITS"),
        "n_samples": len(queries), "n_reads_in": len(queries), "n_reads_kept": len(accepted),
        "n_asvs": len(accepted), "n_fungal_asvs": n_fungal, "n_species": len(species_level),
        "location": params.get("location", ""), "lat": params.get("lat", ""), "lon": params.get("lon", ""),
        "result_folder_url": folder_url, "result_json_url": "", "notes": "; ".join(notes)[:500],
    }
    hit_rows = []
    for b in barcoding:
        sg = b["sintax"].get("genus", ["", ""])
        ss = b["sintax"].get("species", ["", ""])
        d = {"job_id": job_id, "query_id": b["query_id"], "query_length": b["query_length"],
             "submitted_at": submitted_at, "best_hit_accession": b["best_hit_accession"] or "",
             "best_hit_species": b["best_hit_species"] or "", "best_hit_sh": b["best_hit_sh"] or "",
             "identity_pct": b["identity_pct"] if b["identity_pct"] is not None else "",
             "coverage_pct": b["coverage_pct"] if b["coverage_pct"] is not None else "",
             "sintax_genus": sg[0], "sintax_species": ss[0],
             "sintax_conf": ss[1] if ss[1] != "" else sg[1],
             "assignment_level": b["assignment_level"], "chimera_flag": b["chimera_flag"], "report_url": report_url}
        hit_rows.append([d.get(c, "") for c in hdr["barcoding_hits"]])
    try:
        storage.append_ledger("jobs", [[jobs_row_d.get(c, "") for c in hdr["jobs"]]])
        if hit_rows:
            storage.append_ledger("barcoding_hits", hit_rows)
    except Exception as ex:
        notes.append(f"ledger append failed: {ex}")
    result["notes"] = notes
    return result
