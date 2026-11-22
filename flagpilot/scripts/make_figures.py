"""Draw the README's figures from the committed records (needs matplotlib, which FlagPilot itself does not).

  pip install matplotlib && python3 scripts/make_figures.py      # writes ../docs/figures/*.png

  results.png   verified attempts per tier, v6 and v7, as run and re-scored (results/tacc/v*/rescore.csv)
  judging.png   how an attempt is judged: the four gates and the verdict each one gives
  startup.png   where the Qwen start-up time goes (step times from results/live/2026-11-21-pass*.log)
  power.png     how many paired attempts a B0 vs B3 difference of v7's size needs (exact McNemar, from v7's rescore.csv)
"""

from __future__ import annotations

import ast
import csv
import math
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

APP = Path(__file__).resolve().parent.parent
OUT = APP.parent / "docs" / "figures"
INK, BLUE, YELLOW, RED, GREY, PAPER = "#141414", "#2b4fd8", "#f4b400", "#e0332e", "#c9c9c4", "#f4f3ef"
TIERS = [("B0", "bare model"), ("B1", "+ trimming"), ("B2", "+ structured\ntools"), ("B3", "+ full rules")]

plt.rcParams.update({
    "font.family": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"], "font.size": 12,
    "axes.edgecolor": INK, "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK, "text.color": INK,
    "axes.spines.top": False, "axes.spines.right": False, "axes.linewidth": 1.2,
    "savefig.dpi": 200, "savefig.bbox": "tight", "savefig.pad_inches": 0.12, "savefig.facecolor": "white"})


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p, d = k / n, 1 + z * z / n
    c, h = (p + z * z / (2 * n)) / d, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def verified(batch: str) -> dict[str, dict[str, int]]:
    """{tier: {"old": verified as run, "new": re-scored, "n": attempts}} from a rescore.csv."""
    out: dict[str, dict[str, int]] = {}
    with open(APP / "results" / "tacc" / batch / "rescore.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t = out.setdefault(r["batch"].rsplit("-", 1)[1], {"old": 0, "new": 0, "n": 0})
            t["n"] += 1
            t["old"] += r["old"] == "verified"
            t["new"] += r["new"] == "verified"
    return out


def results() -> None:
    v6, v7 = verified("v6"), verified("v7")
    fig, ax = plt.subplots(figsize=(9.2, 4.4))
    w = 0.36
    for i, (tier, _) in enumerate(TIERS):
        for dx, data, color, name in ((-w / 2, v6, GREY, "v6"), (w / 2, v7, BLUE, "v7")):
            t = data[tier]
            p = 100 * t["new"] / t["n"]
            lo, hi = (100 * x for x in wilson(t["new"], t["n"]))
            ax.bar(i + dx, p, w * 0.92, color=color, edgecolor=INK, linewidth=1.2, zorder=2)
            ax.errorbar(i + dx, p, yerr=[[p - lo], [hi - p]], color=INK, capsize=4, linewidth=1.2, zorder=3)
            ax.text(i + dx, 4, f"{t['new']}/{t['n']}", ha="center", va="bottom", fontsize=10.5,
                    color="white" if color == BLUE else INK, fontweight="bold", zorder=4)
            ax.plot(i + dx, 100 * t["old"] / t["n"], marker="D", markersize=7, markerfacecolor="white",
                    markeredgecolor=RED, markeredgewidth=1.8, linestyle="none", zorder=5)
    ax.set_xticks(range(len(TIERS)), [f"{t}\n{d}" for t, d in TIERS])
    ax.set_ylim(0, 100)
    ax.set_ylabel("verified attempts (%)")
    ax.yaxis.grid(True, color="#e6e5e0", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    handles = [plt.Rectangle((0, 0), 1, 1, facecolor=GREY, edgecolor=INK), plt.Rectangle((0, 0), 1, 1, facecolor=BLUE, edgecolor=INK),
               plt.Line2D([], [], marker="D", markersize=7, markerfacecolor="white", markeredgecolor=RED, markeredgewidth=1.8, linestyle="none")]
    ax.legend(handles, ["v6, re-scored", "v7 (final engine), re-scored", "as judged at run time"], loc="upper center",
              bbox_to_anchor=(0.5, 1.16), ncol=3, frameon=False, fontsize=11)
    ax.set_title("Qwen 3.8 27B on 20 dev challenges, 2 attempts each (whiskers: Wilson 95% CI)", fontsize=12, pad=34, loc="left")
    fig.savefig(OUT / "results.png")
    plt.close(fig)


def box(ax, x, y, w, h, text, face, edge=INK, color=INK, size=11.5, bold=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="square,pad=0", facecolor=face, edgecolor=edge, linewidth=1.6))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size, color=color,
            fontweight="bold" if bold else "normal", linespacing=1.25)


