#!/usr/bin/env python3
"""Render three research figures from the bundled derived figure CSVs only.

No market API, account, raw feed, or model re-estimation is used. Output prices
and historical returns are not substituted for current executable quotes.
"""
from pathlib import Path
import argparse
import csv


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reports" / "figures"


def rows(name):
    with (SOURCE / name).open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=SOURCE)
    args = parser.parse_args()
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise SystemExit("Install optional plotting dependencies: python -m pip install -r requirements-public.txt") from exc
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    navy, teal, grey, gold = "#142B43", "#117C83", "#8C9BAB", "#C88928"
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.labelcolor": navy, "text.color": navy, "svg.hashsalt": "fomc-public-v1"})

    def save(fig, name):
        fig.savefig(output / f"{name}.png", dpi=180, bbox_inches="tight", facecolor="white")
        fig.savefig(output / f"{name}.svg", bbox_inches="tight", metadata={"Date": None})
        plt.close(fig)

    coverage = rows("01_meeting_coverage.csv")
    fig, ax = plt.subplots(figsize=(8.7, 3.3), layout="constrained")
    values = [int(row["meetings"]) for row in coverage]
    assert sum(values) == 38, "Coverage figure must reconcile to the 38-meeting audit"
    bars = ax.barh([row["category"] for row in coverage], values, color=[teal] + [grey] * (len(values) - 1))
    ax.invert_yaxis()
    ax.bar_label(bars, padding=4)
    ax.set_xlim(0, max(values) + 2)
    ax.set_xlabel("Meetings (38 historical meetings; mutually exclusive primary classification)")
    ax.set_title("Twelve meetings pass the current data-design screen", loc="left", fontweight="bold")
    save(fig, "01_meeting_coverage")

    trades = rows("02_trade_returns.csv")
    trades.sort(key=lambda row: row["meeting_date"])
    fig, ax = plt.subplots(figsize=(8.7, 4.0), layout="constrained")
    for i, row in enumerate(trades):
        before, after = float(row["original_roi_pct"]), float(row["updated_roi_pct"])
        ax.plot([before, after], [i, i], color=grey, linewidth=2)
    ax.scatter([float(r["original_roi_pct"]) for r in trades], range(len(trades)), facecolors="white",
               edgecolors=grey, linewidths=1.5, s=50, label="Original cost proxy", zorder=3)
    ax.scatter([float(r["updated_roi_pct"]) for r in trades], range(len(trades)), color=teal,
               s=26, label="Five-book cost replacement", zorder=4)
    labels = [r["meeting_date"] + (" *" if r["new_pm_book_available"].lower() != "true" else "") for r in trades]
    ax.set_yticks(range(len(trades)), labels)
    ax.invert_yaxis()
    ax.set_xlim(left=0)
    ax.grid(axis="x", alpha=.18)
    ax.set_xlabel("Realized model profit / standalone full-stage committed capital (%)")
    ax.set_title("Nine historical Polymarket positions | hypothetical funding and fees", loc="left", fontweight="bold")
    ax.legend(loc="upper right", frameon=False, fontsize=9)
    fig.text(.985, -.025, "* No new prediction-market book; original cost proxy retained.", ha="right", fontsize=8)
    save(fig, "02_trade_returns")

    depths = rows("03_pm_depth_cost.csv")
    fig, ax = plt.subplots(figsize=(8.7, 3.3), layout="constrained")
    values = [float(r["original_size_slippage_from_mid_cents"]) for r in depths]
    bars = ax.bar([r["meeting_date"] for r in depths], values,
                  color=[gold if value > 2 else teal for value in values], width=.58)
    ax.bar_label(bars, labels=[f"{value:.2f}" for value in values], padding=4)
    ax.axhline(2, color=navy, linestyle="--", linewidth=1, label="Original 2-cent proxy")
    ax.set_ylim(0, max(values) + .7)
    ax.set_ylabel("Ask VWAP - midpoint (cents / digital)")
    ax.set_title("Full-size displayed depth cost varies across five entries", loc="left", fontweight="bold")
    ax.legend(frameon=False, loc="upper left", fontsize=9)
    fig.text(.985, -.025, "Snapshot depth only; excludes fees, queue dynamics and subsequent book changes.", ha="right", fontsize=8)
    save(fig, "03_pm_depth_cost")
    print("Rendered three PNG/SVG figure pairs from included derived CSVs.")


if __name__ == "__main__":
    main()
