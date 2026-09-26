#!/usr/bin/env python3
"""Does the Poly-vs-ZQ basis trade pay, and above what entry gate?

The earlier tracking-error work left one question open.  Every meeting we could test had a
hedge we could not rely on, because EFFR only started drifting inside the month in
2025-09; every meeting with a reliable hedge was one we had no ZQ history for.  Databento
closed that gap (ZQ settlements back to 2021-12), so the 2022-01..2025-08 "pinned" regime
is now testable and the question becomes answerable: is the absence of edge a property of
the strategy, or of the current regime?

Instrument.  A ZQ calendar spread whose two delivery months are separated by exactly one
decision.  FRONT = (M, M+1) with span 1-w, BACK = (M-1, M) with span w, where
w = days_post/days_in_month.  Implied decision D = (F_near - F_far)/span.  The unknown
post-decision EFFR level cancels, so the EFFR publication lag never enters.  A month
carrying any OTHER meeting's rate change disqualifies the construction that uses it;
because w is large exactly when the meeting falls early in its month, FRONT and BACK are
complementary and between them cover 18 of the 21 Polymarket-era meetings.

Alignment.  ZQ settles 14:00 CT, which is 15:00 ET, an hour AFTER the FOMC announcement.
This is not documentation, it is measured: the 2024-09-18 settle implies -50.83bp and so
do the next four sessions, while 2024-09-17 implies -42.08bp.  Polymarket is therefore
snapped at its last print at or before 15:00 ET, and decision day is dropped outright.

P&L.  Entering at basis b and holding both legs to settlement locks in |b|; the only thing
that takes it away is the hedge's own error e and the cost of getting in:

    net_cents = |b| - sign(b)*e - 100*ZQ_dollars/(1041.75*span) - poly_half_spread

e is not estimated.  A ZQ contract settles at 100 minus the arithmetic mean of daily EFFR
over its delivery month, so once the month is past, its terminal price is a known function
of published EFFR and e follows exactly.

Usage:
    python3 harness/pinned_regime.py                  # full sweep to stdout
    python3 harness/pinned_regime.py --csv results/   # also write the per-trade tables
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

DATA_ROOT = Path(os.environ.get("FOMC_DATA_ROOT", Path(__file__).resolve().parents[1] / "local_data")).expanduser().resolve()
DB = Path(os.environ.get("FED_PRICING_DB", DATA_ROOT / "fed.duckdb")).expanduser()
ZQ = DATA_ROOT / "raw/cme/databento/zq_outrights.parquet"
PINNED_END = "2025-08-31"          # last month EFFR held a single value all month
CONTRACTS_PER_SPREAD = 1041.75     # 4167 * 0.25: binary contracts hedged by one ZQ spread at span 1


def instruments(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """FRONT/BACK choice per meeting. Pure function of the FOMC calendar: no price input."""
    m = con.execute("""SELECT meeting_date, effective_date, delta, realized_change_bp
                       FROM meetings ORDER BY meeting_date""").df()
    m["meeting_date"] = pd.to_datetime(m.meeting_date)
    m["effective_date"] = pd.to_datetime(m.effective_date)
    eff: dict = {}
    for _, r in m.iterrows():
        eff.setdefault(r.effective_date.to_period("M"), []).append(r.meeting_date)
    others = lambda p, self_: [d for d in eff.get(p, []) if d != self_]

    out = []
    for _, r in m.iterrows():
        p, w, md = r.meeting_date.to_period("M"), r.delta, r.meeting_date
        cands = []
        # w == 0 means the meeting sits on the last day of M and the whole decision lands
        # in M+1, which makes FRONT a span-1.0 instrument rather than a degenerate one.
        if not others(p, md) and not others(p + 1, md) and 1 - w > 1e-9:
            cands.append(("FRONT", 1 - w, str(p), str(p + 1)))
        if not others(p - 1, md) and not others(p, md) and w > 1e-9:
            cands.append(("BACK", w, str(p - 1), str(p)))
        if not cands:
            continue
        pick, span, near, far = max(cands, key=lambda t: t[1])
        out.append(dict(meeting_date=md, pick=pick, span=span, leg_near=near, leg_far=far,
                        realized_change_bp=r.realized_change_bp))
    return pd.DataFrame(out)


def hedge_error(con: duckdb.DuckDBPyConnection, inst: pd.DataFrame) -> pd.DataFrame:
    """Exact realized hedge error per meeting, from published EFFR. No estimation."""
    effr = con.execute("""
        WITH cal AS (SELECT CAST(unnest(generate_series(DATE '2021-12-01', DATE '2026-09-30',
                            INTERVAL 1 DAY)) AS DATE) AS d),
             obs AS (SELECT date, effr FROM fred_rates WHERE effr IS NOT NULL)
        SELECT c.d, o.effr FROM cal c ASOF LEFT JOIN obs o ON c.d >= o.date""").df()
    effr["ym"] = pd.to_datetime(effr.d).dt.to_period("M").astype(str)
    avg = effr.groupby("ym").effr.agg(["mean", "count"])

    rows = []
    for _, r in inst.dropna(subset=["realized_change_bp"]).iterrows():
        if r.leg_near not in avg.index or r.leg_far not in avg.index:
            continue
        a, b = avg.loc[r.leg_near], avg.loc[r.leg_far]
        if a["count"] < 28 or b["count"] < 28:      # month not fully realized yet
            continue
        spread_bp = ((100 - a["mean"]) - (100 - b["mean"])) * 100
        resid_bp = spread_bp - r.span * r.realized_change_bp
        rows.append(dict(meeting_date=r.meeting_date, resid_bp=resid_bp,
                         err_D_bp=resid_bp / r.span, err_cents=4 * resid_bp / r.span))
    return pd.DataFrame(rows)


def cme_panel(con: duckdb.DuckDBPyConnection, inst: pd.DataFrame) -> pd.DataFrame:
    z = pd.read_parquet(ZQ)[["trade_date", "dm", "price"]]
    z["trade_date"] = pd.to_datetime(z.trade_date)
    win = con.execute("SELECT meeting_date, prev_effective_date FROM meeting_windows").df()
    for c in ("meeting_date", "prev_effective_date"):
        win[c] = pd.to_datetime(win[c])
    inst = inst.merge(win, on="meeting_date", how="left")

    frames = []
    for _, r in inst.iterrows():
        near = z[z.dm == r.leg_near].set_index("trade_date").price.rename("F_near")
        far = z[z.dm == r.leg_far].set_index("trade_date").price.rename("F_far")
        d = pd.concat([near, far], axis=1).dropna()
        d = d[(d.index > r.prev_effective_date) & (d.index < r.meeting_date)]
        if d.empty:
            continue
        d = d.reset_index().rename(columns={"trade_date": "date"})
        d["meeting_date"], d["pick"], d["span"] = r.meeting_date, r.pick, r.span
        d["cme_bp"] = (d.F_near - d.F_far) / r.span * 100
        d["dtd"] = (r.meeting_date - d.date).dt.days
        frames.append(d)
    return pd.concat(frames, ignore_index=True)


def poly_panel(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Polymarket at 15:00 ET, with a liveness flag.

    A frozen CLOB midpoint prints every minute exactly like a live one, so the tell is
    variety, not presence.  But a deep out-of-the-money leg is legitimately still, so
    liveness counts distinct midpoints only over legs sitting in [0.05, 0.95].
    """
    df = con.execute("""
    WITH tok AS (SELECT DISTINCT token_id, outcome, fomc_date FROM poly_daily_yes),
    px AS (SELECT t.fomc_date, t.outcome, t.token_id, p.p, p.ts,
                  CAST(timezone('America/New_York', to_timestamp(p.ts)) AS DATE) AS d,
                  strftime(timezone('America/New_York', to_timestamp(p.ts)),'%H:%M') AS hm
           FROM poly_prices p JOIN tok t USING (token_id)),
    snap AS (SELECT fomc_date, outcome, token_id, d, arg_max(p, ts) AS px
             FROM px WHERE hm <= '15:00' GROUP BY ALL),
    var AS (SELECT s.fomc_date, s.outcome, s.d, s.px, count(DISTINCT b.p) AS uniq_3d
            FROM snap s JOIN px b ON b.fomc_date = s.fomc_date AND b.outcome = s.outcome
                                 AND b.d > s.d - 3 AND b.d <= s.d
            GROUP BY ALL),
    byleg AS (SELECT fomc_date, d, outcome, sum(px) AS px, min(uniq_3d) AS uniq_3d
              FROM var GROUP BY ALL),
    agg AS (SELECT fomc_date, d, sum(px) AS sum_raw, count(*) AS n_legs,
              min(CASE WHEN px BETWEEN 0.05 AND 0.95 THEN uniq_3d END) AS live_inplay,
              sum(CASE WHEN outcome='CUT50P'  THEN px ELSE 0 END) AS c50,
              sum(CASE WHEN outcome='CUT25'   THEN px ELSE 0 END) AS c25,
              sum(CASE WHEN outcome='HIKE25'  THEN px ELSE 0 END) AS h25,
              sum(CASE WHEN outcome='HIKE50P' THEN px ELSE 0 END) AS h50
            FROM byleg GROUP BY ALL)
    SELECT a.fomc_date AS meeting_date, a.d AS "date", a.sum_raw, a.n_legs, a.live_inplay,
           (a.c50*-50 + a.c25*-25 + a.h25*25
            + a.h50*(CASE WHEN pem.hike50p_is_any_hike THEN 25 ELSE 50 END)) / a.sum_raw AS poly_bp
    FROM agg a JOIN poly_event_meeting pem
      ON pem.event_id IS NOT NULL AND pem.meeting_date = a.fomc_date
     AND pem.role = 'decision' AND pem.is_primary""").df()
    df["meeting_date"] = pd.to_datetime(df.meeting_date)
    df["date"] = pd.to_datetime(df["date"])
    return df


