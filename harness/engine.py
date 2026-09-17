"""
engine.py -- backtest engine for the Kalshi/ZQ state-replication trade.

WHAT THIS PRICES
----------------
A CBOT 30-Day Fed Funds future (ZQ) for delivery month M settles at
``100 - mean(daily EFFR over the calendar days of M)``, $4,167 per price point
($41.67/bp). For a single FOMC decision, the future is a two-state instrument:
let ``w_M`` be the fraction of month M's days that sit at the post-decision rate,
and let ``B_M`` be the average EFFR over M in the counterfactual where the
decision under study is a hold. Then for a decision of size ``D`` (signed, in
percentage points: +0.25 hike, -0.25 cut):

    F_M(D) = 100 - B_M - D * w_M

An instrument is any linear combination of contracts, ``sum_j c_j * F_{M_j}``
(an outright has one leg with c = +1; the Oct/Nov calendar spread has
c = (+1, -1)). Writing ``W = sum_j c_j * w_{M_j}``:

    V_base   = sum_j c_j * (100 - B_{M_j})          "baseline"
    V_target = V_base - D * W
    h        = V_base - V_target = D * W            "span", signed
    q        = (V_base - P) / h                      normalized state price
    N        = |4167 * h|                            Kalshi contracts per unit
    n        = sign(h)                               units of instrument to BUY

Buy N target-outcome YES at ask ``a`` and take ``n`` units of the instrument.
Gross P&L is then ``N * (q - a)`` in BOTH states -- the replication is exact and
direction-free. (Verify: in the target state, P&L is
``N*(1-a) + n*4167*(V_target - P)``; substituting ``P = V_base - q*h`` and
``n*4167*h = N`` gives ``N*(q-a)``. The base state gives the same.)

Two structural consequences the engine leans on:

1.  ZQ crossing cost per Kalshi contract is ``tick/(4167*|h|)``, which for a
    25bp decision is exactly ``1.00 cent / w`` per leg-crossing. Hedging a
    late-month decision with the MEETING-month contract (w small) is hopeless.
2.  For a balanced spread (``sum_j c_j = 0``) with no other meeting inside the
    legs, ``V_base = 0`` identically: the unknown EFFR level cancels. That
    removes gate G2 (EFFR publication lag) entirely, which is why spreads are
    preferred and why the engine records ``baseline_source`` on every row.

THE CONTRACT WITH panel.py
--------------------------
This module owns no data access. It consumes four callables:

    panel.fomc_calendar()            -> iterable of meeting records
    panel.select_instrument(meeting) -> InstrumentSpec-shaped object (G4)
    panel.build_panel(meeting, freq) -> (rows, meta) or rows-with-.attrs
    panel.effr_asof(t)               -> publication-lagged EFFR as of time t (G2)

and one OPTIONAL callable used purely for P&L resolution, never for decisions:

    panel.resolution(meeting)        -> Resolution-shaped object

Required panel row fields (aliases in ``_ROW_ALIASES``):

    ts                  cross-section time; tz-aware datetime or epoch seconds
    kalshi_ask          executable ask for the TARGET outcome, dollars in [0,1]
    kalshi_volume       contracts traded in the bar (sizing cap, G7)
    instr_price         tradable price of the selected instrument, price points
    kalshi_available_at provenance instant behind kalshi_ask
    zq_available_at     provenance instant behind instr_price

``data_contract.py`` names provenance instants ``available_at``; the engine
accepts that spelling and a ``<field>_src_ts`` spelling equally. Every column
ending in ``_src_ts``, ``_asof`` or ``available_at`` is compared against the
row's ``ts``, and panel meta must declare ``bar_lag_applied`` (G1).

Optional row fields:

    baseline            V_base in price points, if the panel computes it
    effr                EFFR used for an outright baseline (must be lagged)
    days_to_settlement  calendar days to ZQ cash settlement, for financing
    kalshi_state_prices {outcome: price} for the two-state leakage diagnostic
    book_degenerate     book-quality flags from read_kalshi_candles; if any one
    crossed             of them is true the row is not an executable quote and
    is_partial          is marked non-tradable

WHAT THIS ENGINE WILL NOT DO
----------------------------
It will not invent a number. Where resolution data is absent, the exit is
reported as unavailable with a reason rather than estimated; where the panel
cannot prove timestamp provenance, the run aborts rather than proceeding on
trust.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:  # package import
    from . import costs as _costs
except ImportError:  # script import
    import costs as _costs  # type: ignore

CostAssumptions = _costs.CostAssumptions
MakerRegime = _costs.MakerRegime
AssumptionRequired = _costs.AssumptionRequired
ZQ_POINT_MULTIPLIER = _costs.ZQ_POINT_MULTIPLIER
ZQ_BP_VALUE = _costs.ZQ_BP_VALUE

__all__ = [
    "LookaheadViolation",
    "PanelSchemaError",
    "SelectionPurityError",
    "PanelUnavailable",
    "InstrumentSpec",
    "Resolution",
    "RunAssumptions",
    "Threshold",
    "q_cme",
    "hedge_ratio",
    "state_values",
    "announcement_jump",
    "derive_preregistered_threshold",
    "evaluate_entry",
    "size_position",
    "run_meeting",
    "apply_threshold",
    "threshold_curve",
    "walk_forward",
    "run",
    "self_check",
]


# ---------------------------------------------------------------------------
# Failures. All of these are fatal by design; none of them degrade silently.
# ---------------------------------------------------------------------------


class LookaheadViolation(RuntimeError):
    """A panel row used information timestamped after the row itself (G1)."""


class PanelSchemaError(RuntimeError):
    """The panel did not supply a field the engine requires."""


class SelectionPurityError(RuntimeError):
    """Instrument selection depended on something other than the FOMC calendar (G4)."""


class PanelUnavailable(RuntimeError):
    """panel.py could not be imported."""


class ExclusionReason(RuntimeError):
    """A meeting cannot be traded. Recorded in the universe table, not fatal (G5)."""


# ---------------------------------------------------------------------------
# Declared shapes
# ---------------------------------------------------------------------------

# G4: instrument selection may depend on the FOMC calendar (published a year
# ahead) and on the civil calendar. Nothing else. Any other key in
# ``selection_inputs`` means the choice of contract could have been informed by
# what actually happened, and the engine refuses.
ALLOWED_SELECTION_INPUT_KEYS = frozenset(
    {
        "meeting_date",
        "effective_date",
        "delivery_month",
        "delivery_months",
        "days_in_month",
        "days_at_new_rate",
        "w",
        "w_by_month",
        "other_meeting_dates",
        "legs",
        "coefs",
        "calendar_version",
        "calendar_published_date",
        "rule",
    }
)


@dataclass(frozen=True)
class InstrumentSpec:
    """What ``panel.select_instrument(meeting)`` must describe.

    ``legs`` is ``((delivery_month, coef), ...)`` with ``delivery_month`` as
    "YYYY-MM". ``w_by_month`` gives each leg's fraction-of-month at the
    post-decision rate for THIS decision.
    """

    kind: str  # "outright" | "spread"
    legs: Tuple[Tuple[str, float], ...]
    w_by_month: Dict[str, float]
    selection_rule: str
    selection_inputs: Dict[str, Any]
    baseline: Optional[float] = None
    baseline_source: str = "unset"  # "spread_identity" | "effr_asof" | "declared"
    symbol: str = ""
    notes: str = ""

    @property
    def W(self) -> float:
        return sum(coef * self.w_by_month[m] for m, coef in self.legs)

    @property
    def n_legs(self) -> int:
        return len(self.legs)

    @property
    def is_balanced(self) -> bool:
        """A balanced spread's baseline is R-free (G2)."""
        return abs(sum(coef for _, coef in self.legs)) < 1e-12

    def manifest(self) -> dict:
        return {
            "kind": self.kind,
            "symbol": self.symbol,
            "legs": [{"delivery_month": m, "coef": c} for m, c in self.legs],
            "w_by_month": dict(self.w_by_month),
            "W": self.W,
            "is_balanced": self.is_balanced,
            "baseline": self.baseline,
            "baseline_source": self.baseline_source,
            "selection_rule": self.selection_rule,
            "selection_inputs": dict(self.selection_inputs),
            "notes": self.notes,
        }


