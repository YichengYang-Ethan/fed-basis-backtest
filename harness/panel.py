"""Lookahead-safe event-time panel builder for the Kalshi/ZQ Fed-basis test.

Turns the raw stores into a DataFrame indexed by UTC instant in which EVERY
value was public at or before its row's timestamp.  Nothing here computes a
P&L, a threshold, or a signal; this file only decides *what was knowable when*.

Gates implemented in this file
------------------------------
G1  Timestamp alignment.  Each source carries an ``available_at`` instant
    resolved in ``data_contract`` from that store's own labelling convention
    (Kalshi ``ts`` is the bar END and is usable AT that instant; IBKR intraday
    bars are labelled by bar START and are usable only at start+duration; the
    ZQ daily settle is treated as public at ``ZQ_SETTLE_AVAILABLE_ET``).  Every
    column is filled by a backward as-of join on that instant, never by a
    same-calendar-date join.  The daily panel is indexed at 16:00 ET rather
    than at midnight precisely so that the Kalshi leg is read from the 60-min
    (or 1-min) candle ending at the ZQ settle instant instead of from a daily
    Kalshi bar that closes eight hours later.  ``verify_no_lookahead`` proves
    the result: ``max(all *_as_of) <= index`` on every row, or the build raises.

G2  EFFR publication lag.  ``effr_asof`` returns the EFFR that was published at
    or before ``t``, where observation date d becomes public at 09:00 ET on the
    next EFFR publication date (the next New York Fed business day, derived
    from the print calendar itself, not from a weekday rule).  The panel also
    reports ``zq_hold_anchor_bp``: for a clean calendar spread the flat-R
    anchor is 0.0 because the unknown prevailing rate R cancels out of the
    spread, so this gate does not bind at all there.
    ``instrument.requires_effr`` says which case a given meeting is in, and the
    panel's ``attrs`` record it.  Cancelling R removes the PUBLICATION LAG; it
    does not make the anchor empirically zero -- see KNOWN RESIDUAL below.

G4  Contract selection is a pure function of the FOMC calendar.
    ``fomc_calendar`` and ``select_instrument`` never see a price.  The only
    market-derived input ``select_instrument`` accepts is the SET of contract
    symbols a source lists, which is metadata, not data.

G5  Universe declaration.  ``meeting_universe`` enumerates every FOMC meeting in
    the requested window with an explicit ``included`` flag and
    ``exclusion_reason``.  Readers raise naming what is missing instead of
    returning a short frame.

G7  Execution/depth, the observable half.  The panel carries per-leg Kalshi
    ``bid``/``ask``/``vol``/``oi`` and the source bar length, so a size cap can
    be set from volume that was already printed at ``t``.  ZQ depth and spread
    are NOT observable in any store (IBKR ``BID_ASK`` bars are degenerate for
    ZQ: open == close on every bar), so the ZQ crossing cost is an assumption.
    ``Instrument.zq_friction_cents_per_kalshi_contract`` prices that assumption
    at one tick per leg and makes the 1/w blow-up explicit.

G3 (fee anachronism) and G6 (threshold derivation) belong to the P&L engine and
are deliberately absent here; this file supplies the inputs they need
(``Instrument.zq_friction_cents_per_kalshi_contract``, per-leg volumes) and
nothing else.

Structural facts this file encodes
----------------------------------
ZQ settles at 100 minus the arithmetic mean of daily EFFR over the delivery
month.  For delivery month D and a decision effective on date e,

    w(D, e) = #{days of D on or after e} / #{days of D}

and the month average is ``R + sum_j delta_j * w(D, e_j)`` over every decision
j effective inside D.  Hence:

* an outright in month D has span ``h = 25bp * w(D, e)`` and a hold-state
  anchor of R, which must be estimated (G2 binds);
* the calendar spread ``far - near`` has span ``h = 25bp * (w_far - w_near)``
  and, when no other meeting lands in either month, a hold-state anchor of
  exactly 0 -- R cancels (G2 does not bind).  This is why the spread is
  preferred wherever it is clean.

Sign convention: ``zq_span_bp`` is the instrument's implied RATE quantity in
basis points, defined so that a +25bp hike raises it by ``h_bp`` in both cases.
For an outright that is the implied month-average rate ``(100 - F) * 100``; for
the spread it is ``(F_near - F_far) * 100``, i.e. avg_far - avg_near.  The CME
normalised state price for a 25bp hike is then

    q_cme = (zq_span_bp - zq_hold_anchor_bp) / h_bp

which is the ``q_cme`` column.

Hedge ratio and friction: N = 4167 * h(price points) = 41.67 * h_bp Kalshi
contracts per ZQ unit, so a one-tick ZQ crossing cost ($10.4175) is
``25 / h_bp`` cents per Kalshi contract per leg crossed.  Hedging a meeting
with its OWN month when w is small is what makes that number explode.

KNOWN RESIDUAL -- read before trusting ``q_cme``
------------------------------------------------
The w arithmetic is exact: reconstructing October 2025 as
``(1-w)*avg(Oct 1-29) + w*avg(Oct 30-31)`` with w = 2/31 reproduces the
realized month average to 0.0e+00.  What is NOT exact is the two-state model's
assumption that EFFR is FLAT at R before the decision and flat at R+delta
after.  ZQ averages every calendar day of the month, so any intra-month drift
in EFFR enters the contract and therefore enters ``q_cme``.

Measured on the decided meetings the stores cover (2025-09 .. 2026-07, n=8),
the post-decision residual of the flat-R model was:

    outright  n=4  mean |resid| 1.05 cents, max 2.31 cents of state price
    spread    n=4  mean |resid| 3.90 cents, max 12.34 cents of state price

The worst case, 2025-10-29, decomposes exactly: October's realized average
EFFR ran 1.66bp BELOW its terminal pre-cut print (reserve pressure through the
month) and November's ran 0.63bp above, so the realized Oct/Nov spread was
-21.17bp against the flat-R prediction of -23.39bp.  Nothing was mispriced and
nothing is wrong with the panel; the flat-R model is what is approximate.

Two consequences the P&L engine must not ignore:

1.  A clean calendar spread cancels the LEVEL of R but not the DIFFERENCE in
    intra-month drift between its two delivery months.  "R cancels" is a
    statement about G2, the publication lag -- it is not a claim that the
    hold-state anchor is empirically zero.  Pass ``hold_anchor_bp=`` to carry
    a drift model instead of the flat-R default.
2.  Anchor error is amplified by 100/h_bp cents per bp
    (``Instrument.anchor_sensitivity_cents_per_bp``).  At h = 10.8bp, one basis
    point of drift is 9.2 cents of measured state price -- larger than the
    ~2 cent ZQ friction and larger than the edge the strategy claims.  A basis
    that is persistently one-signed (the prior result's 61% positive days) is
    the signature of exactly this bias, so G6's threshold must be derived above
    the drift term, not just above fees plus spread.

Raising, not guessing
---------------------
Every function raises on ambiguity.  Forward fill is opt-in (``ffill=True``);
without it a value older than its source's natural bar length is dropped rather
than carried, and ``*_staleness_sec`` always reports the age of the last
observation whether it was used or not.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Mapping, NamedTuple, Sequence

import numpy as np
import pandas as pd

import data_contract as dc
from data_contract import (  # noqa: F401  (re-exported constants are part of the contract)
    CANONICAL_LEGS,
    DataContractError,
    StoreMissingError,
    ZQ_DOLLARS_PER_BP,
    ZQ_DOLLARS_PER_POINT,
    ZQ_TICK_BP,
    ZQ_TICK_DOLLARS,
    connect,
    delivery_month_str,
    read_effr,
    read_kalshi_candles,
    read_meetings,
    read_poly_prices,
    read_zq_daily,
    read_zq_intraday,
    zq_symbol,
)

ET = "America/New_York"

# --------------------------------------------------------------------------
# Named assumptions.  Each is a modelling choice, not a measurement.
# --------------------------------------------------------------------------

#: Instant at which a ZQ daily settle is treated as public.  CME publishes ZQ
#: settlements earlier in the afternoon than this; assuming a LATER publication
#: can only cost information, never create lookahead, so 16:00 ET is the safe
#: default.  Moving it earlier requires evidence.
ZQ_SETTLE_AVAILABLE_ET = dt.time(16, 0)

#: EFFR for observation date d is published the next business morning.
EFFR_PUBLICATION_TIME_ET = dt.time(9, 0)

#: Scheduled FOMC statement release.  Verified for 2026-07-29, where ZQ Aug
#: moved 96.3000 -> 96.3625 at 13:00 CT.
ANNOUNCEMENT_TIME_ET = dt.time(14, 0)

#: Largest EFFR publication gap treated as normal (a three-day weekend plus a
#: bank holiday).  Since 2022 the observed maximum is 4 calendar days.
MAX_EFFR_GAP_DAYS = 5

#: Default freshness windows in seconds.  A value older than this is NOT
#: carried into a row unless ``ffill=True``.  ``None`` means "use the source
#: bar's own length" (Kalshi candle interval, Polymarket fidelity).
DEFAULT_FRESH_WINDOW_SEC: dict[str, float | None] = {
    "kalshi": None,
    "poly": None,
    "zq": 86_400.0,
    "effr": MAX_EFFR_GAP_DAYS * 86_400.0,
}

#: Rate change in bp attributed to each canonical leg.  CUT50P and HIKE50P are
#: "50 or more" buckets, so +/-50 is an ASSUMPTION; it is exact whenever those
#: legs are near zero (they are, on every meeting in the store) and understates
#: the tail otherwise.  Change ``TAIL_LEG_BP`` to test sensitivity.
TAIL_LEG_BP = 50.0
LEG_BP: dict[str, float] = {
    "CUT50P": -TAIL_LEG_BP, "CUT25": -25.0, "HOLD": 0.0, "HIKE25": 25.0, "HIKE50P": TAIL_LEG_BP,
}

#: Polymarket's ladder changed shape on 2026-06-17.  Before it, the leg this
#: database stores as ``HIKE50P`` is really the question "increases by 25+ bps"
#: -- ANY hike -- and there is no separate HIKE25 leg at all (verified: 11 of
#: 17 primary events since 2025-01 have 4 legs, and
#: ``poly_event_meeting.hike50p_is_any_hike`` is True on exactly those).
#: Weighting that leg at 50bp, or comparing it to a Kalshi HIKE25 leg, is an
#: apples-to-oranges error, so the scheme is chosen per event from the flag.
POLY_ANY_HIKE_LEG_BP: dict[str, float] = {
    "CUT50P": -TAIL_LEG_BP, "CUT25": -25.0, "HOLD": 0.0, "HIKE50P": 25.0,
}

#: Below this span a hedge is dominated by the ZQ tick (25 / h_bp cents per
#: Kalshi contract), so ``select_instrument`` will not choose a clean-but-tiny
#: outright over a contaminated-but-wide one without saying so.
DEFAULT_MIN_SPAN_BP = 5.0


# --------------------------------------------------------------------------
# Calendar arithmetic (G4: nothing below this line reads a price)
# --------------------------------------------------------------------------


def month_bounds(year: int, month: int) -> tuple[dt.date, dt.date, int]:
    """(first day, last day, number of days) of a calendar month."""
    first = dt.date(year, month, 1)
    last = (pd.Timestamp(first) + pd.offsets.MonthEnd(0)).date()
    return first, last, (last - first).days + 1


def add_months(year: int, month: int, k: int) -> tuple[int, int]:
    idx = (year * 12 + (month - 1)) + k
    return idx // 12, idx % 12 + 1


def weight_in_month(effective_date: dt.date, year: int, month: int) -> float:
    """Fraction of delivery month ``(year, month)`` spent at the post-decision rate.

    ``w = #{days d in the month : d >= effective_date} / days_in_month``.
    Zero when the decision takes effect after the month ends, one when it takes
    effect on or before the first of the month.
    """
    first, last, n = month_bounds(year, month)
    if effective_date > last:
        return 0.0
    if effective_date <= first:
        return 1.0
    return ((last - effective_date).days + 1) / n


def fomc_calendar(
    *,
    con=None,
    months_ahead: Sequence[int] = (0, 1),
    verify: bool = True,
) -> pd.DataFrame:
    """One row per (meeting, candidate delivery month), from the calendar alone.

    Columns
    -------
    meeting_date, effective_date, meeting_month
        As stored.  ``effective_date`` is the first calendar day on which EFFR
        prints the new rate, which is decision day + 1 except when that day has
        no print (2025-06-18 -> 2025-06-20, Juneteenth), because ZQ settlement
        carries the previous business day's rate forward.
    months_ahead, delivery_month, zq_symbol, month_first, month_last, days_in_month
        The candidate contract.  ``months_ahead=0`` is the meeting's own month,
        ``1`` the next.
    w_self, days_self
        This decision's weight in that contract (the exact w of the theory).
    contaminants
        Tuple of ``(meeting_date, w)`` for every LATER meeting whose effective
        date also lands inside that delivery month, i.e. unhedged decisions the
        contract is also exposed to.  Empty tuple means the contract prices
        this decision and nothing else.
    n_contaminants, contamination_w
        Count and sum of the above; ``25 * contamination_w`` bp is the size of
        the unhedged exposure per 25bp of the other decisions.

    ``verify=True`` cross-checks ``w_self`` against the ``days_post``/``delta``
    columns stored in the meetings table and raises on any mismatch.
    """
    if con is None:
        with connect() as c:
            return fomc_calendar(con=c, months_ahead=months_ahead, verify=verify)

    meetings = read_meetings(con)
    eff = dict(zip(meetings["meeting_date"], meetings["effective_date"]))

    rows = []
    for m in meetings.itertuples(index=False):
        my, mm = m.meeting_date.year, m.meeting_date.month
        for k in months_ahead:
            y, mo = add_months(my, mm, k)
            first, last, n = month_bounds(y, mo)
            w_self = weight_in_month(m.effective_date, y, mo)
            contaminants = tuple(
                (d, weight_in_month(e, y, mo))
                for d, e in eff.items()
                if d > m.meeting_date and weight_in_month(e, y, mo) > 0.0
            )
            rows.append(
                {
                    "meeting_date": m.meeting_date,
                    "effective_date": m.effective_date,
                    "meeting_month": m.meeting_month,
                    "months_ahead": k,
                    "delivery_month": delivery_month_str(y, mo),
                    "zq_symbol": zq_symbol(y, mo),
                    "month_first": first,
                    "month_last": last,
                    "days_in_month": n,
                    "days_self": round(w_self * n),
                    "w_self": w_self,
                    "contaminants": contaminants,
                    "n_contaminants": len(contaminants),
                    "contamination_w": sum(w for _, w in contaminants),
                }
            )

    cal = pd.DataFrame(rows)

    if verify:
        own = cal[cal["months_ahead"] == 0].merge(
            meetings[["meeting_date", "days_post", "delta", "days_in_month"]],
            on="meeting_date",
            suffixes=("", "_stored"),
        )
        bad = own[
            (own["days_self"] != own["days_post"])
            | ((own["w_self"] - own["delta"]).abs() > 1e-9)
            | (own["days_in_month"] != own["days_in_month_stored"])
        ]
        if not bad.empty:
            raise DataContractError(
                "recomputed meeting-month weights disagree with the stored "
                f"days_post/delta for: {bad['meeting_date'].tolist()}"
            )
    return cal


# --------------------------------------------------------------------------
# Instrument selection (G4)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Instrument:
    """The ZQ structure used to hedge one decision, chosen from the calendar alone."""

    kind: str                      # 'spread' | 'outright'
    meeting_date: dt.date
    near_month: str | None         # 'YYYY-MM' of the short/near leg (None for a far outright)
    far_month: str | None          # 'YYYY-MM' of the long/far leg (None for a near outright)
    near_contract: str | None
    far_contract: str | None
    w_near: float
    w_far: float
    h_bp: float                    # price move per +25bp decision, in bp of rate
    requires_effr: bool            # True when the hold anchor is R (G2 binds)
    clean: bool                    # no other meeting lands in either leg's month
    contaminants: tuple[tuple[dt.date, float], ...]
    legs_crossed: int
    reason: str

    def as_tuple(self) -> tuple:
        """``('spread', near_month, far_month)`` or ``('outright', month)``."""
        if self.kind == "spread":
            return ("spread", self.near_month, self.far_month)
        return ("outright", self.near_month or self.far_month)

    @property
    def contracts(self) -> tuple[str, ...]:
        return tuple(c for c in (self.near_contract, self.far_contract) if c)

    @property
    def hedge_ratio(self) -> float:
        """Kalshi contracts per ZQ unit: N = 4167 * h(price points)."""
        return ZQ_DOLLARS_PER_POINT * self.h_bp / 100.0

    @property
    def zq_friction_cents_per_kalshi_contract(self) -> float:
        """One ZQ tick per crossed leg, expressed per Kalshi contract, in cents.

        ``= 100 * legs_crossed * $10.4175 / N = legs_crossed * 25 / h_bp``.
        This is the 1/w blow-up in one number: hedging a 2-days-of-month
        decision with its own month costs ~15 cents a contract, hedging it with
        a near-w=1 structure costs ~1 cent.
        """
        return 100.0 * self.legs_crossed * ZQ_TICK_DOLLARS / self.hedge_ratio

    @property
    def contamination_bp_per_25(self) -> float:
        """Unhedged span, in bp, per 25bp moved by the contaminating decisions."""
        return 25.0 * sum(w for _, w in self.contaminants)

    @property
    def anchor_sensitivity_cents_per_bp(self) -> float:
        """Cents of implied state price per 1bp of error in the hold anchor.

        ``= 100 / h_bp``.  This is why a small span is dangerous twice over:
        it multiplies the ZQ tick AND it multiplies every basis point of
        anchor error.  At h = 10.8bp one basis point of intra-month EFFR drift
        moves the measured ``q_cme`` by 9.2 cents, which is larger than any
        edge the strategy claims.
        """
        return 100.0 / self.h_bp


def select_instrument(
    meeting: dt.date | str,
    *,
    calendar: pd.DataFrame | None = None,
    con=None,
    available_contracts: Iterable[str] | None = None,
    min_span_bp: float = DEFAULT_MIN_SPAN_BP,
    spread_legs_crossed: int = 2,
) -> Instrument:
    """Choose the ZQ structure for one decision from the FOMC calendar (G4).

    Preference order, all decidable a year ahead from the published calendar:

    1. ``('spread', M, M+1)`` when both months are free of other meetings.  The
       unknown prevailing rate R cancels out of a calendar spread, so the EFFR
       publication lag (G2) stops mattering entirely; this is why the spread
       wins wherever it is available.  Span ``h = 25bp * (1 - w_M)``, which is
       near its maximum exactly when the meeting-month outright is at its worst.
    2. ``('outright', M+1)`` when M+1 is clean but M is not -- drop the dirty
       leg rather than inherit its exposure.  Anchor is R, so G2 binds.
    3. ``('outright', M)`` when M is clean but M+1 is not, provided the span
       clears ``min_span_bp``; a clean 1.6bp span costs ~15 cents a contract to
       cross and is usually worse than a wider contaminated one.
    4. Otherwise the widest available structure, with ``clean=False`` and a
       reason naming the contaminating meetings, for the caller to exclude.

    ``available_contracts`` (symbols a ZQ source lists) only removes legs that
    do not exist; it carries no price information.  ``spread_legs_crossed=2``
    assumes the spread is legged; pass 1 if it is executed as an exchange
    calendar spread with its own quote.
    """
    meeting = pd.Timestamp(meeting).date()
    if calendar is None:
        calendar = fomc_calendar(con=con, months_ahead=(0, 1))
    rows = calendar[calendar["meeting_date"] == meeting]
    if rows.empty:
        raise KeyError(f"{meeting} is not an FOMC meeting date in the calendar")
    near = rows[rows["months_ahead"] == 0]
    far = rows[rows["months_ahead"] == 1]
    if near.empty or far.empty:
        raise ValueError("calendar must contain months_ahead 0 and 1 to select an instrument")
    near, far = near.iloc[0], far.iloc[0]

    have = set(available_contracts) if available_contracts is not None else None
    near_ok = have is None or near["zq_symbol"] in have
    far_ok = have is None or far["zq_symbol"] in have
    if not near_ok and not far_ok:
        raise DataContractError(
            f"neither {near['zq_symbol']} nor {far['zq_symbol']} exists in the requested ZQ source"
        )

    near_clean = near["n_contaminants"] == 0
    far_clean = far["n_contaminants"] == 0

    def _spread() -> Instrument:
        h = 25.0 * (far["w_self"] - near["w_self"])
        return Instrument(
            kind="spread",
            meeting_date=meeting,
            near_month=near["delivery_month"],
            far_month=far["delivery_month"],
            near_contract=near["zq_symbol"],
            far_contract=far["zq_symbol"],
            w_near=float(near["w_self"]),
            w_far=float(far["w_self"]),
            h_bp=float(h),
            requires_effr=False,
            clean=True,
            contaminants=(),
            legs_crossed=spread_legs_crossed,
            reason=(
                f"calendar spread {near['zq_symbol']}/{far['zq_symbol']}: both delivery months "
                f"are free of other FOMC decisions, so the prevailing rate R cancels out of the "
                f"spread and the EFFR publication lag does not bind (G2). "
                f"h = 25bp * (w_far {far['w_self']:.4f} - w_near {near['w_self']:.4f}) = {h:.3f}bp."
            ),
        )

    def _outright(row, side: str, clean: bool, why: str) -> Instrument:
        h = 25.0 * float(row["w_self"])
        return Instrument(
            kind="outright",
            meeting_date=meeting,
            near_month=row["delivery_month"] if side == "near" else None,
            far_month=row["delivery_month"] if side == "far" else None,
            near_contract=row["zq_symbol"] if side == "near" else None,
            far_contract=row["zq_symbol"] if side == "far" else None,
            w_near=float(row["w_self"]) if side == "near" else 0.0,
            w_far=float(row["w_self"]) if side == "far" else 0.0,
            h_bp=h,
            requires_effr=True,
            clean=clean,
            contaminants=tuple(row["contaminants"]),
            legs_crossed=1,
            reason=why + (
                f" Outright {row['zq_symbol']}: h = 25bp * w {row['w_self']:.4f} = {h:.3f}bp. "
                "The hold-state anchor is the prevailing rate R, so the EFFR publication lag "
                "binds (G2) and effr_asof must be used."
            ),
        )

    if near_ok and far_ok and near_clean and far_clean:
        return _spread()

    if far_ok and far_clean:
        why = (
            f"spread rejected: the meeting month {near['delivery_month']} also contains "
            f"{near['n_contaminants']} other FOMC decision(s) "
            f"{[str(d) for d, _ in near['contaminants']]}, which the spread would inherit."
            if near_ok and not near_clean
            else f"spread rejected: {near['zq_symbol']} is not listed in the requested ZQ source."
        )
        return _outright(far, "far", True, why)

    if near_ok and near_clean:
        h_near = 25.0 * float(near["w_self"])
        why = (
            f"spread rejected: the next month {far['delivery_month']} contains "
            f"{far['n_contaminants']} other FOMC decision(s) "
            f"{[str(d) for d, _ in far['contaminants']]} with total weight "
            f"{far['contamination_w']:.4f}."
        )
        if h_near >= min_span_bp or not far_ok:
            return _outright(near, "near", True, why)
        inst = _outright(far, "far", False, why)
        return replace(
            inst,
            reason=(
                why
                + f" The clean alternative (outright {near['zq_symbol']}, h = {h_near:.3f}bp) is "
                f"below min_span_bp={min_span_bp}bp and would cost {25.0 / h_near:.2f} cents per "
                "Kalshi contract to cross, so the contaminated wider contract is reported instead "
                "with clean=False." + inst.reason[len(why):]
            ),
        )

    row, side = (far, "far") if far_ok else (near, "near")
    why = (
        f"no clean structure exists: {near['delivery_month']} carries "
        f"{[str(d) for d, _ in near['contaminants']]} and {far['delivery_month']} carries "
        f"{[str(d) for d, _ in far['contaminants']]}."
    )
    return _outright(row, side, False, why)


def available_zq_contracts(source: str, *, con=None) -> set[str]:
    """Contract symbols a ZQ source lists.  Metadata only -- no prices (G4)."""
    if source == "ibkr":
        dc.require_stores(need_ibkr=True)
        return set(pd.read_parquet(dc.IBKR_ZQ_PARQUET, columns=["contract"])["contract"].unique())
    if source == "cme_zq":
        if con is None:
            raise ValueError("source='cme_zq' requires an open DuckDB connection")
        return set(
            con.execute("select distinct contract from cme_zq where contract <> 'ZQ=F'")
            .df()["contract"]
            .dropna()
        )
    raise ValueError(f"unknown ZQ source {source!r}")


def select_zq_source(
    contracts: Sequence[str], *, con=None, prefer: str = "ibkr", require_all: bool = True
) -> str:
    """Pick the ONE source that covers the legs; sources are never mixed in a panel.

    IBKR is preferred because it is the exchange settle and retains expired
    contracts back to LTD 2025-09-30; ``cme_zq`` (yfinance) is the fallback for
    deferred months IBKR does not list.  Where both cover a contract they agree
    to float32 precision (mean |diff| 2e-6 over 400+ shared days), so the
    fallback does not change the numbers, only the reach.

    With ``require_all=False`` a source covering at least one leg is accepted,
    which lets ``build_panel`` degrade a spread to an outright instead of
    failing outright.
    """
    wanted = set(contracts)
    order = [prefer] + [s for s in ("ibkr", "cme_zq") if s != prefer]
    have, errs = {}, {}
    for src in order:
        try:
            have[src] = available_zq_contracts(src, con=con)
        except (StoreMissingError, ValueError) as exc:
            have[src], errs[src] = set(), str(exc)
    for src in order:
        if wanted <= have.get(src, set()):
            return src
    if not require_all:
        for src in order:
            if wanted & have.get(src, set()):
                return src
    raise DataContractError(
        f"no single ZQ source covers {sorted(wanted)} (sources are never mixed inside a panel). "
        + " | ".join(
            f"{s}: {errs[s]}" if s in errs
            else f"{s}: has {sorted(wanted & have[s]) or 'none of them'}"
            for s in order
        )
    )


def _resolve_instrument(
    meeting: dt.date,
    *,
    calendar: pd.DataFrame,
    con,
    zq_source: str,
    min_span_bp: float,
) -> tuple[Instrument, str]:
    """Pick the ZQ source, then re-select the instrument against what it lists.

    Two passes on purpose: the first is the calendar's preferred structure, the
    second drops any leg the chosen source does not carry.  Both passes see
    only symbols, never prices (G4).
    """
    probe = select_instrument(meeting, calendar=calendar, min_span_bp=min_span_bp)
    src = (
        select_zq_source(probe.contracts, con=con, require_all=False)
        if zq_source == "auto"
        else zq_source
    )
    have = available_zq_contracts(src, con=con)
    return select_instrument(
        meeting, calendar=calendar, available_contracts=have, min_span_bp=min_span_bp
    ), src


# --------------------------------------------------------------------------
# EFFR publication lag (G2)
# --------------------------------------------------------------------------


class EffrAsOf(NamedTuple):
    effr: float
    observation_date: dt.date
    published_at: pd.Timestamp
    staleness_sec: float


def effr_publication_schedule(*, con=None) -> pd.DataFrame:
    """EFFR observations with the instant each became public.

    EFFR for observation date d is published around 09:00 ET on the next
    publication date.  The publication calendar is taken from the print series
    itself: a date carries an EFFR print exactly when it is a New York Fed
    business day, so "the next date with a print" is the next publication day
    including bank holidays, with no hard-coded holiday table.  Bank holidays
    are published years ahead, so using them is calendar knowledge, not
    lookahead (G4).

    The final observation has no successor in the store; its publication
    instant is estimated as 09:00 ET on the next weekday and flagged in
    ``pub_estimated``.  Gaps longer than ``MAX_EFFR_GAP_DAYS`` raise, since that
    means a hole in the series rather than a holiday.
    """
    if con is None:
        with connect() as c:
            return effr_publication_schedule(con=c)

    df = read_effr(con)
    nxt = df["date"].shift(-1)
    estimated = nxt.isna()
    if estimated.any():
        last = df["date"].iloc[-1]
        d = last + dt.timedelta(days=1)
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        nxt = nxt.fillna(d)

    gap = pd.Series([(b - a).days for a, b in zip(df["date"], nxt)])
    bad = gap > MAX_EFFR_GAP_DAYS
    if bad.any():
        where = df.loc[bad.values, "date"].tolist()
        raise DataContractError(
            f"EFFR publication gap exceeds {MAX_EFFR_GAP_DAYS} days after {where}; "
            "the print series has a hole, so the publication lag cannot be modelled"
        )

    out = df.copy()
    out["published_at"] = _et_instant(list(nxt), EFFR_PUBLICATION_TIME_ET)
    out["pub_estimated"] = estimated.values
    out["available_at"] = out["published_at"]
    return out.sort_values("available_at").reset_index(drop=True)


def effr_asof(t, *, schedule: pd.DataFrame | None = None, con=None):
    """The EFFR publicly available at ``t`` (G2).

    ``t`` may be a single timestamp (returns :class:`EffrAsOf`) or any sequence
    of timestamps (returns a DataFrame indexed by ``t``).  Timestamps must be
    timezone-aware; a naive one is ambiguous and raises rather than being
    assumed UTC.

    Using EFFR[D] at any instant on day D is lookahead: that print does not
    exist until 09:00 ET on D+1.  This function is the only sanctioned way to
    read a rate level inside the harness.
    """
    if schedule is None:
        schedule = effr_publication_schedule(con=con)

    scalar = isinstance(t, (str, dt.datetime, pd.Timestamp))
    idx = pd.DatetimeIndex([pd.Timestamp(t)] if scalar else [pd.Timestamp(x) for x in t])
    if idx.tz is None:
        raise ValueError("effr_asof requires timezone-aware timestamps; a naive instant is ambiguous")
    idx = idx.tz_convert("UTC")

    left = pd.DataFrame({"t": idx}).sort_values("t")
    merged = pd.merge_asof(
        left,
        schedule[["available_at", "effr", "date", "pub_estimated"]].rename(
            columns={"date": "observation_date"}
        ),
        left_on="t",
        right_on="available_at",
        direction="backward",
    )
    merged["staleness_sec"] = (merged["t"] - merged["available_at"]).dt.total_seconds()
    merged = merged.set_index("t").reindex(idx)

    if scalar:
        r = merged.iloc[0]
        if pd.isna(r["effr"]):
            raise DataContractError(f"no EFFR print was public at {idx[0]}")
        return EffrAsOf(float(r["effr"]), r["observation_date"], r["available_at"], float(r["staleness_sec"]))
    return merged.rename(
        columns={
            "effr": "effr_asof",
            "observation_date": "effr_asof_date",
            "available_at": "effr_as_of",
            "staleness_sec": "effr_staleness_sec",
            "pub_estimated": "effr_pub_estimated",
        }
    )[["effr_asof", "effr_asof_date", "effr_as_of", "effr_staleness_sec", "effr_pub_estimated"]]


# --------------------------------------------------------------------------
# As-of joining (G1)
# --------------------------------------------------------------------------


def _et_instant(dates: Iterable[dt.date], at: dt.time) -> pd.DatetimeIndex:
    """Local-ET wall clock on each date, as UTC instants.

    ET is used rather than a fixed UTC offset because every convention here is
    a wall-clock one: Kalshi daily bars end 00:00 ET (04:00 UTC in EDT, 05:00
    in EST), the FOMC releases at 14:00 ET year round, and EFFR publishes at
    09:00 ET.  A fixed UTC hour would be an hour wrong for half the sample.
    """
    naive = pd.DatetimeIndex(pd.to_datetime(pd.Index(list(dates)))) + pd.Timedelta(
        hours=at.hour, minutes=at.minute
    )
    return naive.tz_localize(ET, nonexistent="shift_forward", ambiguous=True).tz_convert("UTC")


def _ns(x):
    """Coerce to tz-aware UTC at nanosecond resolution (DuckDB returns microseconds)."""
    out = pd.to_datetime(x, utc=True)
    return out.astype("datetime64[ns, UTC]")


def _row_max_ts(df: pd.DataFrame, cols: Sequence[str]) -> pd.Series:
    """Row-wise max over tz-aware datetime columns, NaT-safe."""
    if not cols:
        return pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
    stacked = np.stack(
        [pd.to_datetime(df[c], utc=True).to_numpy(dtype="datetime64[ns]").astype("int64") for c in cols]
    )
    inat = np.iinfo(np.int64).min
    best = stacked.max(axis=0)
    best[(stacked == inat).all(axis=0)] = inat
    return pd.Series(
        pd.DatetimeIndex(best.astype("datetime64[ns]")).tz_localize("UTC"), index=df.index
    )


def _asof_block(
    index: pd.DatetimeIndex,
    obs: pd.DataFrame,
    value_cols: Sequence[str],
    prefix: str,
    *,
    fresh_window_sec,
    ffill: bool,
    max_staleness_sec: float | None,
) -> pd.DataFrame:
    """Backward as-of join of one source onto the panel index.

    ``obs`` must carry ``available_at`` (tz-aware UTC) and be sorted so that,
    among rows sharing an instant, the preferred row is LAST -- ``merge_asof``
    keeps the last match.

    Freshness: without ``ffill`` a matched observation is used only if its age
    is within ``fresh_window_sec`` (a scalar, or a column name in ``obs``
    holding a per-row window, e.g. the candle's own interval).  Otherwise the
    value is dropped and ``{prefix}_as_of`` is NaT, so a stale quote can never
    masquerade as a live one.  With ``ffill`` the last observation is carried
    regardless of age, capped by ``max_staleness_sec`` if given.  Either way
    ``{prefix}_staleness_sec`` reports the age of the last observation.
    """
    if obs["available_at"].isna().any():
        raise DataContractError(f"{prefix}: observations carry a null available_at")
    if not obs["available_at"].is_monotonic_increasing:
        raise DataContractError(f"{prefix}: observations must be sorted by available_at")

    # DuckDB hands back datetime64[us]; merge_asof demands identical resolution.
    right = obs.copy()
    right["available_at"] = _ns(right["available_at"])
    left = pd.DataFrame(index=range(len(index)))
    left["t"] = _ns(index)
    keep = list(value_cols)
    if isinstance(fresh_window_sec, str) and fresh_window_sec not in keep:
        keep.append(fresh_window_sec)
    merged = pd.merge_asof(
        left, right[["available_at", *keep]], left_on="t", right_on="available_at", direction="backward"
    )
    age = (merged["t"] - merged["available_at"]).dt.total_seconds()

    if ffill:
        limit = float("inf") if max_staleness_sec is None else float(max_staleness_sec)
    elif isinstance(fresh_window_sec, str):
        limit = merged[fresh_window_sec].astype(float)
    else:
        limit = float(fresh_window_sec)

    drop = age.isna() | (age > limit)
    # Built on merge_asof's RangeIndex so that tz-aware columns keep their tz
    # (a .values round-trip would silently drop it), then re-indexed.
    out = pd.DataFrame(index=merged.index)
    for c in value_cols:
        out[f"{prefix}_{c}"] = merged[c].where(~drop)
    out[f"{prefix}_as_of"] = merged["available_at"].where(~drop)
    out[f"{prefix}_staleness_sec"] = age
    out.index = index
    return out


# --------------------------------------------------------------------------
# Universe declaration (G5)
# --------------------------------------------------------------------------


def meeting_universe(
    *,
    con=None,
    start: dt.date | str | None = None,
    end: dt.date | str | None = None,
    min_span_bp: float = DEFAULT_MIN_SPAN_BP,
) -> pd.DataFrame:
    """Every FOMC meeting in the window, with why each one is in or out (G5).

    No meeting is ever dropped silently: rows carry ``included`` and, when
    False, an ``exclusion_reason``.  Contamination is reported as a ``caveat``
    rather than an exclusion, because whether it disqualifies a meeting is the
    P&L engine's decision, not the panel's.
    """
    if con is None:
        with connect() as c:
            return meeting_universe(con=c, start=start, end=end, min_span_bp=min_span_bp)

    meetings = read_meetings(con)
    if start is not None:
        meetings = meetings[meetings["meeting_date"] >= pd.Timestamp(start).date()]
    if end is not None:
        meetings = meetings[meetings["meeting_date"] <= pd.Timestamp(end).date()]

    cal = fomc_calendar(con=con, months_ahead=(0, 1))
    kalshi_events = set(
        con.execute("select distinct event_id from kalshi_candles").df()["event_id"]
    )
    leg_counts = (
        con.execute(
            "select event_id, count(distinct outcome) n from kalshi_candles group by 1"
        )
        .df()
        .set_index("event_id")["n"]
        .to_dict()
    )
    poly = con.execute(
        "select meeting_date, event_id, hike50p_is_any_hike from poly_event_meeting where is_primary"
    ).df()
    poly["meeting_date"] = pd.to_datetime(poly["meeting_date"]).dt.date
    poly_primary = dict(zip(poly["meeting_date"], poly["event_id"]))
    poly_ladder = dict(zip(poly["meeting_date"], poly["hike50p_is_any_hike"]))
    poly_priced = set(con.execute("select distinct event_id from poly_prices").df()["event_id"])
    poly_legs = (
        con.execute(
            "select event_id, count(distinct outcome) n from poly_prices "
            "where outcome_label = 'Yes' group by 1"
        )
        .df()
        .set_index("event_id")["n"]
        .to_dict()
    )

    rows = []
    for m in meetings.itertuples(index=False):
        ev = m.kalshi_event
        has_candles = bool(ev) and ev in kalshi_events
        inst = err = None
        try:
            inst, src = _resolve_instrument(
                m.meeting_date, calendar=cal, con=con, zq_source="auto", min_span_bp=min_span_bp
            )
        except (DataContractError, KeyError, ValueError) as exc:
            src, err = None, str(exc)

        reasons = []
        if not ev:
            reasons.append("no Kalshi event mapped to this meeting")
        elif not has_candles:
            reasons.append(f"Kalshi event {ev} has no candles (purged: the API retains only events open at collection)")
        if err:
            reasons.append(f"no usable ZQ instrument: {err}")

        pev = poly_primary.get(m.meeting_date)
        rows.append(
            {
                "meeting_date": m.meeting_date,
                "effective_date": m.effective_date,
                "kalshi_event": ev,
                "has_kalshi_candles": has_candles,
                "n_kalshi_legs": int(leg_counts.get(ev, 0)),
                "poly_event": pev,
                "has_poly_prices": bool(pev) and pev in poly_priced,
                "poly_n_legs": int(poly_legs.get(pev, 0)),
                # Before 2026-06-17 Polymarket's "HIKE50P" leg is really
                # "25+ bps" and there is no HIKE25 leg: the two regimes are not
                # directly comparable, and neither is comparable to Kalshi's
                # 5-leg ladder without restating.
                "poly_any_hike_ladder": bool(poly_ladder.get(m.meeting_date, False)),
                "instrument": inst.as_tuple() if inst else None,
                "near_contract": inst.near_contract if inst else None,
                "far_contract": inst.far_contract if inst else None,
                "h_bp": inst.h_bp if inst else float("nan"),
                "zq_friction_cents": (
                    inst.zq_friction_cents_per_kalshi_contract if inst else float("nan")
                ),
                "requires_effr": inst.requires_effr if inst else None,
                "clean": inst.clean if inst else None,
                "zq_source": src,
                "included": has_candles and src is not None,
                # Polymarket reaches back to 2022 while Kalshi's API retains only
                # events still open at collection, so the two tracks have
                # different universes and each gets its own flag (G5).
                "included_poly": bool(pev) and pev in poly_priced and src is not None,
                "exclusion_reason": "; ".join(reasons) or None,
                "caveat": None if (inst is None or inst.clean) else inst.reason,
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Panel construction
# --------------------------------------------------------------------------


def _announcement_instant(meeting_date: dt.date) -> pd.Timestamp:
    return _et_instant([meeting_date], ANNOUNCEMENT_TIME_ET)[0]


def _fill_kalshi(out, index, candles, fresh, ffill, maxstale) -> None:
    """Write the per-leg Kalshi block into ``out`` (in place)."""
    for leg in CANONICAL_LEGS:
        obs = candles[candles["outcome"] == leg].rename(
            columns={"bid_close": "bid", "ask_close": "ask", "price_close": "last"}
        )
        # Finest interval LAST: merge_asof keeps the last row at an exact tie,
        # so a 1-min bar wins over the daily bar that ends at the same instant
        # and `kalshi_vol_*` keeps the same length as `kalshi_interval_*`.
        obs = obs.sort_values(["available_at", "interval"], ascending=[True, False], kind="mergesort")
        blk = _asof_block(
            index, obs, ["bid", "ask", "last", "vol", "oi", "interval", "book_degenerate"],
            f"kalshi_{leg}",
            fresh_window_sec="interval_sec" if fresh["kalshi"] is None else fresh["kalshi"],
            ffill=ffill, max_staleness_sec=maxstale.get("kalshi"),
        )
        for src_name, dst in (
            ("bid", "bid"), ("ask", "ask"), ("last", "last"), ("vol", "vol"),
            ("oi", "oi"), ("interval", "interval"), ("staleness_sec", "staleness_sec"),
        ):
            out[f"kalshi_{dst}_{leg}"] = blk[f"kalshi_{leg}_{src_name}"]
        out[f"kalshi_as_of_{leg}"] = blk[f"kalshi_{leg}_as_of"]
        out[f"_degen_{leg}"] = blk[f"kalshi_{leg}_book_degenerate"]


def _as_of_columns(df: pd.DataFrame) -> list[str]:
    """Every provenance column.  ``_as_of`` (with the underscore) is a source
    instant; ``effr_asof``/``effr_asof_date`` are VALUES and are excluded."""
    return [c for c in df.columns if "_as_of" in c and c != "as_of"]


def build_panel(
    meeting: dt.date | str,
    freq: str = "1d",
    *,
    con=None,
    instrument: Instrument | None = None,
    zq_source: str = "auto",
    zq_intraday_path: str | Path | None = None,
    start: dt.date | str | None = None,
    end: dt.date | str | None = None,
    include_post_decision: bool = False,
    ffill: bool = False,
    max_staleness_sec: Mapping[str, float] | None = None,
    fresh_window_sec: Mapping[str, float | None] | None = None,
    kalshi_intervals: Sequence[int] = (1, 60, 1440),
    poly_fidelities: Sequence[int] = (1, 60),
    poly_point_lag_periods: float = 1.0,
    require_poly: bool = False,
    require_kalshi: bool = True,
    hold_anchor_bp: pd.Series | float | None = None,
    min_span_bp: float = DEFAULT_MIN_SPAN_BP,
) -> pd.DataFrame:
    """Lookahead-safe event-time panel for one FOMC decision.

    Parameters
    ----------
    meeting : FOMC decision date present in the ``meetings`` table.
    freq : ``'1d'`` or ``'5min'``.

        ``'1d'`` puts one row on each ZQ trading date that BOTH legs of the
        instrument traded, stamped at ``ZQ_SETTLE_AVAILABLE_ET`` (16:00 ET).
        That instant, not midnight, is the point of the daily panel: pairing a
        CME settle with a Kalshi daily bar that closes at 00:00 ET hands the
        Kalshi leg eight extra hours of information (G1).  Here the Kalshi leg
        is read from whatever candle ended at or before 16:00 ET, which is
        normally the 60-minute bar ending exactly then.

        ``'5min'`` puts a row every five minutes across the window.  No
        intraday ZQ store exists, so this frequency needs either
        ``zq_intraday_path`` or ``ffill=True``; without one of them it raises
        rather than quietly pairing a fresh Kalshi quote with a settle from
        yesterday afternoon.
    start, end : window bounds as ET dates.  ``start`` defaults to the previous
        meeting's effective date, which is what makes the two-state model valid
        (every earlier decision is resolved and folded into R) and what makes
        the contamination analysis in ``select_instrument`` complete.  ``end``
        defaults to the meeting date.
    include_post_decision : keep rows at or after the 14:00 ET announcement.
        Off by default -- the 16:00 ET row on decision day is POST-announcement
        and must not enter a pre-decision study.  Turn it on to look at the
        announcement jump itself.
    ffill : opt in to carrying stale values forward.  Off by default: a value
        older than its source's own bar length is dropped, not carried.
    require_poly : raise instead of returning a panel without Polymarket legs.
    require_kalshi : off lets a meeting Kalshi's API purged still build a
        Polymarket-vs-ZQ panel (Polymarket reaches back to 2022, Kalshi does
        not).  The Kalshi columns are then present and empty, never absent.
    hold_anchor_bp : override the hold-state anchor with a drift model.  The
        default is the flat-R two-state anchor, whose measured residual is in
        KNOWN RESIDUAL above; ``q_cme`` inherits whichever anchor is used, and
        ``attrs['hold_anchor_source']`` records it.

    Returns
    -------
    DataFrame indexed by tz-aware UTC instant with, per canonical leg L in
    ``CANONICAL_LEGS``:

        ``kalshi_bid_L``, ``kalshi_ask_L``, ``kalshi_last_L``, ``kalshi_vol_L``,
        ``kalshi_oi_L``, ``kalshi_interval_L`` (length in minutes of the bar the
        values came from -- so ``kalshi_vol_L`` is unambiguous),
        ``kalshi_as_of_L``, ``kalshi_staleness_sec_L``, ``poly_yes_L``

    and, once per row:

        ``kalshi_sum_bid``, ``kalshi_sum_ask``, ``kalshi_sum_mid`` (across all
        five legs; ``sum_ask - 1`` is the cost of the complete set and is the
        cheapest coherence check on the book), ``kalshi_as_of``,
        ``kalshi_staleness_sec``, ``kalshi_book_degenerate``,
        ``kalshi_emove_bp_bid/ask/mid`` and ``poly_emove_bp`` (expected move in
        bp, ``sum_leg p_leg * LEG_BP[leg]`` -- the quantity directly comparable
        to the ZQ leg, and a diagnostic rather than an executable price),
        ``poly_sum_yes``,
        ``zq_near``, ``zq_far``, ``zq_near_vol``, ``zq_far_vol``,
        ``zq_near_as_of``, ``zq_far_as_of``, ``zq_span_bp``,
        ``zq_hold_anchor_bp``, ``zq_avg_dev_bp`` (deviation in month-average
        space), ``zq_emove_bp`` (= ``25 * q_cme``, the implied policy move,
        which is the one comparable to the Kalshi/Polymarket expected move),
        ``q_cme``, ``effr_asof``,
        ``effr_asof_date``, ``effr_as_of``, ``effr_staleness_sec``,
        ``poly_as_of``, ``poly_staleness_sec``, ``zq_near_no_trade`` /
        ``zq_far_no_trade`` (the exchange carried the settle with no trade),
        ``effr_pub_estimated``, ``is_pre_decision``,
        ``minutes_to_announcement``, ``as_of``, and the identity columns
        ``meeting_date``, ``instrument_kind``, ``zq_near_contract``,
        ``zq_far_contract``, ``zq_source``, ``h_bp``, which are repeated per
        row so that a stack of per-meeting panels survives ``concat`` (pandas
        drops ``attrs``).

    The basis the study is about is ``zq_emove_bp - kalshi_emove_bp_ask``, or
    per-leg ``q_cme - kalshi_ask_HIKE25`` in a two-state world.

    ``as_of`` is the newest source instant behind ANY value in that row; the
    build fails unless ``as_of <= index`` everywhere (G1).  Instrument details,
    assumptions and gate notes are attached to ``DataFrame.attrs``.
    """
    if con is None:
        with connect() as c:
            return build_panel(
                meeting, freq, con=c, instrument=instrument, zq_source=zq_source,
                zq_intraday_path=zq_intraday_path, start=start, end=end,
                include_post_decision=include_post_decision, ffill=ffill,
                max_staleness_sec=max_staleness_sec, fresh_window_sec=fresh_window_sec,
                kalshi_intervals=kalshi_intervals, poly_fidelities=poly_fidelities,
                poly_point_lag_periods=poly_point_lag_periods, require_poly=require_poly,
                require_kalshi=require_kalshi, hold_anchor_bp=hold_anchor_bp,
                min_span_bp=min_span_bp,
            )

    if freq not in ("1d", "5min"):
        raise ValueError(f"freq must be '1d' or '5min', got {freq!r}")

    meeting = pd.Timestamp(meeting).date()
    meetings = read_meetings(con)
    row = meetings[meetings["meeting_date"] == meeting]
    if row.empty:
        raise KeyError(
            f"{meeting} is not an FOMC meeting date. Known dates: "
            f"{meetings['meeting_date'].min()} .. {meetings['meeting_date'].max()}"
        )
    row = row.iloc[0]
    if require_kalshi and not row["kalshi_event"]:
        raise DataContractError(f"meeting {meeting} has no Kalshi event mapped; see meeting_universe()")

    cal = fomc_calendar(con=con, months_ahead=(0, 1))
    if instrument is None:
        instrument, src = _resolve_instrument(
            meeting, calendar=cal, con=con, zq_source=zq_source, min_span_bp=min_span_bp
        )
    elif zq_source == "auto":
        src = select_zq_source(instrument.contracts, con=con)
    else:
        src = zq_source

    # ---- window (pure calendar) -------------------------------------------
    prev = meetings[meetings["meeting_date"] < meeting]
    default_start = prev["effective_date"].iloc[-1] if len(prev) else row["effective_date"]
    win_start = pd.Timestamp(start).date() if start is not None else default_start
    win_end = pd.Timestamp(end).date() if end is not None else meeting
    if win_start > win_end:
        raise ValueError(f"empty window: start {win_start} > end {win_end}")
    announce = _announcement_instant(meeting)

    fresh = {**DEFAULT_FRESH_WINDOW_SEC, **(fresh_window_sec or {})}
    maxstale = dict(max_staleness_sec or {})
    if unknown := set(fresh) - set(DEFAULT_FRESH_WINDOW_SEC):
        raise ValueError(f"unknown fresh_window_sec block(s) {sorted(unknown)}")
    # None means "use the source bar's own length", which only Kalshi candles
    # and Polymarket points carry; a daily settle and an EFFR print have no
    # such field, so None there would be silently meaningless.
    for block in ("zq", "effr"):
        if fresh[block] is None:
            raise ValueError(
                f"fresh_window_sec[{block!r}] cannot be None: that source has no per-row bar "
                f"length to fall back on. Give a number of seconds "
                f"(default {DEFAULT_FRESH_WINDOW_SEC[block]})."
            )

    # ---- ZQ leg ------------------------------------------------------------
    # Read back past win_start so the as-of join can reach a settle for rows
    # that sit before the first settle inside the window (the 5-min panel opens
    # at 00:00 ET, sixteen hours before that day's settle).  Reaching further
    # BACK can never create lookahead; the index still respects win_start.
    zq = read_zq_daily(
        instrument.contracts, source=src, con=con,
        start=win_start - dt.timedelta(days=10), end=win_end,
    )
    zq["available_at"] = _et_instant(zq["date"], ZQ_SETTLE_AVAILABLE_ET)
    if zq_intraday_path is not None:
        intra = read_zq_intraday(zq_intraday_path, instrument.contracts)
        intra = intra[(intra["available_at"] >= zq["available_at"].min()) & (intra["available_at"] <= _et_instant([win_end], dt.time(23, 59))[0])]
        zq = pd.concat(
            [zq[["contract", "available_at", "settle", "volume", "no_trade", "source"]],
             intra[["contract", "available_at", "settle", "volume", "no_trade", "source"]]],
            ignore_index=True,
        )

    if freq == "5min" and zq_intraday_path is None and not ffill:
        raise ValueError(
            "freq='5min' has no intraday ZQ source: the only ZQ observations in the stores are "
            "daily settles, so every row except the 16:00 ET one would pair a live Kalshi quote "
            "with a settle hours old. Pass zq_intraday_path=... (IBKR does serve 1-/5-min TRADES "
            "bars for expired ZQ) or ffill=True to carry the settle forward knowingly -- a carried "
            "settle is not a tradable price."
        )

    # ---- index -------------------------------------------------------------
    if freq == "1d":
        per_leg = [set(g["date"]) for _, g in zq.groupby("contract")]
        dates = sorted(set.intersection(*per_leg)) if per_leg else []
        dates = [d for d in dates if win_start <= d <= win_end]
        if not dates:
            have = {c: (g["date"].min(), g["date"].max()) for c, g in zq.groupby("contract")} or None
            raise DataContractError(
                f"no ZQ trading date in {win_start}..{win_end} is common to "
                f"{list(instrument.contracts)} in source {src!r}. "
                f"Dates available per contract: {have}. "
                "A window entirely in the future is the usual cause: the default window runs "
                "from the previous meeting's effective date to the meeting date, and a meeting "
                "that has not happened yet has no settles for most of it."
            )
        index = _et_instant(dates, ZQ_SETTLE_AVAILABLE_ET)
    else:
        lo = _et_instant([win_start], dt.time(0, 0))[0]
        hi = announce if not include_post_decision else _et_instant([win_end], dt.time(23, 55))[0]
        index = pd.date_range(lo, hi, freq="5min", tz="UTC")

    if not include_post_decision:
        index = index[index < announce]
    if len(index) == 0:
        raise DataContractError(
            f"panel is empty for {meeting} over {win_start}..{win_end} "
            f"(include_post_decision={include_post_decision})"
        )
    index.name = "t"

    out = pd.DataFrame(index=index)

    # ---- Kalshi legs (G1: candle ts is the bar END, usable AT that instant) --
    kalshi_note = None
    try:
        candles = read_kalshi_candles(
            con,
            row["kalshi_event"],
            outcomes=CANONICAL_LEGS,
            intervals=kalshi_intervals,
            start_utc=index[0] - pd.Timedelta(days=7),
            end_utc=index[-1],
        )
    except DataContractError as exc:
        if require_kalshi:
            raise DataContractError(
                f"{exc} Pass require_kalshi=False to build a Polymarket-vs-ZQ panel instead; "
                "see meeting_universe()['included_poly']."
            ) from exc
        candles, kalshi_note = None, (
            f"no Kalshi candles for {row['kalshi_event']!r}; Kalshi legs are empty "
            "(require_kalshi=False). Polymarket reaches back to 2022, Kalshi does not."
        )

    if candles is None:
        for leg in CANONICAL_LEGS:
            for c in ("bid", "ask", "last", "vol", "oi", "interval", "staleness_sec"):
                out[f"kalshi_{c}_{leg}"] = float("nan")
            out[f"kalshi_as_of_{leg}"] = pd.NaT
            out[f"_degen_{leg}"] = False
    else:
        candles["interval_sec"] = candles["interval"].astype(float) * 60.0
        missing_legs = [l for l in CANONICAL_LEGS if l not in set(candles["outcome"])]
        if missing_legs:
            raise DataContractError(
                f"Kalshi event {row['kalshi_event']} is missing candle(s) for leg(s) {missing_legs}"
            )
        _fill_kalshi(out, index, candles, fresh, ffill, maxstale)

    bid_cols = [f"kalshi_bid_{l}" for l in CANONICAL_LEGS]
    ask_cols = [f"kalshi_ask_{l}" for l in CANONICAL_LEGS]
    out["kalshi_sum_bid"] = out[bid_cols].sum(axis=1, min_count=len(CANONICAL_LEGS))
    out["kalshi_sum_ask"] = out[ask_cols].sum(axis=1, min_count=len(CANONICAL_LEGS))
    out["kalshi_sum_mid"] = (out["kalshi_sum_bid"] + out["kalshi_sum_ask"]) / 2.0
    out["kalshi_as_of"] = _row_max_ts(out, [f"kalshi_as_of_{l}" for l in CANONICAL_LEGS])
    out["kalshi_staleness_sec"] = out[
        [f"kalshi_staleness_sec_{l}" for l in CANONICAL_LEGS]
    ].max(axis=1)
    out["kalshi_book_degenerate"] = out[[f"_degen_{l}" for l in CANONICAL_LEGS]].eq(True).any(axis=1)
    out = out.drop(columns=[f"_degen_{l}" for l in CANONICAL_LEGS])

    # Expected move in bp, the quantity directly comparable to the ZQ leg.
    # Diagnostics, not executable prices: the executable numbers are per-leg.
    n = len(CANONICAL_LEGS)
    for side in ("bid", "ask"):
        parts = pd.concat([out[f"kalshi_{side}_{l}"] * LEG_BP[l] for l in CANONICAL_LEGS], axis=1)
        out[f"kalshi_emove_bp_{side}"] = parts.sum(axis=1, min_count=n)
    out["kalshi_emove_bp_mid"] = (out["kalshi_emove_bp_bid"] + out["kalshi_emove_bp_ask"]) / 2.0

    # ---- ZQ columns ---------------------------------------------------------
    for side, contract in (("near", instrument.near_contract), ("far", instrument.far_contract)):
        if contract is None:
            out[f"zq_{side}"] = float("nan")
            out[f"zq_{side}_vol"] = float("nan")
            out[f"zq_{side}_no_trade"] = pd.NA
            out[f"zq_{side}_as_of"] = pd.NaT
            out[f"zq_{side}_staleness_sec"] = float("nan")
            continue
        obs = zq[zq["contract"] == contract].sort_values("available_at", kind="mergesort")
        blk = _asof_block(
            index, obs, ["settle", "volume", "no_trade"], f"zq_{side}",
            fresh_window_sec=fresh["zq"], ffill=ffill, max_staleness_sec=maxstale.get("zq"),
        )
        out[f"zq_{side}"] = blk[f"zq_{side}_settle"]
        out[f"zq_{side}_vol"] = blk[f"zq_{side}_volume"]
        out[f"zq_{side}_no_trade"] = blk[f"zq_{side}_no_trade"]
        out[f"zq_{side}_as_of"] = blk[f"zq_{side}_as_of"]
        out[f"zq_{side}_staleness_sec"] = blk[f"zq_{side}_staleness_sec"]

    if instrument.kind == "spread":
        # implied rate difference avg_far - avg_near, in bp; a hike raises it by h_bp
        out["zq_span_bp"] = (out["zq_near"] - out["zq_far"]) * 100.0
    else:
        price = out["zq_near"] if instrument.near_contract else out["zq_far"]
        out["zq_span_bp"] = (100.0 - price) * 100.0

    # ---- EFFR (G2) ----------------------------------------------------------
    eff = effr_asof(index, schedule=effr_publication_schedule(con=con))
    stale_effr = eff["effr_staleness_sec"] > (
        maxstale.get("effr", float("inf")) if ffill else fresh["effr"]
    )
    eff.loc[stale_effr, ["effr_asof", "effr_asof_date"]] = None
    eff.loc[stale_effr, "effr_as_of"] = pd.NaT
    out = out.join(eff)

    if hold_anchor_bp is not None:
        out["zq_hold_anchor_bp"] = hold_anchor_bp
        anchor_src = "caller-supplied"
    elif instrument.requires_effr:
        out["zq_hold_anchor_bp"] = out["effr_asof"] * 100.0
        anchor_src = "effr_asof (flat-R two-state model)"
    else:
        # R cancels out of a clean calendar spread, so the FLAT-R hold state is
        # exactly zero.  It is not zero in the data: see KNOWN RESIDUAL in the
        # module docstring.  Override with hold_anchor_bp= to carry a drift model.
        out["zq_hold_anchor_bp"] = 0.0
        anchor_src = "0.0 (R cancels; flat-R two-state model)"
    # The raw deviation is in MONTH-AVERAGE space: a decision only moves
    # w of the month, so (span - anchor) understates the policy move by a
    # factor h_bp/25.  Divide by h_bp to normalise to a 25bp digital (q_cme),
    # and multiply back by 25 for the expected POLICY move that is directly
    # comparable to kalshi_emove_bp_*.  Conflating the two is a real trap: for
    # the 2026-09 outright (w = 14/30) the month-average deviation is 10.75bp
    # while the implied policy move is 23.0bp.
    out["zq_avg_dev_bp"] = out["zq_span_bp"] - out["zq_hold_anchor_bp"]
    out["q_cme"] = out["zq_avg_dev_bp"] / instrument.h_bp
    out["zq_emove_bp"] = 25.0 * out["q_cme"]

    # ---- Polymarket ---------------------------------------------------------
    poly_event = con.execute(
        "select event_id, hike50p_is_any_hike from poly_event_meeting "
        "where is_primary and meeting_date = ?",
        [meeting],
    ).df()
    poly_note = None
    poly_weights, poly_missing = LEG_BP, []
    if poly_event.empty:
        poly_note = f"no primary Polymarket event mapped to {meeting}"
        if require_poly:
            raise DataContractError(poly_note)
        for leg in CANONICAL_LEGS:
            out[f"poly_yes_{leg}"] = float("nan")
        out["poly_as_of"] = pd.NaT
        out["poly_staleness_sec"] = float("nan")
    else:
        pev = poly_event["event_id"].iloc[0]
        any_hike = bool(poly_event["hike50p_is_any_hike"].iloc[0])
        poly_weights = POLY_ANY_HIKE_LEG_BP if any_hike else LEG_BP
        pp = read_poly_prices(
            con, pev, outcomes=CANONICAL_LEGS, fidelities=poly_fidelities,
            start_utc=index[0] - pd.Timedelta(days=7), end_utc=index[-1],
            point_lag_periods=poly_point_lag_periods,
        )
        pp["fidelity_sec"] = pp["fidelity"].astype(float) * 60.0
        as_ofs = []
        stales = []
        for leg in CANONICAL_LEGS:
            obs = pp[pp["outcome"] == leg].sort_values(
                ["available_at", "fidelity"], ascending=[True, False], kind="mergesort"
            )
            if obs.empty:
                out[f"poly_yes_{leg}"] = float("nan")
                if leg in poly_weights:
                    poly_missing.append(leg)
                continue
            blk = _asof_block(
                index, obs, ["p"], f"poly_{leg}",
                fresh_window_sec="fidelity_sec" if fresh["poly"] is None else fresh["poly"],
                ffill=ffill, max_staleness_sec=maxstale.get("poly"),
            )
            out[f"poly_yes_{leg}"] = blk[f"poly_{leg}_p"]
            as_ofs.append(blk[f"poly_{leg}_as_of"])
            stales.append(blk[f"poly_{leg}_staleness_sec"])
        if as_ofs:
            pa = pd.concat(as_ofs, axis=1)
            pa.columns = [f"c{i}" for i in range(pa.shape[1])]
            out["poly_as_of"] = _row_max_ts(pa, list(pa.columns))
            out["poly_staleness_sec"] = pd.concat(stales, axis=1).max(axis=1)
        else:
            out["poly_as_of"] = pd.NaT
            out["poly_staleness_sec"] = float("nan")
        poly_note = (
            f"Polymarket event {pev}; ladder={'any-hike (4 legs)' if any_hike else '5 legs'}; "
            "p is a single price, never an executable ask"
        )

    # Expected move over the legs this event actually lists, with the weights
    # that event's ladder implies (see POLY_ANY_HIKE_LEG_BP).  min_count is the
    # full leg set, so a partial ladder yields NaN rather than a number that
    # silently omits a state.
    scheme = list(poly_weights)
    out["poly_emove_bp"] = pd.concat(
        [out[f"poly_yes_{l}"] * poly_weights[l] for l in scheme], axis=1
    ).sum(axis=1, min_count=len(scheme))
    out["poly_sum_yes"] = out[[f"poly_yes_{l}" for l in scheme]].sum(
        axis=1, min_count=len(scheme)
    )
    if poly_missing:
        note = (
            f"Polymarket event is missing leg(s) {poly_missing} from its own ladder "
            f"{scheme}; poly_emove_bp and poly_sum_yes are NaN for this meeting."
        )
        if require_poly:
            raise DataContractError(note)
        poly_note = f"{poly_note}. {note}" if poly_note else note

    # ---- event-time markers -------------------------------------------------
    out["is_pre_decision"] = out.index < announce
    out["minutes_to_announcement"] = (announce - out.index).total_seconds() / 60.0

    # Identity as COLUMNS, not just attrs: pandas drops attrs on concat and
    # groupby, and a stack of per-meeting panels must still know which meeting,
    # which contracts and which span each row belongs to.
    out["meeting_date"] = meeting
    out["instrument_kind"] = instrument.kind
    out["zq_near_contract"] = instrument.near_contract
    out["zq_far_contract"] = instrument.far_contract
    out["zq_source"] = src
    out["h_bp"] = instrument.h_bp

    # ---- G1 proof -----------------------------------------------------------
    out["as_of"] = _row_max_ts(out, _as_of_columns(out))
    out.attrs.update(
        {
            "meeting_date": meeting,
            "effective_date": row["effective_date"],
            "announcement_utc": announce,
            "freq": freq,
            "window": (win_start, win_end),
            "instrument": instrument.as_tuple(),
            "instrument_detail": instrument,
            "instrument_reason": instrument.reason,
            "h_bp": instrument.h_bp,
            "hedge_ratio_kalshi_per_zq": instrument.hedge_ratio,
            "zq_friction_cents_per_kalshi_contract": instrument.zq_friction_cents_per_kalshi_contract,
            "anchor_sensitivity_cents_per_bp": instrument.anchor_sensitivity_cents_per_bp,
            "hold_anchor_source": anchor_src,
            "zq_source": src,
            "kalshi_event": row["kalshi_event"],
            "kalshi_note": kalshi_note,
            "poly_note": poly_note,
            "poly_leg_scheme": dict(poly_weights),
            "poly_missing_legs": list(poly_missing),
            "ffill": ffill,
            "fresh_window_sec": fresh,
            "max_staleness_sec": maxstale,
            "assumptions": {
                "ZQ_SETTLE_AVAILABLE_ET": str(ZQ_SETTLE_AVAILABLE_ET),
                "EFFR_PUBLICATION_TIME_ET": str(EFFR_PUBLICATION_TIME_ET),
                "ANNOUNCEMENT_TIME_ET": str(ANNOUNCEMENT_TIME_ET),
                "poly_point_lag_periods": poly_point_lag_periods,
                "zq_execution_cost": f"{instrument.legs_crossed} x {ZQ_TICK_BP}bp tick "
                                     f"(${ZQ_TICK_DOLLARS:.4f}); no ZQ spread exists in any store",
            },
            "gates": {
                "G1": "as-of joins on per-source availability instants; verified by verify_no_lookahead",
                "G2": ("not binding: R cancels out of the clean spread"
                       if not instrument.requires_effr
                       else "BINDING: outright hold anchor uses effr_asof with the 09:00 ET next-day lag"),
                "G4": "instrument chosen from the FOMC calendar only",
                "G5": "see meeting_universe()",
                "G7": "per-leg Kalshi vol/oi carried; ZQ cost is an assumption, not a measurement",
            },
        }
    )
    verify_no_lookahead(out, raise_on_fail=True)
    return out


def verify_no_lookahead(panel: pd.DataFrame, *, raise_on_fail: bool = True) -> dict:
    """Prove every value in ``panel`` was public at its row's timestamp (G1).

    Checks that the index is tz-aware, sorted and unique, and that no
    ``*_as_of`` column is ever later than its row's index.  Returns a report;
    raises :class:`DataContractError` on failure unless ``raise_on_fail`` is
    False.
    """
    if panel.index.tz is None:
        raise DataContractError("panel index must be timezone-aware")
    if not panel.index.is_monotonic_increasing:
        raise DataContractError("panel index is not sorted")
    if panel.index.has_duplicates:
        raise DataContractError("panel index has duplicate timestamps")

    cols = _as_of_columns(panel) + (["as_of"] if "as_of" in panel.columns else [])
    idx = _ns(panel.index)
    violations = {}
    for c in cols:
        s = _ns(panel[c])
        bad = s.notna() & (s > idx)
        if bad.any():
            violations[c] = {
                "n": int(bad.sum()),
                "worst_sec": float(
                    (
                        (s[bad.values].to_numpy() - idx[bad.values].to_numpy())
                        / np.timedelta64(1, "s")
                    ).max()
                ),
                "first_at": panel.index[bad.values][0],
            }
    report = {
        "rows": len(panel),
        "as_of_columns": cols,
        "violations": violations,
        "max_staleness_sec": {
            c: float(panel[c].max()) for c in panel.columns if c.endswith("_staleness_sec")
        },
        "ok": not violations,
    }
    if violations and raise_on_fail:
        raise DataContractError(f"LOOKAHEAD DETECTED: {violations}")
    return report


if __name__ == "__main__":  # smoke check against the live stores
    import sys

    pd.set_option("display.width", 220)
    uni = meeting_universe(start=dt.date(2025, 1, 1))
    print(
        uni[
            ["meeting_date", "instrument", "h_bp", "zq_friction_cents", "requires_effr",
             "clean", "zq_source", "included", "included_poly"]
        ].to_string(index=False)
    )
    print(f"\nKalshi track: {int(uni.included.sum())}/{len(uni)}   "
          f"Polymarket track: {int(uni.included_poly.sum())}/{len(uni)}")
    built = failed = 0
    for mtg in uni.loc[uni.included_poly, "meeting_date"]:
        try:
            p = build_panel(mtg, "1d", require_kalshi=False)
        except DataContractError as exc:
            failed += 1
            print(f"  {mtg}  SKIP  {str(exc).splitlines()[0][:100]}")
            continue
        built += 1
        print(
            f"  {mtg}  rows={len(p):3d}  {str(p.attrs['instrument']):28s}"
            f"  lookahead_ok={verify_no_lookahead(p)['ok']}"
            f"  basis_last={p['zq_emove_bp'].iloc[-1] - (p['kalshi_emove_bp_ask'].iloc[-1] if p['kalshi_emove_bp_ask'].notna().any() else p['poly_emove_bp'].iloc[-1]):+.2f}bp"
        )
    print(f"\nbuilt {built}, skipped {failed}")
    sys.exit(0 if built else 1)
