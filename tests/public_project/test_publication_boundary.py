"""Small adversarial checks for the file and text publication boundary."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("publication_audit", ROOT / "scripts/publication_audit.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class PublicationBoundaryTests(unittest.TestCase):
    def test_raw_extension_and_secret_directories_are_blocked(self):
        for name in ["data/market.parquet", "data/raw/example.csv", "reports/source.dbn.zst", "scripts/.env"]:
            self.assertTrue(audit.path_errors(name, 100), name)
        self.assertEqual(audit.path_errors("data/hedge_error.csv", 100), [])

    def test_credentials_and_home_paths_do_not_need_real_secrets(self):
        self.assertIn("vendor API key", audit.text_errors("db-" + "a" * 30))
        self.assertIn("private home path", audit.text_errors("/" + "Users" + "/researcher/private/"))
        self.assertIn("signed URL", audit.text_errors("https://example.com/data?" + "X-Amz-" + "Signature=" + "0" * 24))
        self.assertEqual(audit.text_errors("Read a local .parquet file using your own entitlement."), [])

    def test_markdown_links_cannot_point_to_omitted_files(self):
        with tempfile.TemporaryDirectory(prefix="fomc-links-") as directory:
            root = Path(directory)
            source = root / "README.md"
            known = {"docs/model.md", "README.md"}
            self.assertEqual(audit.markdown_link_errors("[model](docs/model.md)", source, root, known), [])
            self.assertTrue(audit.markdown_link_errors("[missing](data/not-shared.csv)", source, root, known))
            self.assertTrue(audit.markdown_link_errors("[outside](../private.csv)", source, root, known))


if __name__ == "__main__":
    unittest.main()