@dataclass(frozen=True)
class Resolution:
    """Post-event truth. Used ONLY for P&L accounting, never for entry."""

    outcome: str  # e.g. "HOLD", "HIKE25", "CUT25"
    realized_delta: Optional[float] = None  # signed pct points
    exit_ts: Optional[int] = None
    exit_price: Optional[float] = None  # instrument price just after announcement
    settle_price: Optional[float] = None  # instrument value at ZQ cash settlement
    settle_date: Optional[str] = None
    p_pre: Optional[float] = None  # pre-announcement target probability, for the jump check
    sources: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunAssumptions:
    """Engine-level parameters. Cost parameters live in ``costs``."""

    costs: CostAssumptions = field(default_factory=CostAssumptions)

    # Which two states are being replicated.
    target_outcome: str = "HIKE25"
    base_outcome: str = "HOLD"
    target_delta: float = 0.25  # signed pct points; -0.25 for CUT25

    # G7 execution / depth
    volume_participation: float = 0.10  # fraction of the bar's observed volume
    max_instrument_units: int = 10  # per entry
    max_total_units: int = 10  # per meeting, across entries

    # Entry policy
    entry_policy: str = "first"  # "first" | "all"
    decision_exit: str = "announcement"  # cost basis for the entry test

    # Gates
    require_provenance: bool = True
    two_state_leakage_tol: float = 0.02  # flag rows where other states carry >2c

    # Threshold
    threshold_basis: str = "net"  # "net" (reserve only) | "gross" (all-in)
    threshold_reference_price: float = 0.50  # only used when basis == "gross"
    threshold_reference_w: float = 1.00  # only used when basis == "gross"

    label: str = "baseline"

    def manifest(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if k != "costs"}
        d["costs"] = self.costs.manifest()
        return d


@dataclass(frozen=True)
class Threshold:
    """A pre-registered entry threshold, frozen before any P&L is computed (G6)."""

    value_usd: float  # dollars per Kalshi contract
    basis: str
    components: Dict[str, float]
    derivation: str
    preregistered: bool = True

    @property
    def cents(self) -> float:
        return self.value_usd * 100.0

    def manifest(self) -> dict:
        return {
            "value_usd_per_contract": self.value_usd,
            "value_cents_per_contract": self.cents,
            "basis": self.basis,
            "components_usd": self.components,
            "derivation": self.derivation,
            "preregistered": self.preregistered,
        }


# ---------------------------------------------------------------------------
# Core pricing math
# ---------------------------------------------------------------------------


def q_cme(F: float, S0: float, S1: float) -> float:
    """CME-style normalized state price ``(S0 - F) / (S0 - S1)``.

    ``S0`` is the instrument's value in the base state, ``S1`` its value in the
    target state, ``F`` the tradable price. Not clipped to [0,1]: a q outside
    the unit interval is a real observation about the futures price and the
    caller should see it rather than a silently winsorized version.
    """
    span = S0 - S1
    if span == 0:
        raise ValueError("S0 == S1: instrument has no exposure to this decision")
    return (S0 - F) / span


def hedge_ratio(h: float) -> float:
    """Kalshi contracts per unit of instrument: ``N = |4167 * h|``."""
    return abs(ZQ_POINT_MULTIPLIER * h)


def state_values(spec: InstrumentSpec, baseline: float, target_delta: float) -> Tuple[float, float, float]:
    """Return ``(V_base, V_target, h)`` in price points."""
    h = target_delta * spec.W
    return baseline, baseline - target_delta * spec.W, h


def announcement_jump(h: float, p_pre: float) -> Tuple[float, float]:
    """Expected price jump at the announcement, in price points.

    The move is the SURPRISE, not the full state distance:
    ``base_jump = p_pre * h`` and ``target_jump = -(1 - p_pre) * h``.
    (Reproduced on 2026-07-29, a hold: Kalshi implied E[move] 6.158bp on 07-28,
    ZQ Aug 96.3000 -> 96.3625 = +6.25bp, residual +0.09bp.)
    """
    return p_pre * h, -(1.0 - p_pre) * h


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

_ROW_ALIASES: Dict[str, Tuple[str, ...]] = {
    "ts": ("ts", "timestamp", "t"),
    "kalshi_ask": ("kalshi_ask", "ask", "ask_close", "target_ask"),
    # data_contract.py names the provenance instant ``available_at``; both that
    # convention and a ``*_src_ts`` spelling are accepted.
    "kalshi_ask_src_ts": (
        "kalshi_ask_src_ts", "kalshi_src_ts", "ask_src_ts",
        "kalshi_available_at", "ask_available_at", "kalshi_ask_available_at",
    ),
    "kalshi_volume": ("kalshi_volume", "volume", "vol"),
    "instr_price": ("instr_price", "zq_price", "price", "instrument_price", "settle"),
    "instr_price_src_ts": (
        "instr_price_src_ts", "zq_src_ts", "price_src_ts",
        "zq_available_at", "instr_price_available_at", "price_available_at",
    ),
}

#: Suffixes that mark a column as a provenance instant for the G1 scan.
_PROVENANCE_SUFFIXES = ("_src_ts", "_asof", "available_at")

#: Book-quality flags from data_contract.read_kalshi_candles. A degenerate book
#: (bid 0.00 / ask 1.00, posted once the book is pulled on resolution day), a
#: crossed book, or a still-forming partial bar is not an executable quote, and
#: an "ask" of 1.00 is not a price anyone could have paid.
_BOOK_REJECT_FLAGS = ("book_degenerate", "crossed", "is_partial")


def _book_reject_reason(row: Dict[str, Any]) -> str:
    for flag in _BOOK_REJECT_FLAGS:
        v = _get(row, flag, required=False)
        if v is not None and bool(v):
            return flag
    return ""

_REQUIRED_ROW_FIELDS = ("ts", "kalshi_ask", "kalshi_volume", "instr_price")
_REQUIRED_PROVENANCE_FIELDS = ("kalshi_ask_src_ts", "instr_price_src_ts")


def _get(row: Dict[str, Any], canonical: str, required: bool = True) -> Any:
    """Fetch a field by canonical name, trying its aliases.

    NaN counts as absent. A DataFrame round-trip turns every missing cell into
    NaN, and NaN is neither None nor falsy: an ungarded ``bool(nan)`` is True,
    which would silently mark clean rows as having a degenerate book, and a NaN
    ask would poison q without raising.
    """
    for key in _ROW_ALIASES.get(canonical, (canonical,)):
        if key not in row:
            continue
        v = row[key]
        if v is None:
            continue
        if isinstance(v, float) and v != v:  # NaN
            continue
        return v
    if required:
        raise PanelSchemaError(
            f"panel row is missing required field {canonical!r} "
            f"(tried aliases {_ROW_ALIASES.get(canonical, (canonical,))}); "
            f"row keys present: {sorted(row.keys())}"
        )
    return None


