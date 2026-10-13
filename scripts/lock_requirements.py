#!/usr/bin/env python3
"""Regenerate requirements-ci.txt — exact versions CI installs.

Resolves requirements-dev.txt (which includes requirements.txt) for the CI
environment — Linux x86_64, CPython 3.12 — regardless of the machine it runs
on, and writes every resolved package, transitive ones included, as `==` pins.

requirements.txt / requirements-dev.txt stay the human-edited source with
version ranges; this file only freezes what CI tests against, so a new upstream
release can't break unrelated PRs. Dependabot bumps the pins weekly.

Usage:
    python scripts/lock_requirements.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = "requirements-dev.txt"
OUTPUT = ROOT / "requirements-ci.txt"
PLATFORM = "manylinux2014_x86_64"
PYTHON_VERSION = "3.12"


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        subprocess.run(
            [
                sys.executable, "-m", "pip", "install", "--quiet", "--dry-run", "--ignore-installed",
                "--only-binary=:all:", f"--platform={PLATFORM}", f"--python-version={PYTHON_VERSION}",
                "--report", str(report), "-r", SOURCE,
            ],
            cwd=ROOT,
            check=True,
        )
        resolved = json.loads(report.read_text())["install"]

    pins = sorted(
        (item["metadata"]["name"].lower(), item["metadata"]["version"]) for item in resolved
    )
    header = (
        "# Exact versions installed by CI (Linux x86_64, Python 3.12).\n"
        f"# Generated from {SOURCE} by scripts/lock_requirements.py — do not edit by hand,\n"
        "# change requirements.txt / requirements-dev.txt and rerun the script.\n"
    )
    OUTPUT.write_text(header + "".join(f"{name}=={version}\n" for name, version in pins))
    print(f"Wrote {len(pins)} pins to {OUTPUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
