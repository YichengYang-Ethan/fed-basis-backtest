#!/usr/bin/env python3
"""Parse all proposed Python modules without importing providers or creating caches."""
import ast
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from publication_audit import ROOT, proposed_files

files = [path for path in proposed_files(ROOT) if path.suffix == ".py"]
for path in files:
    ast.parse(path.read_text(encoding="utf-8"), filename=path.relative_to(ROOT).as_posix())
print(f"Syntax checked {len(files)} Python modules; no provider code was imported.")
