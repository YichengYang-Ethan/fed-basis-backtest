#!/usr/bin/env python3
"""Audit proposed public files and maintain a hash-bound publication manifest.

Only Git-tracked and untracked nonignored files are considered; the Git metadata
directory is never traversed. A manifest pins the reviewed file list and every
byte, including the text scan receipt for PDF documents. It is an audit aid, not
an opinion on a data vendor's redistribution license.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "PUBLICATION_MANIFEST.json"
ALLOWED_ROOTS = {"data", "harness", "results", "prereg", "docs", "reports", "research",
                 "data_pipeline", "scripts", "tests", ".github", "notebooks"}
ALLOWED_TOP = {"README.md", "README.zh-CN.md", "CHANGELOG.md", "LICENSE", "LICENSE-CODE", "LICENSE-DATA", "LICENSE.md",
               "DATA_LICENSE.md", "CITATION.cff", "CONTRIBUTING.md", "SECURITY.md",
               "Makefile", "pyproject.toml", "requirements.txt", "requirements-public.txt",
               "requirements-dev.txt", ".gitignore", ".gitattributes", ".editorconfig", MANIFEST}
ALLOWED_SUFFIXES = {".py", ".md", ".csv", ".json", ".yml", ".yaml", ".toml", ".txt",
                    ".svg", ".png", ".pdf", ".cff", ".ipynb", ".rst"}
BLOCKED_SUFFIXES = {".parquet", ".duckdb", ".dbn", ".db", ".sqlite", ".sqlite3", ".pkl",
                    ".pickle", ".gz", ".zst", ".zip", ".7z", ".tar", ".bz2", ".xz", ".pem", ".key"}
BLOCKED_PARTS = {"raw", "private", "secrets", "credentials", "node_modules", ".venv", "__pycache__"}
TEXT_SUFFIXES = ALLOWED_SUFFIXES - {".png", ".pdf"}
PATTERNS = [
    ("vendor API key", re.compile(r"\b(?:db-[A-Za-z0-9]{20,}|tlx_[A-Za-z0-9]{20,})\b")),
    ("GitHub access token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("signed URL", re.compile(r"(?i)(?:X-Amz-(?:Signature|Credential|Security-Token)|X-Goog-Signature|[?&]sig)=")),
    ("private home path", re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+/|[A-Z]:\\Users\\[^\\]+\\")),
    ("private mentor reference", re.compile(r"\b" + "C" + "aleb" + r"\b", re.I)),
    ("broker account identifier", re.compile(r"\b(?:DU|U)[0-9]{6,10}\b")),
    ("literal credential assignment", re.compile(
        r'''(?i)(?:api_key|access_token|secret_key|password)\s*[=:]\s*["'][A-Za-z0-9_+/=-]{16,}["']''')),
]
EMAIL = re.compile(r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b")
SAFE_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "users.noreply.github.com"}


def proposed_files(root: Path) -> list[Path]:
    """An explicit Git index/worktree inventory, never a recursive home scan."""
    result = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                            check=True, stdout=subprocess.PIPE)
    names = {part.decode("utf-8") for part in result.stdout.split(b"\0") if part}
    return [root / name for name in sorted(names)
            if ((root / name).exists() or (root / name).is_symlink()) and name != MANIFEST]


def path_errors(relative: str, size: int) -> list[str]:
    path = PurePosixPath(relative)
    errors = []
    if path.is_absolute() or ".." in path.parts:
        errors.append("path must remain inside repository")
    if len(path.parts) == 1:
        if relative not in ALLOWED_TOP:
            errors.append("top-level file is not in the publication allowlist")
    elif path.parts[0] not in ALLOWED_ROOTS:
        errors.append("directory is not in the publication allowlist")
    if any(part.lower() in BLOCKED_PARTS or part.lower().startswith(".env") for part in path.parts):
        errors.append("private/raw/environment directory or file is not publishable")
    if any(suffix.lower() in BLOCKED_SUFFIXES for suffix in path.suffixes):
        errors.append("raw, archive, database, or secret file extension is not publishable")
    if len(path.parts) > 1 and path.suffix.lower() not in ALLOWED_SUFFIXES:
        errors.append("file extension is not in the publication allowlist")
    if size > 25 * 1024 * 1024:
        errors.append("file exceeds the 25 MiB public-artifact limit")
    return errors


def text_errors(text: str) -> list[str]:
    errors = [label for label, expression in PATTERNS if expression.search(text)]
    if any(match.group(0).rsplit("@", 1)[1].lower() not in SAFE_EMAIL_DOMAINS for match in EMAIL.finditer(text)):
        errors.append("personal email address")
    return errors


def markdown_link_errors(text: str, source: Path, root: Path, available: set[str]) -> list[str]:
    """Check file links without fetching websites or interpreting code examples."""
    text = re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1\s*$", "", text)
    text = re.sub(r"`[^`\n]+`", "", text)
    targets = re.findall(r"!?\[[^\]\n]*\]\(\s*(<[^>]*>|[^\s)]+)(?:\s+[\"'][^\n]*?[\"'])?\s*\)", text)
    targets += re.findall(r"(?m)^\s*\[[^\]]+\]:\s*(<[^>]*>|\S+)", text)
    errors = []
    for raw in targets:
        target = raw.strip("<>")
        parts = urlsplit(target)
        if parts.scheme:
            if parts.scheme not in {"http", "https", "mailto"}:
                errors.append(f"unsupported local link scheme: {parts.scheme}")
            continue
        if target.startswith("//") or not parts.path:
            continue
        path = unquote(parts.path)
        destination = (root / path.lstrip("/")) if path.startswith("/") else source.parent / path
        try:
            relative = destination.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            errors.append(f"link escapes repository: {target}")
            continue
        if relative == MANIFEST:
            continue
        if relative not in available and not any(p.startswith(relative.rstrip("/") + "/") for p in available):
            errors.append(f"link target is not a published file: {target}")
    return errors


def pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    except ImportError:
        pass
    try:
        import fitz
        with fitz.open(path) as document:
            return "\n".join(page.get_text() for page in document)
    except ImportError:
        pass
    try:
        return subprocess.run(["pdftotext", str(path), "-"], check=True, stdout=subprocess.PIPE).stdout.decode("utf-8")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("PDF manifest creation requires pypdf, PyMuPDF, or pdftotext") from exc


def audit(root: Path, *, write_manifest: bool = False) -> dict:
    files = proposed_files(root)
    available = {path.relative_to(root).as_posix() for path in files}
    old = {}
    if not write_manifest:
        manifest_path = root / MANIFEST
        if not manifest_path.is_file():
            raise AssertionError("Publication manifest missing; run make manifest after reviewing the proposed files")
        manifest = json.loads(manifest_path.read_text())
        assert manifest.get("schema_version") == 1, "Unsupported publication manifest version"
        old = {item["path"]: item for item in manifest["files"]}
        assert set(old) == available, "Manifest file inventory differs from proposed public files; review before refreshing"
    failures = []
    rows = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            failures.append((relative, "symlinks are excluded from public artifacts"))
            continue
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        errors = path_errors(relative, len(content))
        row = {"path": relative, "bytes": len(content), "sha256": digest}
        suffix = path.suffix.lower()
        if suffix in TEXT_SUFFIXES or len(path.relative_to(root).parts) == 1:
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                errors.append("text file is not UTF-8")
            else:
                errors += text_errors(text)
                if suffix == ".md":
                    errors += markdown_link_errors(text, path, root, available)
        elif suffix == ".pdf":
            if write_manifest:
                text = pdf_text(path)
                if not text.strip():
                    errors.append("PDF has no extractable text; manual-only review is insufficient")
                errors += text_errors(text)
                row.update(pdf_text_scanned=True, pdf_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
            else:
                previous = old.get(relative, {})
                if previous.get("sha256") != digest or previous.get("pdf_text_scanned") is not True:
                    errors.append("PDF lacks an unchanged, hash-bound text-scan receipt")
                row.update({k: previous[k] for k in ("pdf_text_scanned", "pdf_text_sha256") if k in previous})
        if not write_manifest and (old[relative].get("sha256") != digest or old[relative].get("bytes") != len(content)):
            errors.append("content changed after publication review")
        failures.extend((relative, error) for error in errors)
        rows.append(row)
    if failures:
        # Do not print matched secrets or personal strings.
        for relative, reason in failures:
            print(f"FAIL {relative}: {reason}", file=sys.stderr)
        raise AssertionError(f"Publication audit failed: {len(failures)} finding(s)")
    if write_manifest:
        result = {"schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
                  "scope": "Git-tracked and proposed nonignored files; no raw-feed redistribution authorization implied",
                  "manifest_excluded_from_own_hash": True,
                  "checks": ["explicit path/type allowlist", "credential/privacy text scan", "local Markdown file links",
                             "PDF extracted-text scan bound to file hash", "SHA-256 file inventory"], "files": rows}
        (root / MANIFEST).write_text(json.dumps(result, indent=2) + "\n")
    return {"publication_audit_passed": True, "files_checked": len(rows),
            "pdf_files_checked": sum(row["path"].endswith(".pdf") for row in rows),
            "manifest_written": write_manifest, "network_calls": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-manifest", action="store_true", help="Review current files and replace the publication manifest")
    args = parser.parse_args()
    try:
        report = audit(ROOT, write_manifest=args.write_manifest)
    except (AssertionError, RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Publication audit stopped: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
