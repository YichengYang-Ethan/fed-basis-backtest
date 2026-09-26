"""Reconcile every chart point to its bundled public-result source."""
from collections import Counter
import csv
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "research" / "depth_replay" / "data"
FIGURES = ROOT / "reports" / "figures"


def read(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


class FigureSourceTests(unittest.TestCase):
    def test_trade_chart_matches_the_same_account_book_and_scenarios(self):
        source = read(DATA / "trade_results.csv")
        old = {r["meeting_date"]: r for r in source
               if r["book"] == "all_signals_asof" and r["scenario"] == "old_proxy__maintenance950"}
        new = {r["meeting_date"]: r for r in source
               if r["book"] == "all_signals_asof" and r["scenario"] == "book_replace_2c__maintenance950"}
        chart = read(FIGURES / "02_trade_returns.csv")
        dates = [r["meeting_date"] for r in chart]
        self.assertEqual(dates, sorted(old))
        self.assertEqual(set(dates), set(new))
        for row in chart:
            meeting = row["meeting_date"]
            # Public result percentages have six decimal places; the figure CSV
            # retains original precision, not a different denominator or cohort.
            self.assertAlmostEqual(float(row["original_roi_pct"]), float(old[meeting]["realized_full_funding_roi_pct"]), delta=.00000051)
            self.assertAlmostEqual(float(row["updated_roi_pct"]), float(new[meeting]["realized_full_funding_roi_pct"]), delta=.00000051)
            self.assertEqual(row["new_pm_book_available"].lower(), new[meeting]["new_pm_book_available"].lower())

    def test_depth_chart_matches_all_five_included_book_diagnostics(self):
        source = {r["meeting_date"]: r for r in read(DATA / "pm_cost_impact.csv")}
        chart = read(FIGURES / "03_pm_depth_cost.csv")
        self.assertEqual({r["meeting_date"] for r in chart}, set(source))
        self.assertEqual(len(chart), len(source))
        field = "original_size_slippage_from_mid_cents"
        for row in chart:
            self.assertAlmostEqual(float(row[field]), float(source[row["meeting_date"]][field]), delta=.00000051)

    def test_coverage_chart_reconciles_every_classification(self):
        mapping = {
            "SIGNAL_KNOWN_TERMINAL": "Admissible for current design",
            "VALID_DATA_NO_4C_3MIN_SIGNAL": "Admissible for current design",
            "SIGNAL_TERMINAL_PENDING": "Admissible for current design",
            "NONEXHAUSTIVE_LISTED_POINT_STATES": "Non-exhaustive point-state ladder",
            "NO_LOCAL_PREDECISION_HISTORY": "No local predecision history",
            "RULE_WORDING_OR_MAPPING_AMBIGUITY": "Ambiguous wording / mapping",
            "CORE_RULE_CONFLICT": "Conflicting core rules",
            "NO_ISOLATED_CME_PAIR": "No isolated ZQ pair",
        }
        source = Counter(mapping[r["primary_exclusion_code"]] for r in read(DATA / "coverage_historical38.csv"))
        chart = read(FIGURES / "01_meeting_coverage.csv")
        self.assertEqual(len(chart), len({r["category"] for r in chart}))
        self.assertEqual({r["category"]: int(r["meetings"]) for r in chart}, dict(source))


if __name__ == "__main__":
    unittest.main()