def _to_epoch(x: Any, *, field_name: str, strict: bool) -> int:
    """Normalize a timestamp to integer epoch seconds UTC.

    Naive datetimes are REFUSED under strict provenance. The entire reason this
    harness exists is a timezone/labelling misalignment between a 16:00 ET CME
    settle and a 00:00 ET Kalshi bar end; silently assuming UTC on a naive
    timestamp is how that bug gets reintroduced.
    """
    # numpy scalars (kalshi_candles.ts is int64, and DataFrame.to_dict("records")
    # hands back numpy.int64, which is not a Python int).
    if hasattr(x, "item") and not isinstance(x, (str, bytes, dt.date)):
        try:
            x = x.item()
        except Exception:  # pragma: no cover - exotic array-likes
            pass
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        if isinstance(x, float) and x != x:
            raise PanelSchemaError(f"{field_name} is NaN")
        return int(x)
    if isinstance(x, dt.datetime):
        if x.tzinfo is None:
            if strict:
                raise LookaheadViolation(
                    f"{field_name} is a naive datetime ({x!r}). Timezone-naive timestamps "
                    f"cannot be compared across the Kalshi (bar-END, ET) and IBKR "
                    f"(bar-START) conventions without ambiguity. Make it tz-aware in panel.py."
                )
            x = x.replace(tzinfo=dt.timezone.utc)
        return int(x.timestamp())
    if isinstance(x, dt.date):
        return int(dt.datetime(x.year, x.month, x.day, tzinfo=dt.timezone.utc).timestamp())
    if isinstance(x, str):
        s = x.replace("Z", "+00:00")
        parsed = dt.datetime.fromisoformat(s)
        return _to_epoch(parsed, field_name=field_name, strict=strict)
    raise PanelSchemaError(f"cannot interpret {field_name}={x!r} as a timestamp")


