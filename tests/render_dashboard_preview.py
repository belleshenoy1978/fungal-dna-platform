"""Static matplotlib rendering of the dashboard's data panels from tests/data/ledger/*.csv.

This is NOT a browser screenshot: headless browsers do not run in the build sandbox, so the
same aggregations implemented in app/static/dashboard.js (FDP.kpis, submissionsPerDay,
generaByReads, barcodeGenera, phylaByJob, mapPoints) are re-implemented here in Python and
drawn with matplotlib. Every number comes from the ledger CSV files.
Run from the repo root:  python tests/render_dashboard_preview.py
"""
from __future__ import annotations
import csv, json, sys
from collections import Counter, defaultdict
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
LED = ROOT / "tests" / "data" / "ledger"


def read(sheet):
    with (LED / f"{sheet}.csv").open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ---- same aggregations as dashboard.js ----
def kpis(jobs, samples, hits):
    ampl = [j for j in jobs if j["job_type"] == "amplicon"]
    return dict(jobs=len(jobs), samples=len(samples) or int(sum(num(j["n_samples"]) for j in ampl)),
                asvs=int(sum(num(j["n_asvs"]) for j in ampl)), species=int(sum(num(j["n_species"]) for j in ampl)), barcodes=len(hits))

def submissions_per_day(jobs):
    by = defaultdict(lambda: {"amplicon": 0, "barcode": 0})
    for j in jobs:
        d = j["submitted_at"][:10]
        by[d]["barcode" if j["job_type"] == "barcode" else "amplicon"] += 1
    days = sorted(by)
    return days, [by[d]["amplicon"] for d in days], [by[d]["barcode"] for d in days]

def genera_by_reads(taxa, top=20):
    acc = Counter()
    for r in taxa:
        g = r["genus"].strip()
        if g and g.lower() != "unassigned":
            acc[g] += num(r["reads"])
    return acc.most_common(top)

def barcode_genera(hits, top=20):
    acc = Counter()
    for h in hits:
        g = h["best_hit_species"].strip().replace("_", " ").split(" ")[0]
        if g:
            acc[g] += 1
    return acc.most_common(top)

def phyla_by_job(taxa, jobs):
    by, tot, ph = defaultdict(Counter), Counter(), Counter()
    for r in taxa:
        p = r["phylum"].strip()
        if r["kingdom"] and r["kingdom"] != "Fungi": p = "Non-fungal"
        elif not p: p = "Unassigned"
        n = num(r["reads"]); by[r["job_id"]][p] += n; tot[r["job_id"]] += n; ph[p] += n
    label = {j["job_id"]: (j["marker"] + " · " if j["marker"] else "") + (j["location"] or j["job_id"]) for j in jobs}
    jids = list(by); order = [p for p, _ in ph.most_common()]
    pct = [[100 * by[j][p] / tot[j] if tot[j] else 0 for j in jids] for p in order]
    return jids, [label.get(j, j) for j in jids], order, pct

def map_points(jobs):
    pts = []
    for j in jobs:
        try: la, lo = float(j["lat"]), float(j["lon"])
        except ValueError: continue
        pts.append((la, lo, j["location"], num(j["n_samples"]), j["marker"]))
    return pts


