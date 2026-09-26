#!/usr/bin/env python3
"""Synthetic calendar-weighted FOMC relative-value payoff model (stdlib only).

Run all examples:       python3 payoff_model.py
Run assertions too:     python3 payoff_model.py --self-test
Select one example:     python3 payoff_model.py --example matched_two_state
Use another JSON file:  python3 payoff_model.py --examples-file scenarios.json

No market data, network, fitting, signal selection, or trading functionality.
All arithmetic is rational until JSON display conversion. Dates and prices in
the companion examples are invented teaching inputs, not historical observations.

Mathematical conventions:
  ZQ futures price = 100 - delivery-month arithmetic-average EFFR (percent).
  w_mj = fraction of calendar days in month m on/after decision j's effective date.
  S = F_near - F_far; L_j = w_far,j - w_near,j.
  Terminal S, in basis-point units, = offset_bp + sum(L_j * decision_change_bp_j).
  Long spread means BUY near / SELL far. Its sign is +1; short is -1.
  Futures P&L = sign * lots * multiplier * (terminal_S_bp - entry_S_bp) / 100.
  One index-price point is 100 bp and is worth $4,167 by default.
  A digital pays either $0 or $1 per contract; scenario payouts are explicit.
  The premium is paid for a long digital and received for a short digital.
  A constant initial rate cancels between months. Unmodeled monthly drift or
  basis belongs in the explicit offset; changes are assumed to persist.
  For multiple spread positions i, other-decision exposure is proportional to
  sum_i(sign_i * lots_i * multiplier_i * L_ij). It cancels for arbitrary,
  independently varying other decisions only if that sum is zero for each j.
  This implementation evaluates one calendar spread and one digital per case.
"""
from __future__ import annotations

import argparse
import calendar
from datetime import date
from decimal import Decimal
from fractions import Fraction
import json
import math
from pathlib import Path


DEFAULT_MULTIPLIER = Fraction(4167)
SIDE = {'long': 1, 'short': -1}


