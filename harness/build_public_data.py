#!/usr/bin/env python3
"""Build the publishable derived tables.

No raw market data leaves the working tree. Databento's licence makes republishing CME
settlement prices an act of REDISTRIBUTION, which would require a CME ILA and redistribution
fees; derived data is permitted provided it cannot be reverse-engineered back to the feed.
So every CME- or Polymarket-derived table here is ONE ROW PER MEETING, never a daily series:
33 summary rows cannot reconstruct 70,579 settlements or 9.2M order-book midpoints.

Two of the tables contain no exchange data at all. fomc_instruments.csv is the Fed's own
calendar, and hedge_error.csv plus effr_month_profile.csv are computed purely from published
EFFR, so they are US government data and free of any licence.

Usage:  python3 harness/build_public_data.py
"""
from __future__ import annotations

import os
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

DATA_ROOT = Path(os.environ.get("FOMC_DATA_ROOT", Path(__file__).resolve().parents[1] / "local_data")).expanduser().resolve()
DB = Path(os.environ.get("FED_PRICING_DB", DATA_ROOT / "fed.duckdb")).expanduser()
ZQ_DBN = DATA_ROOT / "raw/cme/databento/zq_outrights.parquet"
ZQ_IBKR = Path(os.environ.get("FED_PRICING_IBKR_ZQ", DATA_ROOT / "raw/cme/ibkr/zq_contracts_ibkr.parquet")).expanduser()
OUT = Path(__file__).resolve().parent.parent / "data"
PINNED_END = "2025-08-31"
ENTRY_DTD = 19            # the horizon the backtest actually enters at
# EFFR is published to 1bp, so a residual below this is floating-point residue from summing
# ~30 daily rates, not a hedge error. Two pinned meetings land at 2.5e-8 bp without it.
ZERO_TOL_CENTS = 1e-3


def instruments(con):
    m = con.execute("""SELECT meeting_date, effective_date, days_in_month, days_post, delta,
                       realized_change_bp FROM meetings ORDER BY meeting_date""").df()
    m["meeting_date"] = pd.to_datetime(m.meeting_date)
    m["effective_date"] = pd.to_datetime(m.effective_date)
    eff = {}
    for _, r in m.iterrows():
        eff.setdefault(r.effective_date.to_period("M"), []).append(r.meeting_date)
    other = lambda p, s: [d for d in eff.get(p, []) if d != s]

    rows = []
    for _, r in m.iterrows():
        p, w, md = r.meeting_date.to_period("M"), r.delta, r.meeting_date
        fr = not other(p, md) and not other(p + 1, md) and 1 - w > 1e-9
        bk = not other(p - 1, md) and not other(p, md) and w > 1e-9
        cands = ([("FRONT", 1 - w, str(p), str(p + 1))] if fr else []) + \
                ([("BACK", w, str(p - 1), str(p))] if bk else [])
        pick, span, near, far = max(cands, key=lambda t: t[1]) if cands else (None, 0.0, None, None)
        rows.append(dict(
            meeting_date=md.date(), effective_date=r.effective_date.date(),
            days_in_month=int(r.days_in_month), days_post=int(r.days_post), w=round(w, 6),
            front_eligible=fr, front_span=round(1 - w, 6),
            back_eligible=bk, back_span=round(w, 6),
            instrument=pick, span=round(span, 6), leg_near=near, leg_far=far,
            contracts_per_zq=round(1041.75 * span, 1) if span else None,
            realized_change_bp=r.realized_change_bp,
            regime="pinned" if md <= pd.Timestamp(PINNED_END) else "drifting"))
    return pd.DataFrame(rows)


def effr_profile(con):
    """Per delivery month: what EFFR actually did. Pure FRED, no exchange data."""
    e = con.execute("""
        WITH cal AS (SELECT CAST(unnest(generate_series(DATE '2015-01-01', DATE '2026-09-30',
                            INTERVAL 1 DAY)) AS DATE) AS d),
             obs AS (SELECT date, effr FROM fred_rates WHERE effr IS NOT NULL)
        SELECT c.d, o.effr FROM cal c ASOF LEFT JOIN obs o ON c.d >= o.date""").df()
    e = e.dropna(subset=["effr"])
    e["ym"] = pd.to_datetime(e.d).dt.to_period("M").astype(str)
    g = e.groupby("ym").effr.agg(days="size", mean_effr="mean", lo="min", hi="max",
                                 distinct="nunique").reset_index()
    g["range_bp"] = ((g.hi - g.lo) * 100).round(4)
    # mean_effr keeps full precision here: the hedge error is a difference of two monthly
    # means, and rounding first turns an exactly-zero residual into 1e-4bp of fake noise.
    first = e.sort_values("d").groupby("ym").effr.first()
    g["open_effr"] = g.ym.map(first)
    g["drift_bp"] = ((g.mean_effr - g.open_effr) * 100).round(4)
    g["regime"] = np.where(g.ym <= "2025-08", np.where(g.ym >= "2022-01", "pinned", "pre-2022"), "drifting")
    out = g[g.days >= 28].copy()
    out["mean_effr"] = out.mean_effr.round(8)   # display only; hedge_error() reads the raw frame
    return out[["ym", "regime", "days", "mean_effr", "open_effr", "distinct",
                "range_bp", "drift_bp"]]


