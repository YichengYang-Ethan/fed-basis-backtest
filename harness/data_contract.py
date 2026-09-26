"""Raw-store access layer for the Kalshi/ZQ Fed-basis harness.

This module is the ONLY place that touches the raw stores.  It owns the file
paths, the schema expectations, and the conventions that are properties of the
stores themselves (timestamp labelling, units, expiry artifacts).  Everything
above it (``panel.py``) works with validated DataFrames and never writes SQL.

Gates implemented here
----------------------
G1 (timestamp alignment), partially: every reader returns an ``available_at``
    column -- a tz-aware UTC instant at which the row's content first became
    public.  The bar-labelling convention of each store is resolved HERE, once,
    so that no caller has to remember which store labels bars by start and
    which by end.

        * Kalshi candles      ``ts`` is the bar END  -> available_at = ts
        * IBKR / CME daily    labelled by trade date -> available_at = date @
                              ``ZQ_SETTLE_AVAILABLE_ET`` (see panel.py)
        * IBKR intraday       labelled by bar START  -> available_at =
                              start + bar duration
        * Polymarket points   irregular samples      -> available_at =
                              ts + one fidelity period (conservative)
        * FRED EFFR           observation date       -> available_at set by
                              ``panel.effr_publication_schedule`` (09:00 ET on
                              the next publication date)

G5 (universe declaration), partially: readers raise ``DataContractError``
    naming what is missing instead of returning a short frame, so a meeting can
    never be silently dropped for want of data.

No network calls.  DuckDB is opened read-only.

Empirical notes verified against the stores on 2026-09-17 (see repo notes):

* ``kalshi_candles.ts == epoch(bar_end_utc)`` for all 624,766 rows.
* Kalshi daily (interval=1440) bars end at 00:00 ET, i.e. 04:00 UTC in EDT and
  05:00 UTC in EST, so bar-end instants must be derived with a tz database and
  not a fixed UTC hour.
* Kalshi prices are dollars per contract in [0, 1], not cents.
* IBKR daily ``date`` is the ordinary trade date: cross-checked against the
  ``cme_zq`` (yfinance) settles for ZQU26/ZQV26/ZQX26/ZQZ26/ZQF27, a same-date
  join gives mean |diff| 2e-6 (float32 noise) while a one-day shift in either
  direction gives mean |diff| ~0.04 price points.  No date shift is required.
* IBKR emits ONE stale bar dated one calendar day after a contract's last trade
  date (volume=0, barCount=0, settle repeated).  ``read_zq_daily`` drops every
  bar dated after the delivery month's last calendar day.
* ``whatToShow="BID_ASK"`` is degenerate for ZQ (open == close on every bar),
  so no historical ZQ spread exists in any store.  ZQ execution cost is an
  assumption made in ``panel.py``, never a measurement.
"""

from __future__ import annotations

import datetime as dt
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import pandas as pd

# --------------------------------------------------------------------------
# Store locations.  Overridable by environment variable so tests can point at
# a fixture copy; never overridden silently in library code.
# --------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get("FOMC_DATA_ROOT", PROJECT_ROOT / "local_data")).expanduser().resolve()
DB_PATH = Path(os.environ.get("FED_PRICING_DB", DATA_ROOT / "fed.duckdb")).expanduser()
IBKR_ZQ_PARQUET = Path(
    os.environ.get(
        "FED_PRICING_IBKR_ZQ",
        DATA_ROOT / "raw/cme/ibkr/zq_contracts_ibkr.parquet",
    )
).expanduser()

UTC = dt.timezone.utc

#: CME month codes, January first.
MONTH_CODES = "FGHJKMNQUVXZ"

#: The five canonical FOMC decision legs, as both venues label them.
CANONICAL_LEGS: tuple[str, ...] = ("CUT50P", "CUT25", "HOLD", "HIKE25", "HIKE50P")

#: ZQ contract economics.  $41.67 per basis point per contract; a full price
#: point is 100bp.  Minimum tick is a quarter basis point.
ZQ_DOLLARS_PER_BP = 41.67
ZQ_DOLLARS_PER_POINT = 4167.0
ZQ_TICK_BP = 0.25
ZQ_TICK_DOLLARS = ZQ_TICK_BP * ZQ_DOLLARS_PER_BP  # $10.4175

#: Which delivery months each ZQ source can serve, as measured on 2026-09-17.
#: Used only for error messages; actual availability is always re-checked.
ZQ_SOURCE_HINTS = {
    "ibkr": "delivery months 2025-09 .. 2027-02 (IBKR retains expired ZQ back to LTD 2025-09-30)",
    "cme_zq": "delivery months 2026-09 .. 2027-12 (yfinance; expired contracts are not retained)",
}