def main(out=ROOT / "tests" / "dashboard_preview.png"):
    jobs, samples, taxa, hits = read("jobs"), read("samples"), read("taxa"), read("barcoding_hits")
    k = kpis(jobs, samples, hits)
    days, a, b = submissions_per_day(jobs)
    gen = genera_by_reads(taxa); bg = barcode_genera(hits)
    jids, labels, phyla, pct = phyla_by_job(taxa, jobs); pts = map_points(jobs)
    numbers = dict(kpis=k, submissions=dict(days=days, amplicon=a, barcode=b), top_genera=gen[:5], barcode_genera=bg,
                   phyla={labels[i]: {p: round(pct[pi][i], 1) for pi, p in enumerate(phyla)} for i in range(len(jids))}, map_points=pts)
    (out.with_suffix(".json")).write_text(json.dumps(numbers, indent=1), encoding="utf-8")

    try:
        apply_figure_style  # noqa: F821  (figure-style helper when run inside the analysis kernel)
    except NameError:
        plt.rcParams.update({"font.size": 8, "axes.titlesize": 8, "axes.labelsize": 8, "xtick.labelsize": 6, "ytick.labelsize": 6, "legend.fontsize": 7, "axes.spines.top": False, "axes.spines.right": False})
    GREEN, ORANGE = "#0b6e4f", "#c9771e"
    PH_COL = ["#0b6e4f", "#2a6f97", "#8c2f39", "#6a4c93", "#d4a017", "#1b998b", "#b56576", "#3a7d44", "#7b5e3b", "#4f6d7a"]
    def ph_colour(p, i):
        return "#a3a3a3" if p == "Unassigned" else ("#cfd6d5" if p == "Non-fungal" else PH_COL[i % len(PH_COL)])

    fig = plt.figure(figsize=(11, 10.5), dpi=150)
    gs = fig.add_gridspec(4, 3, height_ratios=[0.55, 1.1, 1.3, 0.9], hspace=0.65, wspace=0.45)

    # KPI strip
    axk = fig.add_subplot(gs[0, :]); axk.axis("off")
    tiles = [(k["jobs"], "jobs"), (k["samples"], "samples"), (k["asvs"], "ASVs"), (k["species"], "species-level assignments"), (k["barcodes"], "barcoding queries")]
    for i, (v, lab) in enumerate(tiles):
        x = (i + 0.5) / len(tiles)
        axk.text(x, 0.62, f"{v:,}", ha="center", va="center", fontsize=18, color=GREEN, weight="bold", transform=axk.transAxes)
        axk.text(x, 0.15, lab, ha="center", va="center", fontsize=8, color="#5d6b6b", transform=axk.transAxes)
    axk.set_title("Dashboard preview — matplotlib rendering of tests/data/ledger/*.csv (not a browser screenshot)", loc="left", fontsize=9)

    # submissions over time
    ax1 = fig.add_subplot(gs[1, 0])
    xi = np.arange(len(days))
    ax1.bar(xi, a, color=GREEN, label="amplicon"); ax1.bar(xi, b, bottom=a, color=ORANGE, label="barcode")
    ax1.set_xticks(xi); ax1.set_xticklabels(days, rotation=30, ha="right"); ax1.set_ylabel("jobs"); ax1.set_yticks(range(0, max(np.array(a) + np.array(b)) + 2))
    ax1.set_title("Submissions per day (UTC)", loc="left"); ax1.set_ylim(0, max(np.array(a) + np.array(b)) * 1.6 + 0.1); ax1.legend(frameon=False, loc="upper right", ncol=2)

    # map (India-centred; coastline not drawn — plain lat/lon axes)
    ax2 = fig.add_subplot(gs[1, 1:])
    ax2.set_xlim(64, 100); ax2.set_ylim(4, 38); ax2.set_aspect("equal")
    ax2.set_facecolor("#dbe9f2")
    # coarse India outline polygon (approximate, for orientation only)
    india = [(68.2, 23.6), (70.5, 20.9), (72.6, 19.0), (73.4, 16.0), (74.8, 12.9), (76.3, 9.0), (77.5, 8.1), (78.2, 8.9), (79.8, 10.3), (80.3, 13.1), (80.2, 15.8), (82.3, 17.0), (84.8, 19.3), (86.9, 20.7), (88.2, 21.6), (89.0, 22.0), (88.4, 24.3), (88.1, 26.4), (89.7, 26.8), (92.0, 26.9), (96.0, 27.5), (97.3, 28.3), (94.9, 29.3), (92.0, 27.8), (89.5, 28.0), (88.1, 27.9), (85.0, 28.6), (81.0, 30.1), (79.0, 31.0), (78.4, 32.6), (76.9, 34.4), (74.5, 34.0), (74.0, 32.0), (71.0, 28.5), (69.5, 26.6), (68.2, 23.6)]
    ax2.fill(*zip(*india), color="#eef0e9", ec="#888", lw=0.6)
    for la, lo, loc, n, mk in pts:
        ax2.scatter(lo, la, s=(10 + 4 * np.sqrt(n)) ** 2 * 0.8, color=GREEN, alpha=0.75, ec="white", lw=1, zorder=3)
        ax2.annotate(f"{loc.split(',')[0]}\n{mk}, {int(n)} samples", (lo, la), xytext=(8, -4), textcoords="offset points", fontsize=7, va="top")
    ax2.set_xlabel("longitude (°E)"); ax2.set_ylabel("latitude (°N)")
    ax2.set_title(f"Sample origins ({len(pts)} georeferenced jobs; outline schematic)", loc="left")

    # genera leaderboard
    ax3 = fig.add_subplot(gs[2, :2])
    names = [g for g, _ in gen][::-1]; vals = [v for _, v in gen][::-1]
    ax3.barh(names, vals, color=GREEN); ax3.set_xlabel("reads (sum over samples, top-200 ASVs per job)")
    ax3.set_title(f"Top {len(gen)} genera by reads in the amplicon ledger", loc="left")
    for t in ax3.get_yticklabels(): t.set_fontstyle("italic")
    ax3.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v/1000:.0f}k" if v >= 1000 else f"{v:.0f}"))
    ax3.margins(x=0.08)

    # barcoding genera
    ax4 = fig.add_subplot(gs[2, 2])
    bn = [g for g, _ in bg][::-1]; bv = [v for _, v in bg][::-1]
    ax4.barh(bn, bv, color=ORANGE); ax4.set_xlabel("queries"); ax4.set_xticks(range(0, (max(bv) if bv else 1) + 1))
    ax4.set_title("Barcoding best-hit genera\n(synthetic demo-barcode job)", loc="left")
    for t in ax4.get_yticklabels(): t.set_fontstyle("italic")
    ax4.margins(x=0.1)

    # phylum composition per job
    ax5 = fig.add_subplot(gs[3, :])
    left = np.zeros(len(jids))
    for pi, p in enumerate(phyla):
        vals = np.array(pct[pi]); ax5.barh(labels, vals, left=left, color=ph_colour(p, pi), label=p, height=0.6, ec="#777", lw=0.3); left += vals
    ax5.set_xlim(0, 100); ax5.set_xlabel("% of ledger reads (top-200 ASVs per job)")
    ax5.set_title("Phylum composition per job", loc="left"); ax5.legend(frameon=False, ncol=min(6, len(phyla)), loc="upper center", bbox_to_anchor=(0.5, -0.45), fontsize=7)
    fig.savefig(out, bbox_inches="tight")
    print("saved", out); print(json.dumps(numbers)[:1500])
    return fig


if __name__ == "__main__":
    main()