def arrow(ax, x0, y0, x1, y1, color=INK):
    ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=16, linewidth=1.6, color=color))


def judging() -> None:
    """Row 1: what the agent produces. Row 2: the four gates in order, then the verdict; under each gate, what failing it gives."""
    fig, ax = plt.subplots(figsize=(11.5, 4.5))
    ax.set_xlim(0, 115)
    ax.set_ylim(0, 44)
    ax.axis("off")
    top, th, tw = 31, 11, 25
    tops = [(1, "challenge files\n(read-only copy)"), (32, "agent loop\none action per turn"), (63, "submission\nflag + solve.py")]
    for i, (x, text) in enumerate(tops):
        box(ax, x, top, tw, th, text, PAPER)
        if i < len(tops) - 1:
            arrow(ax, x + tw, top + th / 2, tops[i + 1][0], top + th / 2)
    gy, gh, gw, gap = 10, 12, 19, 4
    gates = [("1  flag hash\nmatches gold", "wrong"),
             ("2  no flag literal\nin solve.py", "unverified ·\nliteral_flag"),
             ("3  a clean replay\nprints the flag", "unverified ·\nscript_error"),
             ("4  not printed with\nthe files removed\nor replaced (decoys)", "unverified ·\nhardcoded_no_material")]
    xs = [1 + i * (gw + gap) for i in range(len(gates))]
    for i, ((text, fail), x) in enumerate(zip(gates, xs)):
        box(ax, x, gy, gw, gh, text, "white", size=10.5)
        ax.text(x + gw / 2, gy - 1.2, "fails →\n" + fail, ha="center", va="top", fontsize=9.5, color=RED, linespacing=1.25)
        arrow(ax, x + gw, gy + gh / 2, (xs[i + 1] if i + 1 < len(xs) else x + gw + gap), gy + gh / 2)
    vx = xs[-1] + gw + gap
    box(ax, vx, gy, gw, gh, "verified", BLUE, color="white", size=14, bold=True)
    ax.text(vx + gw / 2, gy - 1.2, "all four\ngates pass", ha="center", va="top", fontsize=9.5, linespacing=1.25)
    # the submission goes to gate 1: down, left, down
    sx, mid = 63 + tw / 2, (top + gy + gh) / 2
    ax.plot([sx, sx, xs[0] + gw / 2], [top, mid, mid], color=INK, linewidth=1.6, solid_capstyle="butt")
    arrow(ax, xs[0] + gw / 2, mid + 0.4, xs[0] + gw / 2, gy + gh)
    fig.savefig(OUT / "judging.png")
    plt.close(fig)


STEP_NAMES = [("queue", "waiting for 2 GPUs"), ("node", "Python + vLLM import"), ("vllm", "vLLM start"),
              ("engine", "engine on 2 GPUs"), ("weights", "weights"), ("compile", "compiled kernels"), ("server", "API")]
STEP_COLORS = {"queue": "#d9d8d3", "node": "#8fa3ea", "vllm": BLUE, "engine": "#1a2f86", "weights": YELLOW,
               "compile": "#f08b3a", "server": RED}


def step_times(log: Path) -> tuple[str, dict[str, int]]:
    text = log.read_text(encoding="utf-8")
    serve = (re.search(r"serv(?:e|ing job) (\d{5})", text) or [None, "?"])[1]
    raw = re.search(r"step times: (\[.*\])", text).group(1)
    return serve, {k: int(v) for k, v in ast.literal_eval(raw)}