class StoreMissingError(FileNotFoundError):
    """A required raw store is not on disk."""


class DataContractError(ValueError):
    """A store is present but does not satisfy the schema/content contract."""


# --------------------------------------------------------------------------
# Connection and existence checks
# --------------------------------------------------------------------------


def require_stores(*, need_ibkr: bool = True) -> None:
    """Raise :class:`StoreMissingError` naming any missing store."""
    missing = []
    if not DB_PATH.exists():
        missing.append(f"DuckDB store not found: {DB_PATH} (set FED_PRICING_DB to override)")
    if need_ibkr and not IBKR_ZQ_PARQUET.exists():
        missing.append(
            f"IBKR ZQ parquet not found: {IBKR_ZQ_PARQUET} (set FED_PRICING_IBKR_ZQ to override)"
        )
    if missing:
        raise StoreMissingError("; ".join(missing))


@contextmanager
def connect() -> Iterator["object"]:
    """Open ``fed.duckdb`` read-only.  Import of duckdb is deferred so that the
    missing-file error is raised before an ImportError can mask it."""
    require_stores(need_ibkr=False)
    import duckdb

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        yield con
    finally:
        con.close()


def _require_columns(df: pd.DataFrame, cols: Sequence[str], what: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise DataContractError(f"{what}: missing expected column(s) {missing}; present={list(df.columns)}")


def _utc(series: pd.Series) -> pd.Series:
    """Attach UTC to a naive timestamp column (DuckDB TIMESTAMP is naive UTC here)."""
    s = pd.to_datetime(series)
    return s.dt.tz_localize("UTC") if s.dt.tz is None else s.dt.tz_convert("UTC")


# --------------------------------------------------------------------------
# Contract symbols
# --------------------------------------------------------------------------


def zq_symbol(year: int, month: int) -> str:
    """CME ZQ symbol for a delivery month, e.g. (2026, 10) -> ``ZQV26``."""
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range: {month}")
    return f"ZQ{MONTH_CODES[month - 1]}{year % 100:02d}"


def delivery_month_str(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


# --------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------


def read_meetings(con) -> pd.DataFrame:
    """FOMC meetings with the stored ZQ month arithmetic.

    Returns columns ``meeting_date, meeting_month, effective_date, month_end,
    days_in_month, days_post, delta, realized_change_bp, realized_outcome,
    kalshi_event, zq_contract_symbol``.

    ``effective_date`` is the first calendar day on which the NEW rate is the
    one EFFR reports, which is not always decision day + 1: for the 2025-06-18
    meeting it is 2025-06-20 because 2025-06-19 (Juneteenth) has no EFFR print
    and ZQ settlement carries the previous business day's rate forward.  This
    reader takes it as given and ``panel.fomc_calendar`` re-derives every weight
    from it.
    """
    df = con.execute(
        """
        select meeting_date, meeting_month, effective_date, month_end, days_in_month,
               days_post, delta, realized_change_bp, realized_outcome,
               kalshi_event, zq_contract_symbol
        from meetings
        order by meeting_date
        """
    ).df()
    _require_columns(df, ["meeting_date", "effective_date", "month_end", "days_in_month"], "meetings")
    if df.empty:
        raise DataContractError("meetings table is empty")
    for c in ("meeting_date", "effective_date", "month_end"):
        df[c] = pd.to_datetime(df[c]).dt.date
    if df["meeting_date"].duplicated().any():
        dupes = df.loc[df["meeting_date"].duplicated(keep=False), "meeting_date"].tolist()
        raise DataContractError(f"meetings has duplicate meeting_date rows: {dupes}")
    return df


def read_kalshi_candles(
    con,
    event_id: str,
    *,
    outcomes: Iterable[str] = CANONICAL_LEGS,
    intervals: Iterable[int] = (1, 60, 1440),
    start_utc: dt.datetime | None = None,
    end_utc: dt.datetime | None = None,
) -> pd.DataFrame:
    """Kalshi candles for one decision event, one row per (leg, interval, bar).

    ``available_at`` equals the bar END instant, because ``kalshi_candles.ts``
    is the bar end (verified: ``ts == epoch(bar_end_utc)`` on all rows).  A bar
    with end T is therefore usable AT T and not before (G1).

    Prices are dollars in [0, 1].  ``book_degenerate`` marks the empty-book
    bars (bid 0.00 / ask 1.00) that appear on resolution day once the book is
    pulled -- 62 of the 44,271 daily bars in the store.
    """
    outcomes = tuple(outcomes)
    intervals = tuple(int(i) for i in intervals)
    where = ["event_id = ?", f"outcome in ({','.join('?' * len(outcomes))})",
             f"interval in ({','.join('?' * len(intervals))})"]
    params: list[object] = [event_id, *outcomes, *intervals]
    if start_utc is not None:
        where.append("bar_end_utc >= ?")
        params.append(pd.Timestamp(start_utc).tz_convert("UTC").tz_localize(None).to_pydatetime())
    if end_utc is not None:
        where.append("bar_end_utc <= ?")
        params.append(pd.Timestamp(end_utc).tz_convert("UTC").tz_localize(None).to_pydatetime())

    df = con.execute(
        f"""
        select event_id, market_id, outcome, interval, bar_end_utc, trade_date_et,
               bid_close, ask_close, price_close, vol, oi, is_partial
        from kalshi_candles
        where {' and '.join(where)}
        order by bar_end_utc, interval
        """,
        params,
    ).df()
    if df.empty:
        raise DataContractError(
            f"no kalshi_candles rows for event_id={event_id!r} "
            f"(outcomes={outcomes}, intervals={intervals}, window={start_utc}..{end_utc}). "
            "Kalshi's API retains only events that had not yet closed at collection time."
        )
    df["available_at"] = _utc(df["bar_end_utc"])
    bad = df[["bid_close", "ask_close"]].dropna()
    if not bad.empty and (bad.min().min() < 0 or bad.max().max() > 1):
        raise DataContractError("kalshi_candles bid/ask outside [0,1]; unit convention changed")
    df["book_degenerate"] = (df["bid_close"] == 0.0) & (df["ask_close"] == 1.0)
    df["crossed"] = df["ask_close"] < df["bid_close"]
    return df.drop(columns=["bar_end_utc"])


def read_poly_prices(
    con,
    event_id: str,
    *,
    outcomes: Iterable[str] = CANONICAL_LEGS,
    fidelities: Iterable[int] = (1, 60),
    start_utc: dt.datetime | None = None,
    end_utc: dt.datetime | None = None,
    point_lag_periods: float = 1.0,
) -> pd.DataFrame:
    """Polymarket YES prices for one decision event.

    Polymarket samples are irregular (they do not land on bucket boundaries),
    so it is not established whether ``ts`` labels the start or the end of the
    ``fidelity``-minute bucket it came from.  This reader takes the
    conservative reading -- a point is treated as public only
    ``point_lag_periods`` fidelity periods after its stamp -- so that any
    residual ambiguity costs information rather than creating lookahead (G1).
    Set ``point_lag_periods=0.0`` to treat stamps as observation instants.

    There is no order book in this store: ``p`` is a single price, so it can
    never stand in for an executable ask.
    """
    outcomes = tuple(outcomes)
    fidelities = tuple(int(f) for f in fidelities)
    where = ["event_id = ?", f"outcome in ({','.join('?' * len(outcomes))})",
             f"fidelity in ({','.join('?' * len(fidelities))})", "outcome_label = 'Yes'"]
    params: list[object] = [event_id, *outcomes, *fidelities]
    if start_utc is not None:
        where.append("ts >= ?")
        params.append(int(pd.Timestamp(start_utc).timestamp()))
    if end_utc is not None:
        where.append("ts <= ?")
        params.append(int(pd.Timestamp(end_utc).timestamp()))

    df = con.execute(
        f"""
        select event_id, outcome, token_id, ts, p, fidelity
        from poly_prices
        where {' and '.join(where)}
        order by ts
        """,
        params,
    ).df()
    if df.empty:
        labels = con.execute(
            "select distinct outcome_label from poly_prices where event_id = ?", [event_id]
        ).df()
        raise DataContractError(
            f"no poly_prices YES rows for event_id={event_id!r} "
            f"(outcomes={outcomes}, fidelities={fidelities}); "
            f"outcome_labels present for this event: {labels['outcome_label'].tolist()}"
        )
    if df["p"].min() < -0.05 or df["p"].max() > 1.05:
        raise DataContractError(
            f"poly_prices p outside [-0.05, 1.05] (min {df['p'].min()}, max {df['p'].max()})"
        )
    stamp = pd.to_datetime(df["ts"], unit="s", utc=True)
    df["available_at"] = stamp + pd.to_timedelta(df["fidelity"] * 60.0 * point_lag_periods, unit="s")
    df["observed_at"] = stamp
    return df


def read_effr(con) -> pd.DataFrame:
    """FRED EFFR observations, one row per publication date, ``effr`` in percent.

    Only non-null rows are returned: the set of dates carrying an EFFR print is
    exactly the set of New York Fed business days, which is what the
    publication calendar in ``panel.py`` is built from.  Weekends and bank
    holidays carry no print; ZQ settlement carries the previous print forward.
    """
    df = con.execute(
        "select date, effr from fred_rates where effr is not null order by date"
    ).df()
    if df.empty:
        raise DataContractError("fred_rates has no non-null effr rows")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def read_zq_daily(
    contracts: Sequence[str],
    *,
    source: str,
    con=None,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> pd.DataFrame:
    """Daily ZQ settles for the named contracts from ONE source.

    ``source`` is ``'ibkr'`` (the parquet; real exchange settles, expired
    contracts retained back to LTD 2025-09-30) or ``'cme_zq'`` (the yfinance
    table in the DuckDB; forward contracts only, long history per contract).
    Sources are never mixed inside one panel -- see ``panel.select_zq_source``.

    Returns ``contract, date, settle, volume, source``, sorted, with:

    * bars dated after the delivery month's last calendar day dropped.  IBKR
      emits exactly one such bar per expired contract (volume 0, barCount 0,
      settle repeated from LTD); keeping it would put a phantom observation on
      the day after the contract stopped trading.
    * ``no_trade`` True where volume is 0, i.e. the exchange carried the settle
      forward without a trade.  About 35% of IBKR bars on deferred contracts.
    """
    contracts = list(dict.fromkeys(contracts))
    if not contracts:
        raise ValueError("no contracts requested")

    if source == "ibkr":
        require_stores(need_ibkr=True)
        raw = pd.read_parquet(IBKR_ZQ_PARQUET)
        _require_columns(raw, ["date", "settle", "volume", "contract", "delivery_month"], "IBKR ZQ parquet")
        df = raw[raw["contract"].isin(contracts)].copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df[["contract", "delivery_month", "date", "settle", "volume"]]
    elif source == "cme_zq":
        if con is None:
            raise ValueError("source='cme_zq' requires an open DuckDB connection")
        df = con.execute(
            f"""
            select contract, delivery_month, date,
                   coalesce(settle, close) as settle, volume
            from cme_zq
            where contract in ({','.join('?' * len(contracts))})
            """,
            contracts,
        ).df()
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"]).dt.date
    else:
        raise ValueError(f"unknown ZQ source {source!r}; expected 'ibkr' or 'cme_zq'")

    found = set(df["contract"].unique()) if not df.empty else set()
    missing = [c for c in contracts if c not in found]
    if missing:
        raise DataContractError(
            f"ZQ source {source!r} has no rows for contract(s) {missing}. "
            f"Coverage: {ZQ_SOURCE_HINTS.get(source, 'unknown')}."
        )

    # Drop post-expiry artifacts using the delivery month's last calendar day.
    dm = pd.to_datetime(df["delivery_month"] + "-01")
    last_day = (dm + pd.offsets.MonthEnd(0)).dt.date
    df = df[df["date"] <= last_day].copy()

    if start is not None:
        df = df[df["date"] >= start]
    if end is not None:
        df = df[df["date"] <= end]
    if df["settle"].isna().any():
        n = int(df["settle"].isna().sum())
        raise DataContractError(f"ZQ source {source!r} returned {n} rows with a null settle")

    df["no_trade"] = df["volume"].fillna(0) == 0
    df["source"] = source
    return df.sort_values(["contract", "date"]).reset_index(drop=True)


#: Schema an optional ZQ intraday file must satisfy.  No such file exists in
#: the stores as of 2026-09-17; IBKR does serve 1-min and 5-min TRADES bars for
#: expired ZQ contracts, so ``panel.build_panel`` accepts one if it is pulled.
ZQ_INTRADAY_COLUMNS = ("contract", "bar_start_utc", "settle", "volume", "bar_seconds")


def read_zq_intraday(path: str | Path, contracts: Sequence[str]) -> pd.DataFrame:
    """Optional intraday ZQ bars, labelled by bar START (the IBKR convention).

    ``available_at = bar_start + bar_seconds``: a bar labelled T covers
    [T, T+dt) and cannot be known until T+dt (G1).  The one-bar lag is applied
    here so callers cannot forget it.
    """
    path = Path(path)
    if not path.exists():
        raise StoreMissingError(f"ZQ intraday file not found: {path}")
    df = pd.read_parquet(path)
    _require_columns(df, ZQ_INTRADAY_COLUMNS, f"ZQ intraday file {path}")
    df = df[df["contract"].isin(list(contracts))].copy()
    if df.empty:
        raise DataContractError(f"ZQ intraday file {path} has no rows for contracts {list(contracts)}")
    start = pd.to_datetime(df["bar_start_utc"], utc=True)
    df["available_at"] = start + pd.to_timedelta(df["bar_seconds"], unit="s")
    df["no_trade"] = df["volume"].fillna(0) == 0
    df["source"] = f"intraday:{path.name}"
    return df.sort_values(["contract", "available_at"]).reset_index(drop=True)
