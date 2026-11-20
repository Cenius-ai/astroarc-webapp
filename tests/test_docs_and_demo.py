"""The shipped walkthrough and the documentation must match the built tool."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_demo_script_runs_end_to_end(tmp_path: Path) -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"ASTROARC_DATA", "ASTROARC_PASSPHRASE", "NO_COLOR"}
    }
    env["ASTROARC_DEMO_DIR"] = str(tmp_path / "demo-data")

    result = subprocess.run(
        ["bash", "demo.sh"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        stdin=subprocess.DEVNULL,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "No showers yet" in result.stdout
    assert "Perseids" in result.stdout
    assert "Peak meteor count per hour" in result.stdout
    assert "refused, exit 2" in result.stdout, "the demo shows a rejected write"
    assert (tmp_path / "demo-data" / "store.json").exists()
    assert "store.json" in result.stdout


def test_readme_documents_setup_run_and_the_demo_credential() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "## Demo accounts" in readme
    assert "cenius" in readme
    assert "bash demo.sh" in readme
    assert "astroarc --help" in readme
    assert "pytest" in readme


def test_install_script_installs_then_exits() -> None:
    script = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "set -euo pipefail" in script
    assert 'cd "$(dirname "$0")"' in script
    assert "pip install" in script
    assert "sudo" not in script
    for forbidden in ("apt-get", "apk ", "yum ", "systemctl", "brew "):
        assert forbidden not in script
    assert "astroarc.py" in script  # a cheap import self-check before exiting
