"""
costs.py -- every dollar of friction in the Kalshi/ZQ state-replication trade.

DESIGN RULE (G3, G7)
--------------------
No function in this module reads a number from module scope. Every constant
enters through an explicit ``CostAssumptions`` object, so a sensitivity sweep is
a parameter change and never a code edit. Constants that ARE verified are
carried as *defaults on the dataclass* with a provenance string attached, so the
manifest can print where each number came from.

Numbers and their provenance
----------------------------
verified   ZQ_POINT_MULTIPLIER = 4167.0     $ per 1.00 of ZQ price
verified   ZQ_BP_VALUE         = 41.67      $ per basis point per contract
derived    zq_tick_value_usd   = 10.4175    4167 * 0.0025 (the 0.25bp minimum
                                            tick). Quoted elsewhere as the
                                            rounded "$10.42"; 10.4175 is used
                                            here so tick math stays exactly
                                            consistent with the 4167 multiplier.
verified   kalshi_taker_coef   = 0.07       fee = ceil_to_cent(0.07*C*p*(1-p))
measured   hedge_error_mae_cents = 0.343    EFFR pass-through residual, 32 FOMC
measured   hedge_error_max_cents = 3.00     meetings 2022-2025, normalized to a
                                            $1 / 25bp digital.
UNKNOWN    maker fee coefficient            KXFEDDECISION is currently typed
                                            'quadratic_with_maker_fees', but the
                                            HISTORICAL regime is not established.
                                            There is therefore no default: a
                                            maker fee cannot be computed until
                                            the caller declares the regime, and
                                            asking for one otherwise raises
                                            AssumptionRequired. This is the G3
                                            anachronism guard.

Accounting convention for the hedge-error reserve
-------------------------------------------------
``hedge_error_reserve`` is NOT charged against realized P&L. Realized P&L is
computed from realized settlement prices / realized EFFR, which already contain
whatever hedge error actually occurred; charging the reserve on top would double
count it. The reserve exists to set the pre-registered entry threshold (G6) --
it is the one friction that cannot be charged deterministically at entry time
because it is a distribution rather than a number.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass, field
from decimal import ROUND_CEILING, Decimal
from typing import Optional, Tuple

__all__ = [
    "AssumptionRequired",
    "ceil_to_cent",
    "MakerRegime",
    "CostAssumptions",
    "kalshi_taker_fee",
    "kalshi_maker_fee",
    "kalshi_entry_fee",
    "zq_crossing_cost",
    "zq_cost_per_kalshi_contract",
    "hedge_error_reserve",
    "ZQ_POINT_MULTIPLIER",
    "ZQ_BP_VALUE",
]


class AssumptionRequired(RuntimeError):
    """Raised when a cost needs a number the caller has not declared.

    This is deliberately fatal. The alternative -- quietly substituting today's
    fee schedule for a 2025 trade date -- is exactly the G3 anachronism the
    harness exists to prevent.
    """


# Verified contract specification. These two are properties of the CBOT ZQ
# contract, not modelling choices, so they are module constants; everything a
# researcher might want to vary lives on CostAssumptions instead.
ZQ_POINT_MULTIPLIER = 4167.0  # $ per 1.00 of price
ZQ_BP_VALUE = 41.67  # $ per basis point


def ceil_to_cent(x: float) -> float:
    """Round up to the next whole cent, the way Kalshi rounds fees.

    Decimal, not float: ``math.ceil(0.07 * ... * 100) / 100`` bumps a value that
    lands exactly on a cent up by a further cent whenever binary representation
    error leaves it a hair above.
    """
    return float(Decimal(repr(float(x))).quantize(Decimal("0.01"), rounding=ROUND_CEILING))


@dataclass(frozen=True)
class MakerRegime:
    """Effective-dated Kalshi maker-fee schedule (G3).

    ``entries`` is a sequence of ``(effective_date, mode, coef)`` sorted by date.
    ``mode`` is one of:

      "none"      -- makers were not charged; coef ignored.
      "quadratic" -- fee = ceil_to_cent(coef * C * p * (1-p)).
      "unknown"   -- the regime on this date is not established. Asking for a
                     maker fee raises AssumptionRequired.

    The default regime is a single "unknown" entry covering all time. That is
    the honest starting point: the harness's primary strategy crosses the
    observed ask (taker), and nothing about the historical maker regime has been
    verified, so any maker-fee number must be declared by the caller.
    """

    entries: Tuple[Tuple[_dt.date, str, Optional[float]], ...] = (
        (_dt.date.min, "unknown", None),
    )
    note: str = "historical maker regime not established; declare it explicitly"

    def at(self, trade_date: _dt.date) -> Tuple[str, Optional[float]]:
        """Return ``(mode, coef)`` in force on ``trade_date``."""
        trade_date = _as_date(trade_date)
        mode, coef = "unknown", None
        found = False
        for eff, m, c in sorted(self.entries, key=lambda e: e[0]):
            if _as_date(eff) <= trade_date:
                mode, coef, found = m, c, True
            else:
                break
        if not found:
            raise AssumptionRequired(
                f"maker-fee regime has no entry covering {trade_date.isoformat()}; "
                f"earliest declared entry starts later"
            )
        return mode, coef

    @classmethod
    def declared(cls, *entries: Tuple[_dt.date, str, Optional[float]], note: str = "") -> "MakerRegime":
        return cls(entries=tuple(entries), note=note or "caller-declared regime")

    def manifest(self) -> dict:
        return {
            "note": self.note,
            "entries": [
                {"effective_date": _as_date(e).isoformat() if e != _dt.date.min else "-inf",
                 "mode": m,
                 "coef": c}
                for e, m, c in sorted(self.entries, key=lambda x: x[0])
            ],
        }


def _as_date(x) -> _dt.date:
    if isinstance(x, _dt.datetime):
        return x.date()
    if isinstance(x, _dt.date):
        return x
    if isinstance(x, str):
        return _dt.date.fromisoformat(x[:10])
    raise TypeError(f"cannot interpret {x!r} as a date")


@dataclass(frozen=True)
class CostAssumptions:
    """Every friction parameter, in one auditable object.

    Prices are in dollars per contract (a Kalshi contract pays $1). ZQ prices are
    in price points. "cents" fields are cents of a $1 digital.
    """

    # ---- Kalshi ---------------------------------------------------------
    kalshi_taker_coef: float = 0.07
    kalshi_execution_style: str = "taker"  # "taker" | "maker"
    maker_regime: MakerRegime = field(default_factory=MakerRegime)
    # Depth is not observable (G7): filling beyond top of book costs more than
    # the quoted ask. Default 0.0 means "assume the quoted ask fills the whole
    # clip"; sweep this upward for sensitivity.
    kalshi_slippage_cents_per_contract: float = 0.0
    # Kalshi does not charge a separate settlement fee on these markets today.
    # Kept as a parameter so the assumption is visible rather than implied.
    kalshi_settlement_fee_per_contract: float = 0.0

    # ---- ZQ -------------------------------------------------------------
    # 4167 * 0.0025 = 10.4175. IBKR BID_ASK bars for ZQ are degenerate
    # (open == close on every bar), so the historical ZQ spread is NOT
    # observable and the crossing cost is necessarily an assumption (G7).
    zq_tick_value_usd: float = 10.4175
    zq_ticks_crossed: float = 1.0
    # Legs crossed per round of execution. An outright is 1. A calendar spread
    # is 2 unless it trades as an exchange-recognized spread instrument, in
    # which case the caller sets spread_trades_as_unit=True.
    spread_trades_as_unit: bool = False
    # Carrying the ZQ leg to month-end settlement is not free (margin, funding).
    # Default 0.0 is an explicit assumption, not a claim that it is zero.
    zq_financing_usd_per_contract_per_day: float = 0.0

    # ---- Hedge error (threshold only; never charged to realized P&L) ----
    hedge_error_mode: str = "mae"  # "mae" | "max" | "none" | "custom"
    hedge_error_mae_cents: float = 0.343
    hedge_error_max_cents: float = 3.00
    hedge_error_custom_cents: Optional[float] = None
    hedge_error_multiple: float = 1.0

    # ---- Provenance -----------------------------------------------------
    label: str = "baseline"
    notes: str = ""

    def manifest(self) -> dict:
        d = asdict(self)
        d["maker_regime"] = self.maker_regime.manifest()
        d["_provenance"] = {
            "kalshi_taker_coef": "verified: Kalshi quadratic taker schedule",
            "zq_tick_value_usd": "derived: 4167 * 0.0025 (0.25bp min tick); quoted as $10.42",
            "hedge_error_mae_cents": "measured: 32 FOMC meetings 2022-2025, EFFR pass-through residual",
            "hedge_error_max_cents": "measured: same sample, worst case",
            "maker_regime": "NOT established historically; must be declared to be used",
            "zq_ticks_crossed": "assumption: ZQ historical spread is not observable (IBKR BID_ASK degenerate)",
            "kalshi_slippage_cents_per_contract": "assumption: Kalshi depth is not observable",
            "zq_financing_usd_per_contract_per_day": "assumption, set to zero by default",
        }
        return d


# ---------------------------------------------------------------------------
# Kalshi
# ---------------------------------------------------------------------------


def kalshi_taker_fee(price: float, n: float, assumptions: CostAssumptions) -> float:
    """Kalshi taker fee in dollars: ceil_to_cent(coef * C * p * (1-p))."""
    _check_price(price)
    if n < 0:
        raise ValueError(f"contract count must be non-negative, got {n}")
    return ceil_to_cent(assumptions.kalshi_taker_coef * n * price * (1.0 - price))


def kalshi_maker_fee(
    price: float,
    n: float,
    assumptions: CostAssumptions,
    trade_date: _dt.date,
) -> float:
    """Kalshi maker fee under the regime in force on ``trade_date`` (G3).

    Raises AssumptionRequired if the regime on that date is "unknown". Never
    falls back to today's schedule.
    """
    _check_price(price)
    mode, coef = assumptions.maker_regime.at(trade_date)
    if mode == "none":
        return 0.0
    if mode == "quadratic":
        if coef is None:
            raise AssumptionRequired(
                f"maker regime on {_as_date(trade_date)} is 'quadratic' but no coefficient "
                f"was declared"
            )
        return ceil_to_cent(coef * n * price * (1.0 - price))
    raise AssumptionRequired(
        f"Kalshi maker-fee regime on {_as_date(trade_date)} is not established "
        f"({assumptions.maker_regime.note}). Declare it with MakerRegime.declared(...) "
        f"before pricing a maker fill, or trade as a taker."
    )


def kalshi_entry_fee(
    price: float,
    n: float,
    assumptions: CostAssumptions,
    trade_date: _dt.date,
) -> float:
    """Dispatch to taker or maker according to the declared execution style."""
    if assumptions.kalshi_execution_style == "taker":
        return kalshi_taker_fee(price, n, assumptions)
    if assumptions.kalshi_execution_style == "maker":
        return kalshi_maker_fee(price, n, assumptions, trade_date)
    raise ValueError(f"unknown kalshi_execution_style {assumptions.kalshi_execution_style!r}")


def kalshi_slippage_cost(n: float, assumptions: CostAssumptions) -> float:
    """Assumed adverse fill beyond the quoted ask, in dollars (G7)."""
    return n * assumptions.kalshi_slippage_cents_per_contract / 100.0


def _check_price(price: float) -> None:
    if not (0.0 <= price <= 1.0):
        raise ValueError(f"Kalshi price must be in [0,1] dollars, got {price}")


# ---------------------------------------------------------------------------
# ZQ
# ---------------------------------------------------------------------------


def zq_crossing_cost(
    n_contracts: float,
    assumptions: CostAssumptions,
    ticks: Optional[float] = None,
    legs: int = 1,
) -> float:
    """Cost in dollars of crossing ``n_contracts`` ZQ, ``legs`` legs deep.

    ``ticks`` defaults to ``assumptions.zq_ticks_crossed``. The ZQ spread is NOT
    observable historically, so this is an assumption to be swept, never a
    measurement.
    """
    t = assumptions.zq_ticks_crossed if ticks is None else ticks
    effective_legs = 1 if (legs > 1 and assumptions.spread_trades_as_unit) else legs
    return abs(n_contracts) * assumptions.zq_tick_value_usd * t * effective_legs


def zq_cost_per_kalshi_contract(
    h: float,
    assumptions: CostAssumptions,
    ticks: Optional[float] = None,
    legs: int = 1,
) -> float:
    """ZQ crossing cost amortized over the Kalshi contracts it hedges.

    Because the hedge ratio is N = |4167*h| Kalshi contracts per instrument
    unit, this is independent of trade size:

        cost_per_contract = tick_value * ticks * legs / (4167 * |h|)

    With h = 0.25*w (a 25bp decision) and the 0.25bp tick, this collapses to
    exactly 1.00 cent per w per leg-crossing -- which is the structural reason
    hedging with a small-w contract is hopeless: at w = 3/31 the ZQ leg alone
    costs 10.3 cents per Kalshi contract to cross once.
    """
    if h == 0:
        raise ValueError("h == 0: the instrument has no exposure to this decision")
    n_per_unit = abs(ZQ_POINT_MULTIPLIER * h)
    return zq_crossing_cost(1.0, assumptions, ticks=ticks, legs=legs) / n_per_unit


def zq_financing_cost(
    n_contracts: float, days_held: float, assumptions: CostAssumptions
) -> float:
    """Cost of carrying the ZQ leg to settlement. Zero by default, by assumption."""
    return abs(n_contracts) * days_held * assumptions.zq_financing_usd_per_contract_per_day


# ---------------------------------------------------------------------------
# Hedge error
# ---------------------------------------------------------------------------


def hedge_error_reserve(assumptions: CostAssumptions) -> float:
    """Reserve in DOLLARS per Kalshi contract for EFFR pass-through error.

    Measured on 32 FOMC meetings 2022-2025: every one of the 17 policy changes
    passed through 1:1 at 1bp publication precision; the residual post-meeting
    EFFR error has MAE 0.085654bp / max 0.75bp, which normalizes to 0.343c MAE
    and 3.00c max on a $1 / 25bp digital.

    Modes: "mae" (central), "max" (worst observed), "none", "custom".
    The 2015-2021 regime is materially worse (one-day pass-through MAE ~0.93bp,
    and 10bp during the 2019-09 repo stress), so this reserve is only defensible
    on a 2022+ sample.
    """
    mode = assumptions.hedge_error_mode
    if mode == "none":
        cents = 0.0
    elif mode == "mae":
        cents = assumptions.hedge_error_mae_cents
    elif mode == "max":
        cents = assumptions.hedge_error_max_cents
    elif mode == "custom":
        if assumptions.hedge_error_custom_cents is None:
            raise AssumptionRequired(
                "hedge_error_mode='custom' requires hedge_error_custom_cents"
            )
        cents = assumptions.hedge_error_custom_cents
    else:
        raise ValueError(f"unknown hedge_error_mode {mode!r}")
    return cents * assumptions.hedge_error_multiple / 100.0