def number(value, name='number'):
    """Read the decimal value supplied by the user; reject nonfinite inputs."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal, Fraction)):
        raise ValueError(f'{name} must be a finite numeric value')
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f'{name} must be finite')
    try:
        return value if isinstance(value, Fraction) else Fraction(str(value))
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError(f'{name} must be a finite numeric value') from exc


def integer(value, name, minimum=0):
    result = number(value, name)
    if result.denominator != 1 or result < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return int(result)


def month_start(month):
    if not isinstance(month, str) or len(month) != 7:
        raise ValueError('Delivery month must have YYYY-MM format')
    return date.fromisoformat(month + '-01')


def monthly_weight(delivery_month, effective_date):
    """Exact calendar-day weight; the effective date itself uses the new rate."""
    first = month_start(delivery_month)
    effective = date.fromisoformat(effective_date)
    days = calendar.monthrange(first.year, first.month)[1]
    if effective <= first:
        return Fraction(1)
    if effective.year != first.year or effective.month != first.month:
        return Fraction(0)
    return Fraction(days - effective.day + 1, days)


def calendar_loadings(near_month, far_month, decisions):
    """Return L_j for an earlier-minus-later delivery-month calendar spread."""
    if month_start(near_month) >= month_start(far_month):
        raise ValueError('near_month must precede far_month')
    if not decisions:
        raise ValueError('At least one decision effective date is required')
    return {name: monthly_weight(far_month, effective) - monthly_weight(near_month, effective)
            for name, effective in decisions.items()}


def round_quantity(exact_quantity, policy):
    """Round a nonnegative theoretical contract count; nearest uses half-up."""
    if exact_quantity < 0:
        raise ValueError('Cannot round a negative absolute contract quantity')
    floor = exact_quantity.numerator // exact_quantity.denominator
    if policy == 'floor':
        return floor
    if policy == 'ceil':
        return floor + int(exact_quantity != floor)
    if policy == 'nearest':
        return floor + int(exact_quantity - floor >= Fraction(1, 2))
    raise ValueError('rounding must be floor, ceil, or nearest')


def digital_fee(quantity, premium_usd, config):
    """Entry fee: fixed + per-contract*Q + k*Q*p*(1-p), optionally ceil to cents.

    Here p is the dollar premium for a $1 face-value digital. This configurable
    formula is a teaching cost model, not a statement of any venue's fee rules.
    The fee is a nonnegative cash cost for either a long or a short position.
    """
    fixed = number(config.get('fixed_usd', 0), 'fixed fee')
    linear = number(config.get('per_contract_usd', 0), 'per-contract fee')
    coefficient = number(config.get('quadratic_coefficient', 0), 'quadratic fee coefficient')
    if min(fixed, linear, coefficient) < 0:
        raise ValueError('Fee components must be nonnegative')
    rule = config.get('rounding', 'none')
    if rule not in ('none', 'ceil_cent'):
        raise ValueError('Fee rounding must be none or ceil_cent')
    fee = fixed + linear * quantity + coefficient * quantity * premium_usd * (1 - premium_usd)
    if rule == 'ceil_cent':
        cents = fee * 100
        fee = Fraction(-(-cents.numerator // cents.denominator), 100)
    return fee


def cash_flow(*, futures_sign, lots, multiplier, entry_bp, terminal_bp,
              digital_sign, quantity, premium, payout, digital_fees, futures_cost):
    futures_pnl = futures_sign * lots * multiplier * (terminal_bp - entry_bp) / 100
    premium_cash_flow = -digital_sign * quantity * premium
    payout_cash_flow = digital_sign * quantity * payout
    net = futures_pnl + premium_cash_flow + payout_cash_flow - digital_fees - futures_cost
    return dict(futures_pnl_usd=futures_pnl, digital_premium_cash_flow_usd=premium_cash_flow,
                digital_settlement_cash_flow_usd=payout_cash_flow,
                digital_fee_cost_usd=digital_fees, futures_explicit_cost_usd=futures_cost,
                net_pnl_usd=net)


def evaluate(spec):
    """Evaluate one synthetic input dictionary and return JSON-ready results."""
    spread, target, digital = spec['spread'], spec['two_state_target'], spec['digital']
    decisions = spec['decision_effective_dates']
    loads = calendar_loadings(spread['near_month'], spread['far_month'], decisions)
    target_name = target['decision']
    if target_name not in loads or loads[target_name] == 0:
        raise ValueError('The target decision must have nonzero calendar-spread loading')
    try:
        futures_sign = SIDE[spread['side']]
        digital_sign = SIDE[digital['side']]
    except KeyError as exc:
        raise ValueError('Position side must be long or short') from exc
    lots = integer(spread.get('lots', 1), 'futures lots', 1)
    multiplier = number(spread.get('multiplier_usd_per_index_point', DEFAULT_MULTIPLIER), 'multiplier')
    if multiplier <= 0:
        raise ValueError('Futures multiplier must be positive')
    entry_bp = number(spread['entry_spread_bp'], 'entry spread bp')
    low = number(target['lower_change_bp'])
    high = number(target['upper_change_bp'])
    payout_low = number(target.get('digital_payout_lower_usd', 0))
    payout_high = number(target.get('digital_payout_upper_usd', 1))
    if high <= low or payout_low not in (0, 1) or payout_high not in (0, 1) or payout_low == payout_high:
        raise ValueError('Two distinct ordered decisions and distinct $0/$1 digital payouts are required')
    premium = number(digital['premium_usd_per_contract'], 'digital premium')
    if not 0 <= premium <= 1:
        raise ValueError('The premium for this $1 face-value digital must lie in [0, 1]')
    signed_required = -futures_sign * lots * multiplier * loads[target_name] * (high - low) / (100 * (payout_high - payout_low))
    theoretical = abs(signed_required)
    direction_matches = digital_sign * signed_required > 0
    if digital.get('quantity') is None:
        if not direction_matches:
            raise ValueError('Digital side is incompatible with the automatic two-state hedge')
        quantity = round_quantity(theoretical, digital.get('quantity_rounding', 'nearest'))
    else:
        quantity = integer(digital['quantity'], 'digital quantity')
    fee = digital_fee(quantity, premium, digital.get('fee', {}))
    futures_cost = number(spread.get('explicit_cost_usd_per_lot', 0)) * lots
    if futures_cost < 0:
        raise ValueError('Explicit futures costs must be nonnegative')
    endpoint_residual = (futures_sign * lots * multiplier * loads[target_name] * (high - low) / 100
                         + digital_sign * quantity * (payout_high - payout_low))
    load_report = {name: dict(effective_date=decisions[name],
        near_month_weight_exact=str(monthly_weight(spread['near_month'], decisions[name])),
        far_month_weight_exact=str(monthly_weight(spread['far_month'], decisions[name])),
        spread_loading_exact=str(weight), spread_loading=float(weight),
        futures_pnl_usd_per_1bp_change=float(futures_sign * lots * multiplier * weight / 100))
        for name, weight in loads.items()}
    scenarios = []
    for scenario in spec['scenarios']:
        changes = scenario['decision_changes_bp']
        if set(changes) != set(loads):
            raise ValueError('Each scenario must explicitly supply every decision change, with no extra names')
        changes = {name: number(value) for name, value in changes.items()}
        payout = number(scenario['digital_payout_usd'])
        if payout not in (0, 1):
            raise ValueError('Scenario digital payouts must be $0 or $1')
        if changes[target_name] in (low, high):
            expected = payout_low if changes[target_name] == low else payout_high
            if payout != expected:
                raise ValueError('Scenario payout conflicts with the specified two-state contract')
        offset = number(scenario.get('terminal_offset_bp', 0))
        terminal = offset + sum((loads[name] * change for name, change in changes.items()), Fraction(0))
        cash = cash_flow(futures_sign=futures_sign, lots=lots, multiplier=multiplier,
            entry_bp=entry_bp, terminal_bp=terminal, digital_sign=digital_sign, quantity=quantity,
            premium=premium, payout=payout, digital_fees=fee, futures_cost=futures_cost)
        scenarios.append(dict(name=scenario['name'], decision_changes_bp={k: float(v) for k, v in changes.items()},
            digital_payout_usd_per_contract=float(payout), terminal_offset_bp=float(offset),
            terminal_spread_bp=float(terminal), terminal_spread_index_points=float(terminal / 100),
            outside_assumed_two_state_support=changes[target_name] not in (low, high),
            **{key: float(value) for key, value in cash.items()}))
    return dict(name=spec['name'], description=spec.get('description', ''), synthetic=True,
        calendar_loadings=load_report,
        positions=dict(futures_side=spread['side'], futures_position_sign=futures_sign,
            futures_lots=lots, multiplier_usd_per_index_point=float(multiplier),
            entry_spread_bp=float(entry_bp), entry_spread_index_points=float(entry_bp / 100),
            digital_side=digital['side'], digital_position_sign=digital_sign, digital_quantity=quantity,
            digital_premium_usd_per_contract=float(premium)),
        hedge_diagnostics=dict(theoretical_signed_digital_quantity_exact=str(signed_required),
            theoretical_absolute_digital_quantity_exact=str(theoretical),
            theoretical_absolute_digital_quantity=float(theoretical),
            integer_rounding_difference_contracts=float(quantity - theoretical),
            direction_matches_two_state_hedge=direction_matches,
            upper_minus_lower_net_pnl_usd=float(endpoint_residual),
            exact_two_state_endpoint_cancellation=endpoint_residual == 0,
            independently_varying_other_decisions_cancel=all(weight == 0 for name, weight in loads.items() if name != target_name),
            uncancelled_other_decisions=[name for name, weight in loads.items() if name != target_name and weight != 0],
            cancellation_condition='Target endpoint residual must be zero; every independently varying other decision must have zero loading or its own hedge; terminal offset must be fixed.'),
        scenarios=scenarios)


def self_test():
    """Analytical invariants; tests use no files, market data, or API calls."""
    assert monthly_weight('2032-02', '2032-02-29') == Fraction(1, 29)
    assert monthly_weight('2030-03', '2030-03-01') == 1
    assert monthly_weight('2030-02', '2030-03-01') == 0
    assert calendar_loadings('2030-04', '2030-05', {'target': '2030-04-16'})['target'] == Fraction(1, 2)
    clean = calendar_loadings('2030-04', '2030-05', {'prior': '2030-03-01', 'later': '2030-06-01'})
    assert clean == {'prior': 0, 'later': 0}
    dirty = calendar_loadings('2030-04', '2030-05', {'other': '2030-05-16'})
    assert dirty['other'] == Fraction(16, 31)
    exact_q = DEFAULT_MULTIPLIER * Fraction(25, 100)
    assert exact_q == Fraction(4167, 4) == Fraction('1041.75')
    for policy, expected in [('floor', 1041), ('ceil', 1042), ('nearest', 1042)]:
        assert round_quantity(exact_q, policy) == expected
    def payoff(change, payout, q, futures_sign=-1, digital_sign=1):
        return cash_flow(futures_sign=futures_sign, lots=4, multiplier=DEFAULT_MULTIPLIER,
            entry_bp=Fraction(10), terminal_bp=Fraction(change), digital_sign=digital_sign,
            quantity=q, premium=Fraction('0.35'), payout=Fraction(payout),
            digital_fees=Fraction(2), futures_cost=Fraction(8))['net_pnl_usd']
    assert payoff(0, 0, 4167) == payoff(25, 1, 4167)
    assert payoff(50, 1, 4167) - payoff(25, 1, 4167) == -4167
    # Reversing both economic legs reverses pre-cost P&L, not fees.
    assert payoff(0, 0, 4167) + payoff(0, 0, 4167, 1, -1) == -20
    rounded_difference = -DEFAULT_MULTIPLIER * Fraction(25, 100) + 1042
    assert rounded_difference == Fraction(1, 4)
    assert digital_fee(10, Fraction('0.2'), {'quadratic_coefficient': '.05'}) == Fraction('.08')
    assert digital_fee(1, Fraction('.5'), {'quadratic_coefficient': '.05', 'rounding': 'ceil_cent'}) == Fraction('.02')
    return dict(passed=True, coverage=['calendar day counts and leap day', 'near-minus-far signs and units',
        'other-decision cancellation and contamination', 'two-state endpoint equality',
        'bounded digital tail mismatch', 'integer rounding residual', 'reversed sides with explicit costs',
        'configurable fee and explicit cent rounding'])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--examples-file', type=Path, default=Path(__file__).with_name('synthetic_examples.json'))
    parser.add_argument('--example', help='Evaluate only the example with this name')
    parser.add_argument('--self-test', action='store_true', help='Run analytical assertions before evaluating examples')
    args = parser.parse_args()
    try:
        tests = self_test() if args.self_test else None
        source = json.loads(args.examples_file.read_text())
        examples = source['examples']
        if args.example:
            examples = [example for example in examples if example['name'] == args.example]
            if not examples:
                raise ValueError('Unknown example name')
        results = [evaluate(example) for example in examples]
    except (ValueError, KeyError, TypeError, OSError) as exc:
        # Avoid emitting local file paths in portable teaching output.
        print(json.dumps({'error': 'Could not evaluate the supplied example specification', 'error_type': type(exc).__name__}))
        return 2
    output = dict(model='Synthetic FOMC calendar-spread and binary-digital payoff model',
        units={'rates': 'basis points', 'spread_entry_and_terminal': 'basis points; divide by 100 for index-price points',
               'cash': 'USD', 'digital_face_value': '$1 per contract', 'position_sign': '+1 long, -1 short'},
        default_25bp_hedge='One spread lot requires 1041.75 * abs(target loading) digital contracts before integer rounding.',
        assumptions=['EFFR changes on the supplied effective date and by the stated decision amount.',
            'Effective dates, delivery months, position sizes, premiums and costs are fixed inputs.',
            'Digital settlement payouts outside the two assumed states are supplied explicitly, not inferred.',
            'The terminal offset captures only a scenario supplied residual; it is not estimated.',
            'Futures settle to the stated monthly average; both instruments are held to their respective settlements.'],
        limitations=['Two-state matching is conditional; a bounded digital cannot hedge an unrestricted linear rate payoff.',
            'No execution model, bid/ask depth, leg risk, margin, daily variation cash flows, collateral, funding or annualization.',
            'A short digital may require collateral and may not be available at a particular venue.',
            'Fees are a configurable scenario, not verified venue fee schedules; contract-specific rules are not modeled.',
            'Exact rational internal arithmetic is converted to JSON numbers for display; scenario state coverage is not a probability model.'],
        examples=results)
    if tests is not None:
        output['self_test'] = tests
    print(json.dumps(output, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
