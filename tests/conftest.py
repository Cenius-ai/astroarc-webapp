"""Fixtures shared by the AstroArc test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    """A JSON store path inside the test's own temporary directory."""
    return tmp_path / "store.json"