def hedge_error(con, inst):
    """Exact realized hedge error per meeting. Pure FRED + the FOMC calendar."""
    prof = effr_profile(con).set_index("ym")
    rows = []
    for _, r in inst.dropna(subset=["realized_change_bp"]).iterrows():
        if not r.instrument or r.leg_near not in prof.index or r.leg_far not in prof.index:
            continue
        a, b = prof.loc[r.leg_near], prof.loc[r.leg_far]
        spread_bp = ((100 - a.mean_effr) - (100 - b.mean_effr)) * 100
        resid = spread_bp - r.span * r.realized_change_bp
        rows.append(dict(meeting_date=r.meeting_date, regime=r.regime, instrument=r.instrument,
                         span=r.span, realized_change_bp=r.realized_change_bp,
                         spread_terminal_bp=round(spread_bp, 4),
                         ideal_bp=round(r.span * r.realized_change_bp, 4),
                         resid_bp=round(resid, 4), err_implied_bp=round(resid / r.span, 4),
                         err_cents=round(4 * resid / r.span, 4),
                         is_exact_zero=abs(4 * resid / r.span) < ZERO_TOL_CENTS))
    return pd.DataFrame(rows)


def venue_snapshot(con, inst):
    """One row per meeting: both venues' implied expected move at a fixed horizon, plus
    coverage counts. Two probabilities per meeting reconstruct nothing."""
    poly = con.execute(f"""
    WITH tok AS (SELECT DISTINCT token_id, outcome, fomc_date FROM poly_daily_yes),
    px AS (SELECT t.fomc_date, t.outcome, p.p, p.ts,
                  CAST(timezone('America/New_York', to_timestamp(p.ts)) AS DATE) AS d,
                  strftime(timezone('America/New_York', to_timestamp(p.ts)),'%H:%M') AS hm
           FROM poly_prices p JOIN tok t USING (token_id)),
    snap AS (SELECT fomc_date, outcome, d, arg_max(p, ts) AS px
             FROM px WHERE hm <= '15:00' GROUP BY ALL),
    agg AS (SELECT fomc_date, d, sum(px) AS sum_raw, count(*) AS n_legs,
              sum(CASE WHEN outcome='CUT50P'  THEN px ELSE 0 END) AS c50,
              sum(CASE WHEN outcome='CUT25'   THEN px ELSE 0 END) AS c25,
              sum(CASE WHEN outcome='HIKE25'  THEN px ELSE 0 END) AS h25,
              sum(CASE WHEN outcome='HIKE50P' THEN px ELSE 0 END) AS h50
            FROM snap GROUP BY ALL)
    SELECT a.fomc_date AS meeting_date, a.d AS "date", a.sum_raw, a.n_legs,
      (a.c50*-50 + a.c25*-25 + a.h25*25
       + a.h50*(CASE WHEN pem.hike50p_is_any_hike THEN 25 ELSE 50 END)) / a.sum_raw AS poly_bp
    FROM agg a JOIN poly_event_meeting pem
      ON pem.meeting_date = a.fomc_date AND pem.role='decision' AND pem.is_primary""").df()
    poly["meeting_date"] = pd.to_datetime(poly.meeting_date)
    poly["date"] = pd.to_datetime(poly["date"])
    poly["dtd"] = (poly.meeting_date - poly.date).dt.days

    z = pd.read_parquet(ZQ_DBN)[["trade_date", "dm", "price"]]
    z["trade_date"] = pd.to_datetime(z.trade_date)
    ib = pd.read_parquet(ZQ_IBKR)[["date", "delivery_month", "settle"]].rename(
        columns={"date": "trade_date", "delivery_month": "dm", "settle": "price"})
    ib["trade_date"] = pd.to_datetime(ib.trade_date)
    z = pd.concat([z, ib[~ib.set_index(["trade_date", "dm"]).index.isin(
        z.set_index(["trade_date", "dm"]).index)]], ignore_index=True)

    cov = con.execute("""
    WITH tok AS (SELECT DISTINCT token_id, fomc_date FROM poly_daily_yes)
    SELECT t.fomc_date AS meeting_date, count(DISTINCT t.token_id) AS poly_legs,
           count(*) FILTER (WHERE p.fidelity=1) AS poly_minute_points,
           min(CAST(timezone('America/New_York',to_timestamp(p.ts)) AS DATE)) AS poly_first,
           max(CAST(timezone('America/New_York',to_timestamp(p.ts)) AS DATE)) AS poly_last
    FROM poly_prices p JOIN tok t USING (token_id) GROUP BY 1""").df()
    cov["meeting_date"] = pd.to_datetime(cov.meeting_date)
    kal = con.execute("""SELECT min(trade_date_et) k_first, max(trade_date_et) k_last,
      count(DISTINCT market_id) k_legs, substr(market_id,15,5) tag
      FROM kalshi_candles WHERE market_id LIKE 'KXFEDDECISION%' GROUP BY 4""").df()

    rows = []
    for _, r in inst.iterrows():
        md = pd.Timestamp(r.meeting_date)
        out = dict(meeting_date=r.meeting_date, regime=r.regime,
                   realized_change_bp=r.realized_change_bp)
        if r.instrument:
            a = z[z.dm == r.leg_near].set_index("trade_date").price
            b = z[z.dm == r.leg_far].set_index("trade_date").price
            d = pd.concat([a.rename("n"), b.rename("f")], axis=1).dropna()
            d = d[(d.index < md) & ((md - d.index).days <= ENTRY_DTD + 4)]
            if len(d):
                row = d.iloc[(np.abs((md - d.index).days - ENTRY_DTD)).argmin()]
                out["cme_implied_bp_t19"] = round((row.n - row.f) / r.span * 100, 3)
                out["cme_obs_days"] = len(d)
        p = poly[(poly.meeting_date == md) & (poly.dtd.between(ENTRY_DTD - 4, ENTRY_DTD + 4))
                 & poly.sum_raw.between(0.95, 1.10)]
        if len(p):
            row = p.iloc[(p.dtd - ENTRY_DTD).abs().argmin()]
            out["poly_implied_bp_t19"] = round(row.poly_bp, 3)
        c = cov[cov.meeting_date == md]
        if len(c):
            out.update(poly_legs=int(c.poly_legs.iloc[0]),
                       poly_minute_points=int(c.poly_minute_points.iloc[0]),
                       poly_first=c.poly_first.iloc[0], poly_last=c.poly_last.iloc[0])
        tag = md.strftime("%y%b").upper()
        k = kal[kal.tag == tag]
        if len(k):
            out.update(kalshi_legs=int(k.k_legs.iloc[0]), kalshi_first=k.k_first.iloc[0],
                       kalshi_last=k.k_last.iloc[0])
        if "cme_implied_bp_t19" in out and "poly_implied_bp_t19" in out:
            out["basis_cents_t19"] = round(4 * (out["cme_implied_bp_t19"] - out["poly_implied_bp_t19"]), 3)
        rows.append(out)
    return pd.DataFrame(rows)


def main():
    OUT.mkdir(exist_ok=True)
    con = duckdb.connect(str(DB), read_only=True)
    inst = instruments(con)
    tables = {
        "fomc_instruments.csv": inst,
        "effr_month_profile.csv": effr_profile(con),
        "hedge_error.csv": hedge_error(con, inst),
        "venue_snapshot.csv": venue_snapshot(con, inst),
    }
    for name, df in tables.items():
        df.to_csv(OUT / name, index=False)
        print(f"{name:26s} {len(df):>4} rows x {len(df.columns):>2} cols")
    e = tables["hedge_error.csv"]
    print("\nhedge error by regime (cents per 25bp-equivalent contract):")
    for reg, g in e.groupby("regime"):
        a = g.err_cents.abs()
        print(f"  {reg:9s} n={len(g):2d}  MAE {a.mean():6.3f}  median {a.median():6.3f}  "
              f"p90 {a.quantile(.9):6.3f}  max {a.max():6.3f}  exact-zero {g.is_exact_zero.sum()}")


if __name__ == "__main__":
    main()
