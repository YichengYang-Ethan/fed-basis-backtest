"""Measure the ZQ calendar-spread hedge's tracking error. Resolves BLOCKER B6.

The quantity measured is the *intra-month EFFR drift differential* between two
adjacent delivery months: the realized spread between their calendar-day mean
EFFRs, minus the spread predicted by a flat post-decision rate.  This, not the
target-to-EFFR level pass-through residual, is what a calendar spread is exposed
to, because a clean spread cancels the level of R identically (PREREGISTRATION
Section 4.2) but does not cancel the difference in drift.

Method, conventions, results and the reserve that replaces C3 = 0.343c / 3.00c
are written up in ``prereg/TRACKING_ERROR.md``.  This script is the reproduction:

    python3 harness/measure_tracking_error.py

It reads only ``fred_rates`` and ``meetings`` -- no price data of any kind -- and
rewrites ``results/tracking_error_by_pair.csv``.  Instrument selection and the
contamination coefficient are pure functions of the published FOMC calendar (G4);
realized outcomes enter only the model path used to form the ex-post residual.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get("FOMC_DATA_ROOT", REPO / "local_data")).expanduser().resolve()
DB = str(Path(os.environ.get("FED_PRICING_DB", DATA_ROOT / "fed.duckdb")).expanduser())
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 60)
pd.set_option("display.max_rows", 200)

con = duckdb.connect(DB, read_only=True)

# ---------------------------------------------------------------- EFFR calendar
raw = con.execute(
    "SELECT date, effr, sofr, iorb, tgt_upper, tgt_lower, dff FROM fred_rates "
    "WHERE date >= DATE '2015-01-01' ORDER BY date"
).fetchdf()
raw["date"] = pd.to_datetime(raw["date"])
raw = raw.set_index("date")
assert len(pd.date_range(raw.index.min(), raw.index.max(), freq="D")) == len(raw)

eff = raw["effr"].ffill()
iorb = raw["iorb"].ffill()
EFFR_LAST_PUB = raw["effr"].last_valid_index()

vw = con.execute("SELECT date, effr_ff FROM v_effr_calendar WHERE date >= DATE '2021-12-01'").fetchdf()
vw["date"] = pd.to_datetime(vw["date"]); vw = vw.set_index("date")["effr_ff"]
ci = vw.index.intersection(eff.index)
CHK_VIEW = float((vw.loc[ci] - eff.loc[ci]).abs().max())
cm = eff.loc["2022-01-01":EFFR_LAST_PUB].index
CHK_DFF = int(((raw["dff"].ffill().loc[cm] - eff.loc[cm]).abs() > 1e-9).sum())

# ---------------------------------------------------------------- meetings
mt = con.execute(
    "SELECT meeting_date, meeting_month, effective_date, days_in_month, days_post, "
    "delta AS w_db, realized_change_bp FROM meetings ORDER BY meeting_date"
).fetchdf()
mt["meeting_date"] = pd.to_datetime(mt["meeting_date"])
mt["effective_date"] = pd.to_datetime(mt["effective_date"])
mt["chg"] = mt["realized_change_bp"].astype("Float64") / 100.0


def month_days(ym) -> pd.DatetimeIndex:
    start = pd.Timestamp(str(ym) + "-01")
    return pd.date_range(start, start + pd.offsets.MonthEnd(0), freq="D")


mt["w"] = [float((month_days(r["meeting_month"]) >= r["effective_date"]).sum())
           / len(month_days(r["meeting_month"])) for _, r in mt.iterrows()]
assert np.allclose(mt["w"], mt["w_db"]), "w reproduction failed"

step_check, bad = [], []
for _, r in mt.iterrows():
    if pd.isna(r["chg"]) or r["chg"] == 0 or r["effective_date"] > EFFR_LAST_PUB:
        continue
    d = float(eff.loc[r["effective_date"]] - eff.loc[r["effective_date"] - pd.Timedelta(days=1)])
    step_check.append((r["meeting_date"].date(), float(r["chg"]), round(d, 6)))
    if abs(d - float(r["chg"])) > 0.0051:
        bad.append((r["meeting_date"].date(), float(r["chg"]), round(d, 6)))

decided = mt[mt["realized_change_bp"].notna()].copy()
# calendar-only lookup: meeting held in month m (independent of outcome)
BY_MONTH = {r["meeting_month"]: r for _, r in mt.iterrows()}

# ---------------------------------------------------------------- monthly realized means
LAST_FULL = EFFR_LAST_PUB.to_period("M") - 1
months = pd.period_range("2021-12", LAST_FULL, freq="M")
M = pd.DataFrame({p: dict(
    n_days=len(month_days(p)),
    mean_effr=float(eff.loc[month_days(p)].mean()),
    mean_spread_bp=float((eff.loc[month_days(p)] - iorb.loc[month_days(p)]).mean() * 100.0),
    n_distinct=int(eff.loc[month_days(p)].nunique()),
) for p in months}).T

steps = decided[["effective_date", "chg"]].dropna().copy()
steps["chg"] = steps["chg"].astype(float)


def model_mean(period, anchor_date: pd.Timestamp, anchor_level: float) -> float:
    idx = month_days(period)
    out = np.full(len(idx), anchor_level, dtype=float)
    for _, s in steps.iterrows():
        ed, c = s["effective_date"], s["chg"]
        if ed > anchor_date:
            out += np.where(idx >= ed, c, 0.0)
        else:
            out -= np.where(idx < ed, c, 0.0)
    return float(out.mean())


# ---------------------------------------------------------------- per-pair sweep
rows = []
for E in pd.period_range("2022-01", LAST_FULL - 1, freq="M"):
    L = E + 1
    anchor_date = month_days(E)[0] - pd.Timedelta(days=1)
    r0 = float(eff.loc[anchor_date])
    me, ml = model_mean(E, anchor_date, r0), model_mean(L, anchor_date, r0)
    re_, rl = float(M.loc[E, "mean_effr"]), float(M.loc[L, "mean_effr"])
    dE, dL = (re_ - me) * 100.0, (rl - ml) * 100.0
    resid = ((re_ - rl) - (me - ml)) * 100.0
    assert abs(resid - (dE - dL)) < 1e-9
    a2 = anchor_date - pd.Timedelta(days=45)
    r2 = ((re_ - model_mean(E, a2, float(eff.loc[a2]))) -
          (rl - model_mean(L, a2, float(eff.loc[a2])))) * 100.0
    assert abs(r2 - resid) < 1e-9, (E, L)

    # --- CALENDAR-ONLY roles/contamination (G4-pure) ---
    mE, mL = BY_MONTH.get(str(E)), BY_MONTH.get(str(L))
    if mE is not None:                       # FRONT spread for the meeting held in E
        s_front = 1.0 - float(mE["w"])
        c_front = float(mL["w"]) if mL is not None else 0.0
        front_meeting = mE["meeting_date"].date()
    else:
        s_front = c_front = np.nan; front_meeting = None
    if mL is not None:                       # BACK spread for the meeting held in L
        s_back = float(mL["w"])
        c_back = (1.0 - float(mE["w"])) if mE is not None else 0.0
        back_meeting = mL["meeting_date"].date()
    else:
        s_back = c_back = np.nan; back_meeting = None

    rows.append(dict(
        pair=f"{E}/{L}", month_early=str(E), month_late=str(L),
        n_days_early=int(M.loc[E, "n_days"]), n_days_late=int(M.loc[L, "n_days"]),
        n_distinct_effr_early=int(M.loc[E, "n_distinct"]), n_distinct_effr_late=int(M.loc[L, "n_distinct"]),
        anchor_date=anchor_date.date(), anchor_effr=r0,
        real_mean_early=re_, real_mean_late=rl, model_mean_early=me, model_mean_late=ml,
        drift_early_bp=dE, drift_late_bp=dL,
        spread_real_bp=(re_ - rl) * 100.0, spread_model_bp=(me - ml) * 100.0,
        resid_bp=resid, abs_resid_bp=abs(resid),
        effr_less_iorb_early_bp=float(M.loc[E, "mean_spread_bp"]),
        effr_less_iorb_late_bp=float(M.loc[L, "mean_spread_bp"]),
        iorb_identity_bp=float(M.loc[E, "mean_spread_bp"]) - float(M.loc[L, "mean_spread_bp"]),
        front_meeting=front_meeting, s_front=s_front, c_front=c_front,
        front_clean=(c_front == 0.0) if mE is not None else None,
        resid_cents_front=(4.0 * abs(resid) / s_front) if (mE is not None and s_front > 0) else np.nan,
        back_meeting=back_meeting, s_back=s_back, c_back=c_back,
        back_clean=(c_back == 0.0) if mL is not None else None,
        resid_cents_back=(4.0 * abs(resid) / s_back) if (mL is not None and s_back > 0) else np.nan,
        regime=("pinned" if E < pd.Period("2025-09") else "drifting"),
        quarter_boundary=str(E).endswith(("-03", "-06", "-09", "-12")),
    ))
P = pd.DataFrame(rows)

# identity check: resid == mean(EFFR-IORB)_E - mean(EFFR-IORB)_L when IORB tracks the target
P["identity_err_bp"] = (P["resid_bp"] - P["iorb_identity_bp"]).abs()

print("=" * 100)
print("DATA INTEGRITY CHECKS")
print("=" * 100)
print(f"  EFFR last published                : {EFFR_LAST_PUB.date()}")
print(f"  last fully realized delivery month : {LAST_FULL}")
print(f"  my ffill vs v_effr_calendar ASOF    : max abs diff {CHK_VIEW:.1e}")
print(f"  my ffill(EFFR) vs DFF, 2022+        : {CHK_DFF} mismatched calendar days of {len(cm)}")
print(f"  w(M) reproduced for all 49 meetings : max diff {np.abs(mt['w']-mt['w_db']).max():.1e}")
print(f"  EFFR steps by exactly Delta on effective_date: {len(step_check)} changes, {len(bad)} off by >0.51bp")
print(f"  anchor-invariance of resid (re-anchored -45d): passed for all {len(P)} pairs")
print(f"  identity resid == d(EFFR-IORB): max err {P['identity_err_bp'].max():.4f}bp, "
      f"n>0.01bp = {(P['identity_err_bp']>0.01).sum()}")
print(f"  months where EFFR printed ONE value all month: {int((M['n_distinct']==1).sum())} of {len(M)}")


def dist(x, label):
    x = pd.Series(x).dropna().astype(float)
    if not len(x):
        return dict(label=label, n=0)
    a = x.abs()
    return dict(label=label, n=len(x), mean=x.mean(), MAE=a.mean(), sd=x.std(ddof=1),
                p50=a.median(), p90=a.quantile(.90), p95=a.quantile(.95), max=a.max())


print("\n" + "=" * 100)
print("(3) FULL DISTRIBUTION -- drift differential, ALL adjacent delivery-month pairs")
print("=" * 100)
print(f"pairs: {P['pair'].iloc[0]} .. {P['pair'].iloc[-1]}   n = {len(P)}")
print(pd.DataFrame([
    dist(P["resid_bp"], "resid_bp, all pairs"),
    dist(P[P.regime == "pinned"]["resid_bp"], "resid_bp, pinned 2022-01..2025-08"),
    dist(P[P.regime == "drifting"]["resid_bp"], "resid_bp, drifting 2025-09..2026-07"),
]).to_string(index=False))
print("\nsign structure of the drifting-regime residuals (one-signed bias check):")
dr = P[P.regime == "drifting"][["pair", "resid_bp", "effr_less_iorb_early_bp", "effr_less_iorb_late_bp"]]
print(dr.to_string(index=False))
nz = dr[dr.resid_bp.abs() > 1e-9]
print(f"  nonzero residuals: {len(nz)}, negative: {(nz.resid_bp<0).sum()}, positive: {(nz.resid_bp>0).sum()}")

print("\n=== worst 5 pairs by |resid_bp| ===")
W = P.nlargest(5, "abs_resid_bp")[[
    "pair", "resid_bp", "drift_early_bp", "drift_late_bp", "effr_less_iorb_early_bp",
    "effr_less_iorb_late_bp", "n_distinct_effr_early", "n_distinct_effr_late",
    "front_meeting", "s_front", "resid_cents_front", "back_meeting", "s_back", "resid_cents_back"]]
print(W.to_string(index=False))

# ---------------------------------------------------------------- (4) by structure
fr = P[P.front_meeting.notna()]
bk = P[P.back_meeting.notna()]
frc = fr[fr.front_clean == True]
bkc = bk[bk.back_clean == True]
print("\n" + "=" * 100)
print("(4) BY STRUCTURE -- residual in bp of the traded structure")
print("=" * 100)
print(pd.DataFrame([
    dist(P["resid_bp"], "ALL pairs"),
    dist(fr["resid_bp"], "FRONT candidates (all)"),
    dist(frc["resid_bp"], "FRONT clean (c=0)"),
    dist(bk["resid_bp"], "BACK candidates (all)"),
    dist(bkc["resid_bp"], "BACK clean (c=0)"),
]).to_string(index=False))
print("\n-- normalized: cents per 25bp-equivalent contract = 4*|resid_bp|/s --")
print(pd.DataFrame([
    dist(fr["resid_cents_front"], "FRONT candidates (cents)"),
    dist(frc["resid_cents_front"], "FRONT clean (cents)"),
    dist(bk["resid_cents_back"], "BACK candidates (cents)"),
    dist(bkc["resid_cents_back"], "BACK clean (cents)"),
]).to_string(index=False))
print(f"\nmean s: FRONT clean {frc.s_front.mean():.4f}   BACK clean {bkc.s_back.mean():.4f}")

# ---------------------------------------------------------------- outrights
out = []
for _, r in decided.iterrows():
    Mp = pd.Period(r["meeting_month"], freq="M")
    if Mp + 1 > LAST_FULL:
        continue
    ad = r["effective_date"] - pd.Timedelta(days=1)
    r0 = float(eff.loc[ad]); w = float(r["w"])
    resid_mm = -(float(M.loc[Mp, "mean_effr"]) - model_mean(Mp, ad, r0)) * 100.0
    resid_nx = -(float(M.loc[Mp + 1, "mean_effr"]) - model_mean(Mp + 1, ad, r0)) * 100.0
    nxt = BY_MONTH.get(str(Mp + 1))
    s_nx = 1.0 - (float(nxt["w"]) if nxt is not None else 0.0)
    out.append(dict(meeting=r["meeting_date"].date(), w=w, resid_mm_bp=resid_mm,
                    resid_mm_cents=(4 * abs(resid_mm) / w) if w > 0 else np.nan,
                    s_next=s_nx, resid_next_bp=resid_nx,
                    resid_next_cents=(4 * abs(resid_nx) / s_nx) if s_nx > 0 else np.nan))
O = pd.DataFrame(out)
print("\n-- OUTRIGHTS (G2-exposed Tier-3 fallback) --")
print(pd.DataFrame([
    dist(O["resid_mm_bp"], "meeting-month outright (bp)"),
    dist(O["resid_mm_cents"], "meeting-month outright (cents, s=w)"),
    dist(O["resid_next_bp"], "M+1 outright (bp)"),
    dist(O["resid_next_cents"], "M+1 outright (cents, s=1-c)"),
]).to_string(index=False))
print(f"  ({int(O['w'].eq(0).sum())} meetings have w=0 -> meeting-month outright undefined, excluded from cents)")

# ---------------------------------------------------------------- Section-4 selection
sel = []
for _, r in mt.iterrows():
    Mp = pd.Period(r["meeting_month"], freq="M")
    w = float(r["w"])
    cands = []
    nxt, prv = BY_MONTH.get(str(Mp + 1)), BY_MONTH.get(str(Mp - 1))
    cands.append(("FRONT", 1.0 - w, float(nxt["w"]) if nxt is not None else 0.0, f"{Mp}/{Mp+1}"))
    cands.append(("BACK", w, (1.0 - float(prv["w"])) if prv is not None else 0.0, f"{Mp-1}/{Mp}"))
    live = [c for c in cands if c[1] > 0]
    clean = [c for c in live if c[2] == 0]
    if clean:
        clean.sort(key=lambda c: (-c[1], 0 if c[0] == "FRONT" else 1)); pick, tier = clean[0], 1
    elif live:
        live.sort(key=lambda c: (c[2] / c[1], 0 if c[0] == "FRONT" else 1)); pick, tier = live[0], 2
    else:
        continue
    kind, s, c, pr = pick
    row = P[P["pair"] == pr]
    resid = float(row["resid_bp"].iloc[0]) if len(row) else np.nan
    sel.append(dict(meeting=r["meeting_date"].date(), instrument=kind, pair=pr, tier=tier,
                    s=s, c_over_s=c / s, resid_bp=resid,
                    resid_cents=(4 * abs(resid) / s) if pd.notna(resid) else np.nan,
                    decided=pd.notna(r["realized_change_bp"])))
S = pd.DataFrame(sel)
S["breach_K3"] = S["resid_cents"] > 3.00

# cross-check against the Section 3 table of the pre-registration
PREREG = {"2026-10-28": ("FRONT", 0.903226), "2026-12-09": ("BACK", 0.709677),
          "2025-10-29": ("FRONT", 0.935484), "2025-12-10": ("BACK", 0.677419),
          "2026-01-28": ("FRONT", 0.903226), "2026-03-18": ("BACK", 0.419355),
          "2026-04-29": ("FRONT", 0.966667), "2026-06-17": ("BACK", 0.433333),
          "2026-07-29": ("FRONT", 0.935484), "2026-09-16": ("BACK", 0.466667),
          "2025-09-17": ("BACK", 0.433333), "2022-06-15": ("FRONT", 0.500000),
          "2023-06-14": ("BACK", 0.533333), "2024-12-18": ("FRONT", 0.580645),
          "2025-06-18": ("FRONT", 0.633333), "2024-01-31": ("FRONT", 1.000000)}
mism = []
for k, (kind, s) in PREREG.items():
    row = S[S.meeting.astype(str) == k]
    if not len(row) or row.instrument.iloc[0] != kind or abs(row.s.iloc[0] - s) > 1e-6:
        mism.append((k, kind, s, None if not len(row) else (row.instrument.iloc[0], round(row.s.iloc[0], 6))))
print(f"\nSelector vs pre-registration Section 3 fixture: {len(mism)} mismatches of {len(PREREG)} spot-checked")
if mism:
    print(mism)

Sd = S[S.decided & S.resid_bp.notna()]
print("\n" + "=" * 100)
print("SECTION-4-SELECTED INSTRUMENT, measured residual per decided meeting")
print("=" * 100)
print(Sd[["meeting", "instrument", "pair", "tier", "s", "c_over_s", "resid_bp", "resid_cents", "breach_K3"]]
      .round(4).to_string(index=False))
S1 = Sd[Sd.tier == 1]
print("\n" + pd.DataFrame([
    dist(S1["resid_bp"], "selected Tier-1 (bp)"),
    dist(S1["resid_cents"], "selected Tier-1 (cents)"),
    dist(Sd["resid_cents"], "selected all tiers (cents)"),
]).to_string(index=False))
print(f"K3 breaches (>3.00c): Tier-1 {int(S1.breach_K3.sum())}/{len(S1)}, all {int(Sd.breach_K3.sum())}/{len(Sd)}")

arm2 = Sd[Sd.meeting >= dt.date(2025, 10, 29)]
print("\nARM-2 window (2025-10-29 onward, decided):")
print(arm2[["meeting", "instrument", "pair", "s", "resid_bp", "resid_cents", "breach_K3"]].round(4).to_string(index=False))
print(f"K3 breaches in ARM-2 window: {int(arm2.breach_K3.sum())} of {len(arm2)}")

drift_sel = Sd[Sd.meeting >= dt.date(2025, 9, 1)]
print("\nDrifting-regime selected instruments (2025-09 onward):")
print(pd.DataFrame([dist(drift_sel["resid_bp"], "bp"), dist(drift_sel["resid_cents"], "cents")]).to_string(index=False))

# ---------------------------------------------------------------- (5) IORB without target change
print("\n" + "=" * 100)
print("(5) IORB CHANGE WITHOUT TARGET-RANGE CHANGE")
print("=" * 100)
io = raw[["iorb", "tgt_upper", "effr"]].copy()
io_valid = io.dropna(subset=["iorb"]).copy()
print(f"IORB series coverage: {io_valid.index.min().date()} .. {io_valid.index.max().date()} "
      f"(FRED IORB starts 2021-07-29; IOER, its pre-2021 predecessor, is NOT in this database)")
iof = io.ffill()
iof = iof.loc[io_valid.index.min():]
d_i, d_t = iof["iorb"].diff(), iof["tgt_upper"].diff()
# a row whose tgt_upper was never published cannot be compared -- publication-lag artifact
published_tgt = io["tgt_upper"].notna()
cand = iof[(d_i.abs() > 1e-9) & (d_t.abs() < 1e-9)]
for d in cand.index:
    art = not bool(published_tgt.loc[d])
    pre = eff.loc[d - pd.Timedelta(days=10):d - pd.Timedelta(days=1)].mean()
    post = eff.loc[d:min(d + pd.Timedelta(days=9), eff.index.max())].mean()
    print(f"  {d.date()}  d_iorb={d_i.loc[d]*100:+.1f}bp  tgt_upper_published_that_day={not art}  "
          f"EFFR(-10d)={pre:.4f} EFFR(+10d)={post:.4f}  response={100*(post-pre):+.2f}bp"
          + ("   <-- ARTIFACT: target series not yet loaded for this date, not a technical adjustment" if art else ""))
if len(cand) == 0:
    print("  none")
genuine = [d for d in cand.index if bool(published_tgt.loc[d])]
print(f"  genuine technical adjustments in the IORB-covered sample: {len(genuine)}")
tgt_moves = iof[d_t.abs() > 1e-9].index
orphan = [d.date() for d in tgt_moves if d not in set(mt["effective_date"])]
print(f"  target-range moves with no matching FOMC effective_date: {orphan if orphan else 'none'}")

# ---------------------------------------------------------------- write CSV
os.makedirs(f"{REPO}/results", exist_ok=True)
cols = ["pair", "month_early", "month_late", "regime", "n_days_early", "n_days_late",
        "n_distinct_effr_early", "n_distinct_effr_late", "anchor_date", "anchor_effr",
        "real_mean_early", "real_mean_late", "model_mean_early", "model_mean_late",
        "drift_early_bp", "drift_late_bp", "spread_real_bp", "spread_model_bp",
        "resid_bp", "abs_resid_bp", "effr_less_iorb_early_bp", "effr_less_iorb_late_bp",
        "iorb_identity_bp", "identity_err_bp",
        "front_meeting", "s_front", "c_front", "front_clean", "resid_cents_front",
        "back_meeting", "s_back", "c_back", "back_clean", "resid_cents_back",
        "quarter_boundary"]
Pout = P[cols].copy()
for c in Pout.columns:
    if Pout[c].dtype.kind == "f":
        Pout[c] = Pout[c].round(6)
Pout.to_csv(f"{REPO}/results/tracking_error_by_pair.csv", index=False)
print(f"\nwrote {REPO}/results/tracking_error_by_pair.csv ({len(Pout)} rows, {len(cols)} cols)")

# ---------------------------------------------------------------- proposed reserve
print("\n" + "=" * 100)
print("PROPOSED RESERVE")
print("=" * 100)
drift_pairs = P[P.regime == "drifting"]["resid_bp"]
all_pairs = P["resid_bp"]
for nm, x in [("all pairs 2022-01..2026-07", all_pairs),
              ("drifting regime 2025-09..2026-07", drift_pairs),
              ("selected Tier-1 instruments (all)", S1["resid_bp"]),
              ("selected instruments, 2025-09+", drift_sel["resid_bp"])]:
    a = x.abs()
    print(f"  {nm:38s} n={len(x):3d}  MAE={a.mean():.4f}bp  p95={a.quantile(.95):.4f}bp  max={a.max():.4f}bp")
print("\n  cents at representative spans (C3 = 4 * R_bp / s):")
hdr = [1.0, 0.935484, 0.903226, 0.709677, 0.466667, 0.433333, 0.419355]
for nm, R in [("MAE all pairs", float(all_pairs.abs().mean())),
              ("MAE drifting regime", float(drift_pairs.abs().mean())),
              ("max drifting regime", float(drift_pairs.abs().max()))]:
    print(f"    {nm:22s} R={R:.4f}bp  " + "  ".join(f"s={s:.3f}:{4*R/s:6.3f}c" for s in hdr))

# worked-example dump
print("\n" + "=" * 100)
print("WORKED EXAMPLE: 2025-10-29 meeting, Tier-1 FRONT Oct/Nov 2025")
print("=" * 100)
rw = P[P.pair == "2025-10/2025-11"].iloc[0]
for k in ["anchor_date", "anchor_effr", "real_mean_early", "real_mean_late", "model_mean_early",
          "model_mean_late", "drift_early_bp", "drift_late_bp", "spread_model_bp",
          "spread_real_bp", "resid_bp", "s_front", "resid_cents_front", "iorb_identity_bp"]:
    print(f"  {k:22s} = {rw[k]}")
print("\n  Oct-2025 daily EFFR (calendar-day ffill):")
print("   ", " ".join(f"{d.day}:{v:.2f}" for d, v in eff.loc["2025-10-01":"2025-10-31"].items()))
print("  Nov-2025 daily EFFR:")
print("   ", " ".join(f"{d.day}:{v:.2f}" for d, v in eff.loc["2025-11-01":"2025-11-30"].items()))
