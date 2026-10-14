"""The app version has a single source — the VERSION file — and is never behind the latest release tag."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from config import load_version, settings

ROOT = Path(__file__).resolve().parent.parent


def _parse(version: str) -> tuple[int, ...]:
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"not a X.Y.Z version: {version!r}"
    return tuple(int(part) for part in version.split("."))


def test_settings_version_comes_from_version_file():
    assert settings["soft_version"] == (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert settings["soft_version"] == load_version()


def test_version_is_not_behind_latest_tag():
    try:
        tags = subprocess.run(
            ["git", "tag", "--list", "v*"], cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available")
    releases = [_parse(t[1:]) for t in tags if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
    if not releases:
        pytest.skip("no release tags in this checkout")
    assert _parse(load_version()) >= max(releases), "bump VERSION: it is lower than the latest release tag"
