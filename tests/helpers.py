"""Shared helpers for the AstroArc test suite: every test drives the real CLI."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "astroarc.py"

# The standard demo credential, exposed here as the passphrase of the demo store.
DEMO_PASSPHRASE = "cenius"

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

_SCRUBBED = {"ASTROARC_DATA", "ASTROARC_PASSPHRASE", "NO_COLOR"}


def clean_env(store: Path | None, passphrase: str | None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _SCRUBBED}
    if store is not None:
        env["ASTROARC_DATA"] = str(store)
    if passphrase is not None:
        env["ASTROARC_PASSPHRASE"] = passphrase
    return env


def run(
    args: list[str],
    store: Path | None = None,
    passphrase: str | None = DEMO_PASSPHRASE,
    stdin_text: str | None = None,
) -> subprocess.CompletedProcess:
    """Run `python3 astroarc.py <args>` against its own JSON store."""
    kwargs: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "timeout": 120,
        "cwd": str(ROOT),
        "env": clean_env(store, passphrase),
    }
    if stdin_text is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = stdin_text
    return subprocess.run([sys.executable, str(APP), *args], check=False, **kwargs)


def add_shower(
    store: Path,
    name: str,
    constellation: str = "Perseus",
    peak: str = "2024-08-12",
    passphrase: str | None = DEMO_PASSPHRASE,
) -> subprocess.CompletedProcess:
    result = run(
        ["shower", "add", "--name", name, "--constellation", constellation, "--peak", peak],
        store,
        passphrase,
    )
    assert result.returncode == 0, result.stderr
    return result


def start_session(store: Path, shower: str, session_date: str) -> str:
    result = run(["session", "start", "--shower", shower, "--date", session_date], store)
    assert result.returncode == 0, result.stderr
    match = UUID_RE.search(result.stdout)
    assert match is not None, result.stdout
    return match.group(0)


def log_hour(store: Path, session_id: str, hour: int, count: int) -> subprocess.CompletedProcess:
    result = run(["session", "log", session_id, "--hour", str(hour), "--count", str(count)], store)
    assert result.returncode == 0, result.stderr
    return result