def net_cents(g: pd.DataFrame, zq_dollars: float, poly_half: float) -> pd.Series:
    fixed = 100 * zq_dollars / (CONTRACTS_PER_SPREAD * g.span)
    return g.basis_c.abs() - np.sign(g.basis_c) * g.err_cents - fixed - poly_half


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, help="directory to write per-trade tables into")
    ap.add_argument("--zq", type=float, default=16.0, help="$ per ZQ calendar spread, round turn")
    ap.add_argument("--poly", type=float, default=1.0, help="Polymarket half-spread, cents")
    args = ap.parse_args()

    con = duckdb.connect(str(DB), read_only=True)
    inst = instruments(con)
    err = hedge_error(con, inst)
    p = (cme_panel(con, inst)
         .merge(poly_panel(con), on=["meeting_date", "date"], how="inner")
         .merge(err[["meeting_date", "err_cents"]], on="meeting_date", how="inner"))
    p = p[p.sum_raw.between(0.95, 1.10) & (p.n_legs >= 3)].copy()
    p["basis_c"] = 4 * (p.cme_bp - p.poly_bp)
    p["regime"] = np.where(p.meeting_date <= PINNED_END, "pinned", "drifting")

    err["regime"] = np.where(err.meeting_date <= PINNED_END, "pinned", "drifting")
    print("=== exact hedge error, cents per 25bp-equivalent contract ===")
    for reg, g in err.groupby("regime"):
        a = g.err_cents.abs()
        print(f"  {reg:9s} n={len(g):2d}  MAE {a.mean():6.3f}  median {a.median():6.3f}  "
              f"p90 {a.quantile(.9):6.3f}  max {a.max():6.3f}  exactly-zero {(a < 1e-9).sum()}")

    live = p[p.live_inplay.notna()]
    for reg in ("pinned", "drifting"):
        d = live[live.regime == reg]
        print(f"\n=== {reg}: net cents/contract, one trade per meeting, held to settlement "
              f"(ZQ ${args.zq:.0f}/spread, Poly {args.poly:.1f}c) ===")
        rows = []
        for lv in (0, 4, 9, 21):
            for tau in (0, 3, 5, 8, 12):
                s = d[(d.live_inplay >= lv) & (d.basis_c.abs() > tau) & (d.dtd <= 21)]
                s = s.sort_values("date").groupby("meeting_date").head(1)
                if s.empty:
                    rows.append(dict(live_gate=lv, basis_gate=tau, trades=0))
                    continue
                n = net_cents(s, args.zq, args.poly)
                rows.append(dict(live_gate=lv, basis_gate=tau, trades=len(n),
                                 win=f"{(n > 0).mean():.0%}", mean=round(n.mean(), 2),
                                 med=round(n.median(), 2), worst=round(n.min(), 2),
                                 t=round(n.mean() / (n.std(ddof=1) / np.sqrt(len(n))), 2)
                                 if len(n) > 2 else np.nan))
        print(pd.DataFrame(rows).to_string(index=False))

    if args.csv:
        args.csv.mkdir(parents=True, exist_ok=True)
        err.to_csv(args.csv / "pinned_hedge_error.csv", index=False)
        s = live[(live.regime == "pinned") & (live.live_inplay >= 9)
                 & (live.basis_c.abs() > 3) & (live.dtd <= 21)]
        s = s.sort_values("date").groupby("meeting_date").head(1).copy()
        s["net_cents"] = net_cents(s, args.zq, args.poly)
        s["contracts_per_spread"] = (CONTRACTS_PER_SPREAD * s.span).round()
        s["usd_per_spread"] = s.net_cents * s.contracts_per_spread / 100
        # F_near/F_far are raw CME settlement prices; only derived quantities leave the tree.
        s.drop(columns=["F_near", "F_far"]).to_csv(args.csv / "pinned_trades.csv", index=False)
        print(f"\nwrote {args.csv}/pinned_hedge_error.csv and pinned_trades.csv")


if __name__ == "__main__":
    main()
