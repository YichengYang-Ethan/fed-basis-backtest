"""Regression tests that distinguish economic checks from checksum checks."""
from contextlib import contextmanager
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "research" / "depth_replay"
spec = importlib.util.spec_from_file_location("public_result_verifier", PACKAGE / "model" / "verify_results.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


@contextmanager
def altered_csv(filename, mutate):
    with tempfile.TemporaryDirectory(prefix="fomc-public-test-") as directory:
        temporary = Path(directory)
        shutil.copytree(PACKAGE / "data", temporary / "data")
        file = temporary / "data" / filename
        with file.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            columns, rows = reader.fieldnames, list(reader)
        mutate(rows)
        with file.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        # Refresh the checksum deliberately. A pass here would mean arithmetic
        # was relying on hashes rather than independently recomputing results.
        manifest_file = temporary / "data" / "source_manifest.json"
        manifest = json.loads(manifest_file.read_text())
        next(item for item in manifest["files"] if item["file"] == "data/" + filename)["sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
        manifest_file.write_text(json.dumps(manifest))
        with patch.object(verifier, "ROOT", temporary), patch.object(verifier, "DATA", temporary / "data"):
            yield


class PublicResultTests(unittest.TestCase):
    def test_bundled_projection_reconciles(self):
        result = verifier.verify()
        self.assertTrue(result["all_checks_passed"])
        self.assertEqual(result["unique_filled_meetings"], 9)
        self.assertFalse(result["raw_data_replay_performed"])

    def test_profit_corruption_fails_even_with_updated_checksum(self):
        def mutate(rows):
            rows[0]["financed_profit_usd"] = str(float(rows[0]["financed_profit_usd"]) + 1)
        with altered_csv("trade_results.csv", mutate):
            with self.assertRaisesRegex(AssertionError, "funding ROI"):
                verifier.verify()

    def test_execution_clock_cannot_move_after_selection(self):
        def mutate(rows):
            rows[0]["execution_ts"] = str(float(rows[0]["execution_ts"]) + 60)
        with altered_csv("trade_results.csv", mutate):
            with self.assertRaisesRegex(AssertionError, "fixed execution clock"):
                verifier.verify()

    def test_annualized_return_is_recomputed(self):
        def mutate(rows):
            rows[0]["calendar_cagr_pct"] = str(float(rows[0]["calendar_cagr_pct"]) + .1)
        with altered_csv("scenario_summary.csv", mutate):
            with self.assertRaisesRegex(AssertionError, "calendar CAGR"):
                verifier.verify()


if __name__ == "__main__":
    unittest.main()