def _normalize_rows(panel_obj: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Accept (rows, meta), a DataFrame with .attrs, or a plain iterable."""
    meta: Dict[str, Any] = {}
    obj = panel_obj
    if isinstance(panel_obj, tuple) and len(panel_obj) == 2:
        obj, meta = panel_obj[0], dict(panel_obj[1] or {})
    if hasattr(obj, "to_dict") and hasattr(obj, "columns"):  # pandas DataFrame
        meta = {**dict(getattr(obj, "attrs", {}) or {}), **meta}
        rows = obj.to_dict("records")
    elif hasattr(obj, "rows"):
        rows = list(obj.rows)
        meta = {**dict(getattr(obj, "meta", {}) or {}), **meta}
    else:
        rows = [dict(r) for r in obj]
    return [dict(r) for r in rows], meta


def assert_no_lookahead(
    rows: Sequence[Dict[str, Any]], meta: Dict[str, Any], assumptions: RunAssumptions, label: str
) -> Dict[str, Any]:
    """G1. Refuse to run if any source timestamp postdates its row timestamp.

    Also refuses, under ``require_provenance``, when the panel supplies no
    provenance at all or has not declared that the bar-labelling lag was
    applied. An unprovable panel is treated exactly like a violating one.
    """
    strict = assumptions.require_provenance
    if strict and not meta.get("bar_lag_applied"):
        raise LookaheadViolation(
            f"[{label}] panel meta does not declare 'bar_lag_applied'. Kalshi candle ts is "
            f"the bar END and IBKR bars are labelled by bar START; panel.py must reconcile "
            f"the two and apply an explicit one-bar lag, then declare it."
        )

    violations: List[Dict[str, Any]] = []
    checked = 0
    for i, raw in enumerate(rows):
        ts = _to_epoch(_get(raw, "ts"), field_name="ts", strict=strict)
        src_keys = [k for k in raw.keys() if k.endswith(_PROVENANCE_SUFFIXES)]
        if strict:
            present = {
                canon
                for canon in _REQUIRED_PROVENANCE_FIELDS
                if _get(raw, canon, required=False) is not None
            }
            missing = set(_REQUIRED_PROVENANCE_FIELDS) - present
            if missing:
                raise LookaheadViolation(
                    f"[{label}] row {i} (ts={ts}) carries no provenance for {sorted(missing)}. "
                    f"G1 cannot be proven, so the engine refuses to run. Emit one provenance "
                    f"instant per observed field in panel.py, named either '<field>_src_ts' or "
                    f"'<field>_available_at' (the data_contract.py spelling)."
                )
        for k in src_keys:
            v = raw[k]
            if v is None:
                continue
            src = _to_epoch(v, field_name=k, strict=strict)
            checked += 1
            if src > ts:
                violations.append({"row": i, "ts": ts, "field": k, "src_ts": src, "lead_secs": src - ts})

    if violations:
        head = violations[:5]
        raise LookaheadViolation(
            f"[{label}] {len(violations)} row(s) use information from the future. "
            f"First offenders: {head}. This is gate G1; the engine refuses to run."
        )
    return {"rows_checked": len(rows), "provenance_comparisons": checked, "violations": 0}


def assert_selection_purity(spec: InstrumentSpec, label: str) -> None:
    """G4. Contract choice must be a pure function of the published FOMC calendar."""
    extra = set(spec.selection_inputs) - ALLOWED_SELECTION_INPUT_KEYS
    if extra:
        raise SelectionPurityError(
            f"[{label}] select_instrument used non-calendar inputs {sorted(extra)}. "
            f"Contract selection must depend only on the FOMC calendar (published a year "
            f"ahead) and the civil calendar. Allowed keys: {sorted(ALLOWED_SELECTION_INPUT_KEYS)}"
        )
    if not spec.selection_rule:
        raise SelectionPurityError(f"[{label}] select_instrument did not declare a selection_rule")


def _coerce_spec(obj: Any) -> InstrumentSpec:
    if isinstance(obj, InstrumentSpec):
        return obj
    if isinstance(obj, dict):
        d = dict(obj)
    else:
        d = {
            k: getattr(obj, k)
            for k in (
                "kind", "legs", "w_by_month", "selection_rule", "selection_inputs",
                "baseline", "baseline_source", "symbol", "notes",
            )
            if hasattr(obj, k)
        }
    try:
        legs = tuple((str(m), float(c)) for m, c in d["legs"])
        return InstrumentSpec(
            kind=d.get("kind", "outright" if len(legs) == 1 else "spread"),
            legs=legs,
            w_by_month={str(k): float(v) for k, v in d["w_by_month"].items()},
            selection_rule=d.get("selection_rule", ""),
            selection_inputs=dict(d.get("selection_inputs", {})),
            baseline=d.get("baseline"),
            baseline_source=d.get("baseline_source", "unset"),
            symbol=d.get("symbol", ""),
            notes=d.get("notes", ""),
        )
    except KeyError as e:
        raise PanelSchemaError(
            f"select_instrument returned an object missing {e}; engine needs at least "
            f"legs, w_by_month, selection_rule, selection_inputs"
        ) from e


# ---------------------------------------------------------------------------
# Threshold (G6): derived from first principles, frozen before any P&L
# ---------------------------------------------------------------------------


def derive_preregistered_threshold(assumptions: RunAssumptions) -> Threshold:
    """Derive the entry threshold without touching a single market observation.

    basis="net" (default). ``evaluate_entry`` already charges every DETERMINISTIC
    friction -- the Kalshi fee at the quoted ask, the assumed ZQ crossing, the
    assumed depth slippage -- against the edge. What remains uncharged is the one
    friction that is a distribution rather than a number: EFFR pass-through
    error. The threshold is therefore that reserve, plus the depth allowance
    that cannot be charged per-contract because size is unobservable.

    basis="gross". For readers who prefer a threshold on the raw ``q - a``, the
    same components plus fees and crossing evaluated at a declared REFERENCE
    price and REFERENCE w. The reference values are parameters, not fitted, and
    the resulting number is only comparable to a gross edge.

    Neither branch looks at P&L. That is the entire point of G6.
    """
    c = assumptions.costs
    reserve = _costs.hedge_error_reserve(c)
    slip = c.kalshi_slippage_cents_per_contract / 100.0

    if assumptions.threshold_basis == "net":
        components = {"hedge_error_reserve": reserve, "kalshi_depth_allowance": slip}
        total = reserve + slip
        derivation = (
            f"net-basis threshold = hedge_error_reserve(mode={c.hedge_error_mode}, "
            f"x{c.hedge_error_multiple}) + kalshi_depth_allowance. Fees, ZQ crossing and "
            f"financing are charged explicitly inside evaluate_entry and are therefore NOT "
            f"repeated here. The reserve is the measured EFFR pass-through error "
            f"(MAE {c.hedge_error_mae_cents}c, max {c.hedge_error_max_cents}c per $1/25bp "
            f"digital, 2022-2025 sample)."
        )
    elif assumptions.threshold_basis == "gross":
        p = assumptions.threshold_reference_price
        w = assumptions.threshold_reference_w
        h_ref = assumptions.target_delta * w
        fee_ref = _costs.kalshi_taker_fee(p, 1.0, c)
        crossings = 2 if assumptions.decision_exit == "announcement" else 1
        zq_ref = _costs.zq_cost_per_kalshi_contract(h_ref, c) * crossings
        components = {
            "kalshi_fee_at_reference_price": fee_ref,
            "zq_crossing_at_reference_w": zq_ref,
            "hedge_error_reserve": reserve,
            "kalshi_depth_allowance": slip,
        }
        total = fee_ref + zq_ref + reserve + slip
        derivation = (
            f"gross-basis threshold evaluated at reference price p={p} and reference "
            f"w={w} ({crossings} ZQ crossing(s)). Reference values are declared parameters, "
            f"not fitted. Compare only against gross edge (q - a). The fee term is priced "
            f"on a single contract, so ceil-to-cent rounding makes it a conservative upper "
            f"bound on the per-contract fee (2.00c vs 1.75c at p=0.50); evaluate_entry "
            f"charges the exact fee on the actual clip."
        )
    else:
        raise ValueError(f"unknown threshold_basis {assumptions.threshold_basis!r}")

    return Threshold(
        value_usd=total, basis=assumptions.threshold_basis, components=components,
        derivation=derivation, preregistered=True,
    )


# ---------------------------------------------------------------------------
# Entry evaluation
# ---------------------------------------------------------------------------


def evaluate_entry(
    panel_row: Dict[str, Any],
    assumptions: RunAssumptions,
    spec: Optional[InstrumentSpec] = None,
    *,
    effr_asof: Optional[Callable[[Any], float]] = None,
) -> Dict[str, Any]:
    """Price one cross-section. Returns edge, every cost component, and the inputs.

    Costs here are PER KALSHI CONTRACT and size-independent: the ZQ crossing
    amortizes to ``tick/(4167|h|)`` regardless of clip size, and the Kalshi fee
    is linear in contracts up to a sub-cent rounding. Sizing therefore does not
    feed back into the entry decision, and ``size_position`` runs afterwards.

    The returned ``inputs`` dict is the audit record: every number that produced
    the decision, including which source supplied the baseline (G2).
    """
    row = dict(panel_row)
    spec = spec if spec is not None else _coerce_spec(_get(row, "instrument"))
    c = assumptions.costs
    strict = assumptions.require_provenance

    ts = _to_epoch(_get(row, "ts"), field_name="ts", strict=strict)
    a = float(_get(row, "kalshi_ask"))
    P = float(_get(row, "instr_price"))
    volume = float(_get(row, "kalshi_volume"))
    _costs._check_price(a)

    # ---- baseline (G2) --------------------------------------------------
    baseline, baseline_source = _resolve_baseline(row, spec, assumptions, effr_asof, ts)

    V_base, V_target, h = state_values(spec, baseline, assumptions.target_delta)
    if h == 0:
        raise ExclusionReason(
            f"instrument has zero exposure to this decision (W={spec.W}); "
            f"nothing to replicate"
        )
    q = q_cme(P, V_base, V_target)
    N_per_unit = hedge_ratio(h)
    n_sign = 1 if h > 0 else -1

    gross_edge = q - a  # dollars per Kalshi contract, state-independent

    # ---- costs per Kalshi contract --------------------------------------
    trade_date = _trade_date(row, ts)
    fee_block = _costs.kalshi_entry_fee(a, N_per_unit, c, trade_date)
    kalshi_fee_pc = fee_block / N_per_unit
    slip_pc = c.kalshi_slippage_cents_per_contract / 100.0
    zq_cross_pc = _costs.zq_cost_per_kalshi_contract(h, c, legs=spec.n_legs)

    # Exit (a) crosses ZQ a second time. Exit (b) holds to cash settlement and
    # pays financing instead (zero by default, by declared assumption).
    zq_exit_pc_announce = zq_cross_pc
    days_held = _days_to_settlement(row)
    fin_pc = (
        _costs.zq_financing_cost(1.0, days_held, c) / N_per_unit if days_held is not None else 0.0
    )
    settle_fee_pc = c.kalshi_settlement_fee_per_contract

    cost_announce = kalshi_fee_pc + slip_pc + zq_cross_pc + zq_exit_pc_announce + settle_fee_pc
    cost_settle = kalshi_fee_pc + slip_pc + zq_cross_pc + fin_pc + settle_fee_pc

    net_announce = gross_edge - cost_announce
    net_settle = gross_edge - cost_settle
    net_used = net_announce if assumptions.decision_exit == "announcement" else net_settle

    # ---- two-state completeness diagnostic ------------------------------
    leakage, leak_flag = _state_leakage(row, assumptions)

    return {
        "ts": ts,
        "trade_date": trade_date.isoformat(),
        "kalshi_ask": a,
        "instr_price": P,
        "kalshi_volume": volume,
        "q_cme": q,
        "gross_edge_per_contract": gross_edge,
        "gross_edge_cents": gross_edge * 100.0,
        "net_edge_per_contract": net_used,
        "net_edge_cents": net_used * 100.0,
        "net_edge_announce": net_announce,
        "net_edge_settle": net_settle,
        "cost_kalshi_fee_pc": kalshi_fee_pc,
        "cost_kalshi_slippage_pc": slip_pc,
        "cost_zq_entry_pc": zq_cross_pc,
        "cost_zq_exit_pc": zq_exit_pc_announce,
        "cost_zq_financing_pc": fin_pc,
        "cost_kalshi_settlement_pc": settle_fee_pc,
        "cost_total_announce_pc": cost_announce,
        "cost_total_settle_pc": cost_settle,
        "h": h,
        "W": spec.W,
        "N_per_unit": N_per_unit,
        "n_sign": n_sign,
        "V_base": V_base,
        "V_target": V_target,
        "baseline_source": baseline_source,
        "r_free": spec.is_balanced and baseline_source == "spread_identity",
        "residual_state_prob": leakage,
        "two_state_violation": leak_flag,
        "book_reject_reason": _book_reject_reason(row),
        "inputs": {
            "instrument": spec.manifest(),
            "target_outcome": assumptions.target_outcome,
            "base_outcome": assumptions.base_outcome,
            "target_delta": assumptions.target_delta,
            "baseline": baseline,
            "baseline_source": baseline_source,
            "kalshi_ask": a,
            "instr_price": P,
            "h": h,
            "q_cme": q,
            "N_per_unit": N_per_unit,
            "days_to_settlement": days_held,
            "trade_date": trade_date.isoformat(),
            "cost_assumptions_label": c.label,
            "zq_ticks_crossed": c.zq_ticks_crossed,
            "kalshi_execution_style": c.kalshi_execution_style,
        },
    }


def _resolve_baseline(
    row: Dict[str, Any],
    spec: InstrumentSpec,
    assumptions: RunAssumptions,
    effr_asof: Optional[Callable[[Any], float]],
    ts: int,
) -> Tuple[float, str]:
    """Get V_base, recording which gate it went through (G2).

    A balanced spread with no contaminating meeting has V_base == 0 by identity
    and touches no EFFR observation at all. An outright must reach for the EFFR
    level, which is published ~09:00 ET on D+1 -- so it must come from
    ``panel.effr_asof(t)``, never from a same-day EFFR print.
    """
    explicit = _get(row, "baseline", required=False)
    if explicit is not None:
        return float(explicit), str(_get(row, "baseline_source", required=False) or spec.baseline_source or "declared")
    if spec.baseline is not None:
        return float(spec.baseline), spec.baseline_source or "declared"
    if spec.is_balanced:
        # sum(c_j) == 0 and, per panel's selection rule, no other meeting inside
        # the legs: the unknown EFFR level cancels out of the spread entirely.
        return 0.0, "spread_identity"
    if effr_asof is None:
        raise PanelSchemaError(
            "outright instrument needs a baseline (V_base = 100 - R) but the panel supplied "
            "neither a baseline nor an effr_asof callable. G2 forbids reaching for EFFR[D] "
            "directly: it is published ~09:00 ET on D+1. Prefer a balanced spread, whose "
            "baseline is R-free by identity."
        )
    R = float(effr_asof(ts))
    legs_sum = sum(coef for _, coef in spec.legs)
    return legs_sum * (100.0 - R), "effr_asof"


def _trade_date(row: Dict[str, Any], ts: int) -> dt.date:
    td = _get(row, "trade_date_et", required=False) or _get(row, "trade_date", required=False)
    if td is not None:
        return _costs._as_date(td)
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).date()


def _days_to_settlement(row: Dict[str, Any]) -> Optional[float]:
    """Calendar days from this row to ZQ cash settlement, if the panel supplies it.

    None means the panel did not say, and financing is then charged as zero --
    which is the declared default assumption, not a measurement.
    """
    d = _get(row, "days_to_settlement", required=False)
    return float(d) if d is not None else None


def _state_leakage(row: Dict[str, Any], assumptions: RunAssumptions) -> Tuple[Optional[float], Optional[bool]]:
    """How much probability sits OUTSIDE the two states being replicated.

    The Kalshi event has five outcomes (CUT50P, CUT25, HOLD, HIKE25, HIKE50P).
    The replication assumes the world is {base, target}. When the panel supplies
    the other outcome prices, this measures how false that is. A meeting where
    the live contest is HOLD vs CUT25 cannot be replicated with a HIKE25 leg,
    and this is the number that says so.
    """
    sp = _get(row, "kalshi_state_prices", required=False)
    if not isinstance(sp, dict) or not sp:
        return None, None
    p_base = float(sp.get(assumptions.base_outcome, 0.0) or 0.0)
    p_tgt = float(sp.get(assumptions.target_outcome, 0.0) or 0.0)
    residual = 1.0 - p_base - p_tgt
    return residual, bool(abs(residual) > assumptions.two_state_leakage_tol)


# ---------------------------------------------------------------------------
# Sizing (G7)
# ---------------------------------------------------------------------------


def size_position(
    N_per_unit: float, volume: float, assumptions: RunAssumptions, units_already: int = 0
) -> Dict[str, Any]:
    """Cap the clip at a fraction of the bar's observed Kalshi volume.

    The hedge is lumpy: one instrument unit requires ``N_per_unit`` Kalshi
    contracts, and ``N_per_unit`` is around 1042*w. If the volume cap does not
    fund a single whole unit, there is no trade -- rounding a fractional ZQ
    position into existence would be fabricating liquidity. The integer rounding
    that remains leaves a small unhedged digital residual, which is reported
    rather than swept away.
    """
    cap_contracts = assumptions.volume_participation * max(volume, 0.0)
    units = int(math.floor(cap_contracts / N_per_unit)) if N_per_unit > 0 else 0
    units = min(units, assumptions.max_instrument_units, max(assumptions.max_total_units - units_already, 0))
    if units < 1:
        return {
            "units": 0,
            "kalshi_contracts": 0,
            "hedge_residual_contracts": 0.0,
            "reason": "size_below_one_hedge_lot",
            "cap_contracts": cap_contracts,
        }
    kalshi_contracts = int(round(units * N_per_unit))
    return {
        "units": units,
        "kalshi_contracts": kalshi_contracts,
        "hedge_residual_contracts": kalshi_contracts - units * N_per_unit,
        "reason": "",
        "cap_contracts": cap_contracts,
    }


# ---------------------------------------------------------------------------
# Per-meeting run
# ---------------------------------------------------------------------------


def run_meeting(
    meeting: Dict[str, Any],
    freq: int,
    assumptions: RunAssumptions,
    threshold: Threshold,
    *,
    panel: Any = None,
) -> Dict[str, Any]:
    """Evaluate every timestamp for one meeting and price both exits.

    Counterfactual P&L is computed at EVERY timestamp regardless of the
    threshold, so that ``threshold_curve`` and ``walk_forward`` are pure filters
    over a fixed set of observations rather than re-runs. The threshold passed
    here only marks which rows the pre-registered rule would have taken.
    """
    panel = _load_panel(panel)
    label = str(meeting.get("date") or meeting.get("meeting_date"))

    spec = _coerce_spec(panel.select_instrument(meeting))
    assert_selection_purity(spec, label)

    rows, meta = _normalize_rows(panel.build_panel(meeting, freq))
    if not rows:
        raise ExclusionReason("panel returned no rows")
    gate_report = assert_no_lookahead(rows, meta, assumptions, label)

    resolution = _load_resolution(panel, meeting)
    effr_fn = getattr(panel, "effr_asof", None)

    decisions: List[Dict[str, Any]] = []
    for raw in rows:
        ev = evaluate_entry(raw, assumptions, spec, effr_asof=effr_fn)
        sizing = size_position(ev["N_per_unit"], ev["kalshi_volume"], assumptions)
        ev.update({f"size_{k}": v for k, v in sizing.items()})
        ev["meeting"] = label
        ev["freq"] = freq
        # A row is tradable only if the book was executable AND the volume cap
        # funded at least one whole hedge lot.
        reject = ev["book_reject_reason"] or sizing["reason"]
        ev["tradable"] = not reject
        ev["not_tradable_reason"] = reject
        ev["passes_preregistered_threshold"] = bool(
            _edge_on_basis(ev, threshold.basis) >= threshold.value_usd
        )
        pnl = _realized_pnl(ev, sizing, spec, assumptions, resolution, tradable=ev["tradable"], reject=reject)
        ev.update(pnl)
        decisions.append(ev)

    summary = _summarize_meeting(label, spec, decisions, assumptions, threshold, resolution, gate_report, freq)
    return {"meeting": label, "spec": spec, "decisions": decisions, "summary": summary, "resolution": resolution}


def _edge_on_basis(ev: Dict[str, Any], basis: str) -> float:
    return ev["gross_edge_per_contract"] if basis == "gross" else ev["net_edge_per_contract"]


def _realized_pnl(
    ev: Dict[str, Any],
    sizing: Dict[str, Any],
    spec: InstrumentSpec,
    assumptions: RunAssumptions,
    resolution: Optional[Resolution],
    *,
    tradable: bool = True,
    reject: str = "",
) -> Dict[str, Any]:
    """P&L in dollars for the clip, under both exits.

    Realized, not modelled: the Kalshi leg pays its actual settlement and the ZQ
    leg is marked at an actually-observed exit price (exit a) or at the value
    implied by realized EFFR (exit b). The hedge-error reserve is NOT subtracted
    here -- realized prices already contain whatever pass-through error occurred,
    and charging the reserve on top would double count it.
    """
    out: Dict[str, Any] = {
        "pnl_announce": None,
        "pnl_settle": None,
        "pnl_unavailable_reason": "",
        "replication_held": None,
        "jump_residual_points": None,
    }
    units, n_k = sizing["units"], sizing["kalshi_contracts"]
    if not tradable or units < 1:
        out["pnl_unavailable_reason"] = reject or "not_tradable_at_this_size"
        return out
    if resolution is None:
        out["pnl_unavailable_reason"] = "no_resolution_data"
        return out

    c = assumptions.costs
    a, P = ev["kalshi_ask"], ev["instr_price"]
    h, n_sign = ev["h"], ev["n_sign"]
    trade_date = _costs._as_date(ev["trade_date"])

    kalshi_outlay = n_k * a + _costs.kalshi_entry_fee(a, n_k, c, trade_date) + _costs.kalshi_slippage_cost(n_k, c)
    kalshi_payoff = n_k * (1.0 if resolution.outcome == assumptions.target_outcome else 0.0)
    kalshi_settle_fee = n_k * c.kalshi_settlement_fee_per_contract
    zq_entry = _costs.zq_crossing_cost(units, c, legs=spec.n_legs)
    kalshi_net = kalshi_payoff - kalshi_outlay - kalshi_settle_fee

    out["replication_held"] = resolution.outcome in (assumptions.base_outcome, assumptions.target_outcome)

    # (a) unwind the futures leg at the announcement
    if resolution.exit_price is not None:
        zq_exit = _costs.zq_crossing_cost(units, c, legs=spec.n_legs)
        zq_pnl = n_sign * units * ZQ_POINT_MULTIPLIER * (resolution.exit_price - P)
        out["pnl_announce"] = kalshi_net + zq_pnl - zq_entry - zq_exit
        if resolution.p_pre is not None:
            expected_base, expected_target = announcement_jump(h, resolution.p_pre)
            expected = expected_target if resolution.outcome == assumptions.target_outcome else expected_base
            out["jump_residual_points"] = (resolution.exit_price - P) - expected
    else:
        out["pnl_unavailable_reason"] = "no_announcement_exit_price"

    # (b) hold the futures leg to cash settlement on realized EFFR
    if resolution.settle_price is not None:
        days = ev["inputs"].get("days_to_settlement")
        fin = _costs.zq_financing_cost(units, days, c) if days is not None else 0.0
        zq_pnl = n_sign * units * ZQ_POINT_MULTIPLIER * (resolution.settle_price - P)
        out["pnl_settle"] = kalshi_net + zq_pnl - zq_entry - fin
    else:
        reason = "no_settlement_price"
        out["pnl_unavailable_reason"] = (
            f"{out['pnl_unavailable_reason']}|{reason}" if out["pnl_unavailable_reason"] else reason
        )
    return out


def _summarize_meeting(
    label, spec, decisions, assumptions, threshold, resolution, gate_report, freq
) -> Dict[str, Any]:
    taken = _select_entries(decisions, threshold.value_usd, threshold.basis, assumptions)
    nets = [d["net_edge_cents"] for d in decisions]
    tradable = [d for d in decisions if d["tradable"]]
    return {
        "meeting": label,
        "freq": freq,
        "instrument": spec.symbol or "+".join(f"{c:+g}{m}" for m, c in spec.legs),
        "kind": spec.kind,
        "W": spec.W,
        "h": assumptions.target_delta * spec.W,
        "N_per_unit": hedge_ratio(assumptions.target_delta * spec.W),
        "r_free": spec.is_balanced,
        "baseline_source": decisions[0]["baseline_source"] if decisions else "",
        "n_timestamps": len(decisions),
        "n_tradable": len(tradable),
        "n_entries_preregistered": len(taken),
        "median_net_edge_cents": _median(nets),
        "pct_net_positive": (100.0 * sum(1 for x in nets if x > 0) / len(nets)) if nets else None,
        "max_residual_state_prob": _max_opt([d["residual_state_prob"] for d in decisions]),
        "any_two_state_violation": any(bool(d["two_state_violation"]) for d in decisions),
        "replication_held": resolution.outcome in (assumptions.base_outcome, assumptions.target_outcome) if resolution else None,
        "realized_outcome": resolution.outcome if resolution else None,
        "pnl_announce": _total([d["pnl_announce"] for d in taken if d["pnl_announce"] is not None], len(taken)),
        "pnl_settle": _total([d["pnl_settle"] for d in taken if d["pnl_settle"] is not None], len(taken)),
        "n_entries_missing_pnl": sum(1 for d in taken if d["pnl_announce"] is None and d["pnl_settle"] is None),
        "pnl_unavailable_reasons": ";".join(sorted({d["pnl_unavailable_reason"] for d in taken if d["pnl_unavailable_reason"]})),
        "contracts_traded": sum(d["size_kalshi_contracts"] for d in taken),
        "units_traded": sum(d["size_units"] for d in taken),
        "gate_G1": gate_report,
    }


def _select_entries(
    decisions: Sequence[Dict[str, Any]], tau: float, basis: str, assumptions: RunAssumptions
) -> List[Dict[str, Any]]:
    """Apply a threshold to pre-computed rows. Pure filter, no re-pricing."""
    qualifying = [d for d in sorted(decisions, key=lambda r: r["ts"]) if d["tradable"] and _edge_on_basis(d, basis) >= tau]
    if not qualifying:
        return []
    if assumptions.entry_policy == "first":
        return qualifying[:1]
    taken, used = [], 0
    for d in qualifying:
        if used + d["size_units"] > assumptions.max_total_units:
            break
        taken.append(d)
        used += d["size_units"]
    return taken


def apply_threshold(
    meeting_results: Sequence[Dict[str, Any]], tau: float, assumptions: RunAssumptions, basis: str = "net"
) -> Dict[str, Any]:
    """Aggregate P&L across meetings at an arbitrary threshold.

    Two different things both look like "no P&L" and must not be conflated:

      no entries taken      -> total is 0.0. A threshold high enough to trade
                               nothing is a legitimate strategy, and it must be
                               scoreable or the walk-forward optimizer can never
                               choose it over a losing one. Standing aside is the
                               trivial control and it competes on the same terms.
      entries, none priced  -> total is None. There is no result to report.
      entries, some priced  -> total sums the priced ones and the rest are
                               counted in n_entries_missing_pnl_*. That total is
                               an incomplete sum, not a result: any downstream
                               read must check the missing count before quoting
                               it, because an omitted losing fill flatters it.
    """
    per_meeting, pa, ps, n_entries = [], [], [], 0
    miss_a = miss_s = 0
    for mr in meeting_results:
        taken = _select_entries(mr["decisions"], tau, basis, assumptions)
        n_entries += len(taken)
        a_vals = [d["pnl_announce"] for d in taken if d["pnl_announce"] is not None]
        s_vals = [d["pnl_settle"] for d in taken if d["pnl_settle"] is not None]
        miss_a += len(taken) - len(a_vals)
        miss_s += len(taken) - len(s_vals)
        per_meeting.append(
            {
                "meeting": mr["meeting"],
                "n_entries": len(taken),
                "pnl_announce": _total(a_vals, len(taken)),
                "pnl_settle": _total(s_vals, len(taken)),
            }
        )
        pa.extend(a_vals)
        ps.extend(s_vals)
    return {
        "threshold_usd": tau,
        "threshold_cents": tau * 100.0,
        "basis": basis,
        "n_entries": n_entries,
        "n_meetings_traded": sum(1 for m in per_meeting if m["n_entries"] > 0),
        "n_entries_missing_pnl_announce": miss_a,
        "n_entries_missing_pnl_settle": miss_s,
        "pnl_announce_total": _total(pa, n_entries),
        "pnl_settle_total": _total(ps, n_entries),
        "pnl_announce_mean_per_entry": (sum(pa) / len(pa)) if pa else None,
        "pnl_settle_mean_per_entry": (sum(ps) / len(ps)) if ps else None,
        "per_meeting": per_meeting,
    }


def _total(values: Sequence[float], n_entries: int) -> Optional[float]:
    """0.0 when nothing was traded; None when something was traded but unpriced."""
    if n_entries == 0:
        return 0.0
    return sum(values) if values else None


# ---------------------------------------------------------------------------
# Threshold curve (G6) and walk-forward (G6)
# ---------------------------------------------------------------------------


def threshold_curve(
    meeting_results: Sequence[Dict[str, Any]],
    assumptions: RunAssumptions,
    prereg: Threshold,
    grid_cents: Optional[Sequence[float]] = None,
) -> List[Dict[str, Any]]:
    """Net P&L as a function of entry threshold.

    Exactly one row of this table is honest: the one flagged
    ``is_preregistered``. The rest exist so the reader can see the shape of the
    curve and judge how much of any positive result is a threshold that was
    chosen after the fact. A peak elsewhere on this curve is not a result.
    """
    if grid_cents is None:
        grid_cents = [round(-2.0 + 0.25 * i, 4) for i in range(41)]  # -2c .. +8c
    grid = sorted({round(float(g), 6) for g in grid_cents} | {round(prereg.cents, 6)})
    out = []
    for g in grid:
        agg = apply_threshold(meeting_results, g / 100.0, assumptions, basis=prereg.basis)
        agg.pop("per_meeting", None)
        agg["is_preregistered"] = abs(g - prereg.cents) < 1e-9
        agg["honest"] = agg["is_preregistered"]
        out.append(agg)
    return out


def walk_forward(
    meeting_results: Sequence[Dict[str, Any]],
    assumptions: RunAssumptions,
    prereg: Threshold,
    grid_cents: Optional[Sequence[float]] = None,
    min_train: int = 2,
    objective: str = "pnl_announce_total",
) -> Dict[str, Any]:
    """Estimate the threshold on meetings 1..k, test on k+1. OOS aggregates only.

    Reported P&L comes exclusively from the test meeting at each step. The
    in-sample optimum is recorded so the reader can see how unstable it is, but
    it never enters the aggregate.
    """
    ordered = sorted(meeting_results, key=lambda m: m["meeting"])
    if grid_cents is None:
        grid_cents = [round(-2.0 + 0.25 * i, 4) for i in range(41)]
    steps: List[Dict[str, Any]] = []
    for k in range(min_train, len(ordered)):
        train, test = ordered[:k], [ordered[k]]
        best_tau, best_obj = None, None
        for g in grid_cents:
            agg = apply_threshold(train, g / 100.0, assumptions, basis=prereg.basis)
            val = agg.get(objective)
            if val is None:
                continue
            if best_obj is None or val > best_obj:
                best_tau, best_obj = g, val
        if best_tau is None:
            steps.append(
                {"test_meeting": ordered[k]["meeting"], "n_train": k, "fitted_threshold_cents": None,
                 "skipped_reason": "no in-sample objective available"}
            )
            continue
        oos = apply_threshold(test, best_tau / 100.0, assumptions, basis=prereg.basis)
        prereg_oos = apply_threshold(test, prereg.value_usd, assumptions, basis=prereg.basis)
        steps.append(
            {
                "test_meeting": ordered[k]["meeting"],
                "n_train": k,
                "fitted_threshold_cents": best_tau,
                "in_sample_objective": best_obj,
                "oos_n_entries": oos["n_entries"],
                "oos_pnl_announce": oos["pnl_announce_total"],
                "oos_pnl_settle": oos["pnl_settle_total"],
                "prereg_oos_n_entries": prereg_oos["n_entries"],
                "prereg_oos_pnl_announce": prereg_oos["pnl_announce_total"],
                "prereg_oos_pnl_settle": prereg_oos["pnl_settle_total"],
            }
        )
    fitted = [s.get("oos_pnl_announce") for s in steps if s.get("oos_pnl_announce") is not None]
    preg = [s.get("prereg_oos_pnl_announce") for s in steps if s.get("prereg_oos_pnl_announce") is not None]
    taus = [s["fitted_threshold_cents"] for s in steps if s.get("fitted_threshold_cents") is not None]
    return {
        "steps": steps,
        "n_steps": len(steps),
        "oos_pnl_announce_total_fitted": sum(fitted) if fitted else None,
        "oos_pnl_announce_total_preregistered": sum(preg) if preg else None,
        "fitted_threshold_cents_min": min(taus) if taus else None,
        "fitted_threshold_cents_max": max(taus) if taus else None,
        "note": (
            "Only out-of-sample numbers appear here. The spread between "
            "fitted_threshold_cents_min and _max is the honest measure of how "
            "unstable the in-sample optimum is; a wide spread means the threshold "
            "carries no information."
        ),
    }


# ---------------------------------------------------------------------------
# Universe (G5) and the top-level run
# ---------------------------------------------------------------------------


def _load_panel(panel: Any = None):
    if panel is not None:
        return panel
    try:
        from . import panel as p  # type: ignore
        return p
    except ImportError:
        pass
    try:
        import panel as p  # type: ignore
        return p
    except ImportError as e:
        raise PanelUnavailable(
            "cannot import panel.py. The engine owns no data access; it needs "
            "fomc_calendar(), select_instrument(meeting), build_panel(meeting, freq) "
            "and effr_asof(t)."
        ) from e


def _load_resolution(panel: Any, meeting: Dict[str, Any]) -> Optional[Resolution]:
    fn = getattr(panel, "resolution", None)
    raw = fn(meeting) if callable(fn) else meeting.get("resolution")
    if raw is None:
        return None
    if isinstance(raw, Resolution):
        return raw
    d = dict(raw) if isinstance(raw, dict) else {
        k: getattr(raw, k) for k in Resolution.__dataclass_fields__ if hasattr(raw, k)
    }
    if "outcome" not in d:
        raise PanelSchemaError("resolution record has no 'outcome'")
    known = {k: v for k, v in d.items() if k in Resolution.__dataclass_fields__}
    return Resolution(**known)


def run(
    window: Tuple[str, str],
    freq: int = 1440,
    assumptions: Optional[RunAssumptions] = None,
    threshold: Optional[Threshold] = None,
    outdir: str = "results",
    *,
    panel: Any = None,
    grid_cents: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """Full backtest. Writes results/ and returns the same content in memory.

    Order matters: the threshold is derived and frozen BEFORE any meeting is
    priced (G6), and the universe is declared in full before any meeting is
    dropped (G5). A lookahead violation anywhere aborts the entire run rather
    than excluding one meeting -- it is a defect in the data layer, not a
    property of a meeting.
    """
    assumptions = assumptions or RunAssumptions()
    threshold = threshold or derive_preregistered_threshold(assumptions)
    panel = _load_panel(panel)
    os.makedirs(outdir, exist_ok=True)

    start, end = window
    all_meetings = [_meeting_dict(m) for m in panel.fomc_calendar()]
    in_window = [m for m in all_meetings if start <= m["date"] <= end]

    universe: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    for m in sorted(in_window, key=lambda x: x["date"]):
        row = {"meeting": m["date"], "status": "", "reason": "", "instrument": "", "n_timestamps": 0}
        try:
            mr = run_meeting(m, freq, assumptions, threshold, panel=panel)
        except LookaheadViolation:
            raise  # G1 is fatal for the whole run
        except (ExclusionReason, PanelSchemaError, SelectionPurityError, AssumptionRequired) as e:
            row.update({"status": "excluded", "reason": f"{type(e).__name__}: {e}"})
            universe.append(row)
            continue
        results.append(mr)
        row.update(
            {
                "status": "included",
                "instrument": mr["summary"]["instrument"],
                "n_timestamps": mr["summary"]["n_timestamps"],
                "reason": "",
            }
        )
        universe.append(row)

    for m in sorted(all_meetings, key=lambda x: x["date"]):
        if not (start <= m["date"] <= end):
            universe.append({"meeting": m["date"], "status": "out_of_window", "reason": f"outside {start}..{end}", "instrument": "", "n_timestamps": 0})

    curve = threshold_curve(results, assumptions, threshold, grid_cents) if results else []
    wf = walk_forward(results, assumptions, threshold, grid_cents) if len(results) > 2 else {"steps": [], "n_steps": 0, "note": "fewer than 3 included meetings; walk-forward not run"}
    headline = apply_threshold(results, threshold.value_usd, assumptions, basis=threshold.basis) if results else {}

    manifest = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "window": {"start": start, "end": end},
        "freq_minutes": freq,
        "run_assumptions": assumptions.manifest(),
        "preregistered_threshold": threshold.manifest(),
        "code_sha256": _code_hashes(),
        "panel_module": getattr(panel, "__file__", str(panel)),
        "universe_counts": _counts(universe),
        "gates": {
            "G1_timestamp_alignment": "enforced: assert_no_lookahead; any *_src_ts > ts aborts the run; naive datetimes refused; panel must declare bar_lag_applied",
            "G2_effr_publication_lag": "enforced: baseline comes from spread identity (R-free) or panel.effr_asof(t); baseline_source is recorded per row",
            "G3_fee_anachronism": "enforced: maker fees resolve through an effective-dated MakerRegime; an undeclared historical regime raises rather than defaulting to today's schedule",
            "G4_selection_purity": "enforced: assert_selection_purity whitelists calendar-only inputs",
            "G5_universe_declaration": "enforced: universe.csv lists every meeting with status and reason; no silent drops",
            "G6_threshold_not_fitted": "enforced: threshold derived before pricing; curve flags the single pre-registered point; walk_forward reports OOS only",
            "G7_execution_depth": "enforced: size capped at volume_participation of observed volume, ZQ tick charged, both swept via CostAssumptions",
        },
        "accounting_notes": [
            "Hedge-error reserve sets the threshold only; it is never charged to realized P&L, which already contains realized pass-through error.",
            "ZQ historical spread is not observable (IBKR BID_ASK bars are degenerate), so zq_ticks_crossed is an assumption to sweep.",
            "Kalshi depth is not observable; size is capped at a fraction of traded volume and kalshi_slippage_cents_per_contract is an assumption.",
        ],
    }

    decisions_flat = [_flatten_decision(d) for mr in results for d in mr["decisions"]]
    _write_csv(os.path.join(outdir, "universe.csv"), universe)
    _write_csv(os.path.join(outdir, "meetings.csv"), [_flatten_summary(mr["summary"]) for mr in results])
    _write_csv(os.path.join(outdir, "decisions.csv"), decisions_flat)
    _write_csv(os.path.join(outdir, "threshold_curve.csv"), curve)
    _write_csv(os.path.join(outdir, "walk_forward.csv"), wf.get("steps", []))
    _write_json(os.path.join(outdir, "manifest.json"), manifest)
    _write_json(os.path.join(outdir, "headline.json"), headline)
    _write_json(os.path.join(outdir, "walk_forward.json"), wf)

    return {
        "universe": universe,
        "results": results,
        "threshold": threshold,
        "threshold_curve": curve,
        "walk_forward": wf,
        "headline": headline,
        "manifest": manifest,
        "outdir": outdir,
    }


def _meeting_dict(m: Any) -> Dict[str, Any]:
    if isinstance(m, dict):
        d = dict(m)
    elif isinstance(m, (str, dt.date)):
        d = {"date": m}
    else:
        d = {k: getattr(m, k) for k in dir(m) if not k.startswith("_") and not callable(getattr(m, k))}
    raw = d.get("date") or d.get("meeting_date")
    if raw is None:
        raise PanelSchemaError(f"meeting record has no date: {m!r}")
    d["date"] = _costs._as_date(raw).isoformat()
    return d


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _flatten_decision(d: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in d.items() if k != "inputs" and not isinstance(v, (dict, list))}
    out["audit_inputs_json"] = json.dumps(d.get("inputs", {}), sort_keys=True, default=str)
    return out


def _flatten_summary(s: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in s.items() if not isinstance(v, (dict, list))}
    out["gate_G1_json"] = json.dumps(s.get("gate_G1", {}), sort_keys=True, default=str)
    return out


def _write_csv(path: str, rows: Sequence[Dict[str, Any]]) -> None:
    if not rows:
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write("")
        return
    fields: List[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _csv_value(r.get(k)) for k in fields})


def _csv_value(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, default=str)
    return v


def _write_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True, default=str)


def _code_hashes() -> Dict[str, str]:
    out = {}
    for name in ("engine.py", "costs.py"):
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
        try:
            with open(p, "rb") as f:
                out[name] = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            out[name] = "unavailable"
    return out


def _counts(universe: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    c: Dict[str, int] = {}
    for r in universe:
        c[r["status"]] = c.get(r["status"], 0) + 1
    return c


def _median(xs: Sequence[float]) -> Optional[float]:
    v = sorted(x for x in xs if x is not None)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def _max_opt(xs: Sequence[Optional[float]]) -> Optional[float]:
    v = [abs(x) for x in xs if x is not None]
    return max(v) if v else None


# ---------------------------------------------------------------------------
# Self-check: identities only, no data required
# ---------------------------------------------------------------------------


def self_check() -> Dict[str, Any]:
    """Verify the engine's own algebra. Touches no data and no panel.

    1. Replication is state-independent: P&L is N*(q-a) in both states.
    2. ZQ crossing cost per Kalshi contract is exactly 1.00 cent / w per leg.
    3. A balanced calendar spread's span is D*(1 - w_front) and its baseline is
       zero, so the EFFR level never enters.
    """
    out: Dict[str, Any] = {}
    c = CostAssumptions()

    # 1. state independence, hike outright, w = 22/31
    w, D, R, a = 22.0 / 31.0, 0.25, 4.33, 0.30
    S0, S1 = 100.0 - R, 100.0 - R - D * w
    h = S0 - S1
    N = hedge_ratio(h)
    F = S0 - 0.40 * h  # q = 0.40
    q = q_cme(F, S0, S1)
    pnl_target = N * (1.0 - a) + 1 * ZQ_POINT_MULTIPLIER * (S1 - F)
    pnl_base = -N * a + 1 * ZQ_POINT_MULTIPLIER * (S0 - F)
    out["state_independent"] = abs(pnl_target - pnl_base) < 1e-6
    out["equals_N_times_edge"] = abs(pnl_base - N * (q - a)) < 1e-6

    # 2. ZQ cost identity, 1 cent per w
    for wt in (1.0, 22.0 / 31.0, 3.0 / 31.0):
        got = _costs.zq_cost_per_kalshi_contract(D * wt, c, legs=1)
        out[f"zq_cost_per_contract_w={wt:.4f}"] = {
            "usd": got, "expected_cents_1_over_w": 1.0 / wt, "ok": abs(got * 100.0 - 1.0 / wt) < 1e-9
        }

    # 3. Oct/Nov spread for a 2026-10-28 decision: w_Oct = 3/31, w_Nov = 1
    spec = InstrumentSpec(
        kind="spread",
        legs=(("2026-10", 1.0), ("2026-11", -1.0)),
        w_by_month={"2026-10": 3.0 / 31.0, "2026-11": 1.0},
        selection_rule="meeting-month / next-month calendar spread",
        selection_inputs={"meeting_date": "2026-10-28", "days_in_month": 31, "w_by_month": True},
    )
    V_base, V_target, h_s = state_values(spec, 0.0, D)
    out["spread_is_balanced"] = spec.is_balanced
    out["spread_baseline_is_zero"] = V_base == 0.0
    out["spread_span_matches_D_times_1_minus_3_31"] = abs(abs(h_s) - D * (1.0 - 3.0 / 31.0)) < 1e-12
    out["spread_sells_the_instrument_on_a_hike"] = (1 if h_s > 0 else -1) == -1

    # 4. announcement jump reproduces the 2026-07-29 observation
    k_h = D * 1.0
    base_jump, _ = announcement_jump(k_h, 0.24632)
    out["jump_2026_07_29_bp"] = base_jump * 100.0  # price points -> bp

    out["all_ok"] = all(
        v is True for k, v in out.items()
        if isinstance(v, bool)
    ) and all(v["ok"] for k, v in out.items() if isinstance(v, dict) and "ok" in v)
    return out
