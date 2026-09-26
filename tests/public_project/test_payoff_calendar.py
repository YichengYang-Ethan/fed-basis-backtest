"""Independent calendar enumeration and bounded-digital tail regressions."""
from datetime import date, timedelta
from fractions import Fraction
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("public_payoff_model", ROOT / "research/depth_replay/model/payoff_model.py")
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


def enumerated_mean(month, effective, before, after):
    day = date.fromisoformat(month + "-01")
    values = []
    while day.isoformat().startswith(month):
        values.append(after if day >= effective else before)
        day += timedelta(days=1)
    return sum(values, Fraction(0)) / len(values)


class PayoffCalendarTests(unittest.TestCase):
    def test_month_end_front_is_not_degenerate(self):
        # Announcement on April 30, effective May 1: the April/May spread has
        # full exposure even though April contains zero new-rate days.
        loads = model.calendar_loadings("2030-04", "2030-05", {"target": "2030-05-01"})
        self.assertEqual(loads["target"], 1)
        self.assertEqual(model.monthly_weight("2030-04", "2030-05-01"), 0)

    def test_model_loading_matches_calendar_day_settlement(self):
        for effective in [date(2032, 2, 1), date(2032, 2, 15), date(2032, 2, 29), date(2032, 3, 1)]:
            # Independent daily settlement enumeration includes leap day.
            before, move = Fraction(4), Fraction(1, 4)
            near = 100 - enumerated_mean("2032-02", effective, before, before + move)
            far = 100 - enumerated_mean("2032-03", effective, before, before + move)
            loading = model.calendar_loadings("2032-02", "2032-03", {"target": effective.isoformat()})["target"]
            self.assertEqual((near - far) * 100, loading * 25)

    def test_two_state_match_does_not_remove_50bp_tail(self):
        cash = dict(futures_sign=-1, lots=4, multiplier=Fraction(4167), entry_bp=Fraction(10),
                    digital_sign=1, quantity=4167, premium=Fraction(35, 100), digital_fees=0, futures_cost=0)
        hold = model.cash_flow(**cash, terminal_bp=0, payout=0)["net_pnl_usd"]
        hike25 = model.cash_flow(**cash, terminal_bp=25, payout=1)["net_pnl_usd"]
        hike50 = model.cash_flow(**cash, terminal_bp=50, payout=1)["net_pnl_usd"]
        self.assertEqual(hold, hike25)
        self.assertEqual(hike50 - hike25, -4167)
        self.assertLess(hike50, 0)


if __name__ == "__main__":
    unittest.main()
