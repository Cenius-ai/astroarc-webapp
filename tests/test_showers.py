"""F1 - record meteor shower peak dates and radiant constellations."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import add_shower, run


def test_empty_registry_invites_the_first_shower_and_creates_the_store(store: Path) -> None:
    result = run(["showers"], store)

    assert result.returncode == 0
    assert "No showers yet" in result.stdout
    assert "astroarc shower add --name Perseids" in result.stdout
    assert store.exists()
    document = json.loads(store.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["showers"] == []


def test_added_shower_is_listed_with_constellation_date_and_year(store: Path) -> None:
    add_shower(store, "Perseids", "Perseus", "2024-08-12")

    result = run(["showers"], store)

    assert result.returncode == 0
    row = next(line for line in result.stdout.splitlines() if "Perseids" in line)
    assert "Perseus" in row
    assert "2024-08-12" in row
    assert "2024" in row
    assert "Shower" in result.stdout and "Constellation" in result.stdout


def test_showers_survive_a_restart(store: Path) -> None:
    add_shower(store, "Geminids", "[redacted]", "2024-12-14")

    second_run = run(["showers"], store)

    assert second_run.returncode == 0
    assert "Geminids" in second_run.stdout


def test_newest_peak_is_listed_first(store: Path) -> None:
    add_shower(store, "Perseids", "Perseus", "2023-08-12")
    add_shower(store, "Geminids", "[redacted]", "2024-12-14")

    result = run(["showers"], store)

    rows = [line for line in result.stdout.splitlines() if line.startswith("\u2502")][1:]
    assert "Geminids" in rows[0]
    assert "Perseids" in rows[1]


@pytest.mark.parametrize("bad_date", ["2024-13-40", "2024-02-30", "2024-00-10"])
def test_impossible_calendar_dates_are_rejected(store: Path, bad_date: str) -> None:
    result = run(
        ["shower", "add", "--name", "Perseids", "--constellation", "Perseus", "--peak", bad_date],
        store,
    )

    assert result.returncode != 0
    assert "peak date" in result.stderr
    assert "2024-08-12" in result.stderr  # the accepted format is shown
    assert "No showers yet" in run(["showers"], store).stdout


@pytest.mark.parametrize("bad_date", ["2024-8-12", "12-08-2024", "2024/08/12", "August 12"])
def test_malformed_dates_are_rejected(store: Path, bad_date: str) -> None:
    result = run(
        ["shower", "add", "--name", "Perseids", "--constellation", "Perseus", "--peak", bad_date],
        store,
    )

    assert result.returncode == 2
    assert "YYYY-MM-DD" in result.stderr
    assert "nothing was written" in result.stderr


def test_duplicate_name_and_peak_reports_it_already_exists(store: Path) -> None:
    add_shower(store, "Perseids", "Perseus", "2024-08-12")

    duplicate = run(
        [
            "shower",
            "add",
            "--name",
            "Perseids",
            "--constellation",
            "Perseus",
            "--peak",
            "2024-08-12",
        ],
        store,
    )

    assert duplicate.returncode != 0
    assert "already recorded" in duplicate.stderr
    assert run(["showers"], store).stdout.count("Perseids") == 1


def test_duplicate_detection_ignores_case_and_padding(store: Path) -> None:
    add_shower(store, "Perseids", "Perseus", "2024-08-12")

    duplicate = run(
        [
            "shower",
            "add",
            "--name",
            "  perseids ",
            "--constellation",
            "Perseus",
            "--peak",
            "2024-08-12",
        ],
        store,
    )

    assert duplicate.returncode != 0
    assert "already recorded" in duplicate.stderr


def test_same_name_with_a_different_peak_date_is_a_new_record(store: Path) -> None:
    add_shower(store, "Perseids", "Perseus", "2024-08-12")

    second = run(
        [
            "shower",
            "add",
            "--name",
            "Perseids",
            "--constellation",
            "Perseus",
            "--peak",
            "2025-08-12",
        ],
        store,
    )

    assert second.returncode == 0, second.stderr
    listed = run(["showers"], store).stdout
    assert listed.count("Perseids") == 2


def test_blank_name_is_rejected(store: Path) -> None:
    result = run(
        ["shower", "add", "--name", "   ", "--constellation", "Perseus", "--peak", "2024-08-12"],
        store,
    )

    assert result.returncode == 2
    assert "must not be empty" in result.stderr
    assert "No showers yet" in run(["showers"], store).stdout