def startup() -> None:
    runs = [step_times(APP / "results" / "live" / f"2026-11-21-pass{i}.log") for i in (1, 3)]
    fig, ax = plt.subplots(figsize=(9.6, 2.9))
    for row, (serve, t) in enumerate(runs):
        left = 0
        for sid, name in STEP_NAMES:
            s = t.get(sid, 0) + (t.get("warmup", 0) if sid == "compile" else 0)
            if not s:
                continue
            ax.barh(row, s, left=left, height=0.56, color=STEP_COLORS[sid], edgecolor=INK, linewidth=1.0)
            if s >= 45:
                ax.text(left + s / 2, row, f"{s}", ha="center", va="center", fontsize=9.5,
                        color="white" if sid in ("vllm", "engine", "node", "server") else INK)
            left += s
        ax.text(left + 8, row, f"{left / 60:.1f} min", va="center", fontsize=11.5, fontweight="bold")
    ax.set_yticks([0, 1], [f"job {runs[0][0]}\ntypical", f"job {runs[1][0]}\nslow storage"])
    ax.invert_yaxis()
    ax.set_xlabel("seconds from submission to ready")
    ax.set_xlim(0, 1130)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    handles = [plt.Rectangle((0, 0), 1, 1, facecolor=STEP_COLORS[s], edgecolor=INK) for s, _ in STEP_NAMES]
    ax.legend(handles, [n for _, n in STEP_NAMES], loc="upper center", bbox_to_anchor=(0.47, 1.42), ncol=4, frameon=False, fontsize=10)
    fig.savefig(OUT / "startup.png")
    plt.close(fig)


def mcnemar_p(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def mcnemar_power(n: int, p_gain: float, p_loss: float, alpha: float = 0.05) -> float:
    """Probability that n independent pairs give p < alpha, when a pair is gained, lost or unchanged with these chances."""
    p_same = 1 - p_gain - p_loss
    total = 0.0
    for b in range(n + 1):
        for c in range(n + 1 - b):
            if mcnemar_p(b, c) >= alpha:
                continue
            lp = math.lgamma(n + 1) - math.lgamma(b + 1) - math.lgamma(c + 1) - math.lgamma(n - b - c + 1)
            lp += b * math.log(p_gain) + (c * math.log(p_loss) if c else 0) + ((n - b - c) * math.log(p_same) if n - b - c else 0)
            total += math.exp(lp)
    return total


def power() -> None:
    """v7 re-scored, B0 -> B3 on matched attempts: 3 gained, 0 lost of 40. The zero is smoothed to one half (a loss in 80)."""
    with open(APP / "results" / "tacc" / "v7" / "rescore.csv", encoding="utf-8") as f:
        v = {}
        for r in csv.DictReader(f):
            v.setdefault(r["batch"].rsplit("-", 1)[1], {})[r["attempt"]] = r["new"] == "verified"
    keys = sorted(set(v["B0"]) & set(v["B3"]))
    gain = sum(1 for k in keys if not v["B0"][k] and v["B3"][k])
    loss = sum(1 for k in keys if v["B0"][k] and not v["B3"][k])
    n0 = len(keys)
    ns = list(range(20, 421, 20))
    fig, ax = plt.subplots(figsize=(9.2, 3.9))
    cases = [(f"as in v7: {gain} gained, {loss} lost of {n0} (a zero counted as ½)", (gain or 0.5) / n0, (loss or 0.5) / n0, BLUE, "-"),
             (f"if one in {n0} were lost: {gain} gained, 1 lost", (gain or 0.5) / n0, 1 / n0, YELLOW, "--")]
    for label, pg, pl, color, ls in cases:
        ax.plot(ns, [100 * mcnemar_power(n, pg, pl) for n in ns], color=color, linewidth=2.6, linestyle=ls, label=label)
    ax.axhline(80, color=INK, linewidth=1, linestyle=":")
    ax.text(ns[-1] - 4, 78, "80% power", ha="right", va="top", fontsize=10.5)
    ax.axvline(n0, color=RED, linewidth=1.4)
    ax.text(n0 + 6, 96, f"v7: {n0} pairs per tier", color=RED, fontsize=10.5, va="top")
    ax.set_xlim(0, ns[-1])
    ax.set_ylim(0, 100)
    ax.set_xlabel("paired attempts per tier (challenges × repeats)")
    ax.set_ylabel("chance of p < 0.05 (%)")
    ax.yaxis.grid(True, color="#e6e5e0", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.2), ncol=1, frameon=False, fontsize=10.5)
    fig.savefig(OUT / "power.png")
    plt.close(fig)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    results()
    judging()
    startup()
    power()
    print("wrote", ", ".join(sorted(p.name for p in OUT.glob("*.png"))), "in", OUT)
