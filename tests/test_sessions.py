"""F2 - observation sessions with hourly counts, totals and peaks."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import add_shower, log_hour, run, start_session


def test_session_start_rejects_an_impossible_date(store: Path) -> None:
    add_shower(store, "Perseids")

    result = run(["session", "start", "--shower", "Perseids", "--date", "2024-13-40"], store)

    assert result.returncode == 2
    assert "session date" in result.stderr
    assert "No sessions yet" in run(["sessions"], store).stdout


def test_session_start_rejects_an_unknown_shower(store: Path) -> None:
    add_shower(store, "Perseids")

    result = run(["session", "start", "--shower", "Draconids", "--date", "2024-10-08"], store)

    assert result.returncode == 2
    assert "no shower matching" in result.stderr
    assert "Perseids" in result.stderr  # the recorded showers are listed
    assert "No sessions yet" in run(["sessions"], store).stdout


def test_session_start_prints_the_next_command(store: Path) -> None:
    add_shower(store, "Perseids")

    session_id = start_session(store, "Perseids", "2023-08-12")

    result = run(["sessions"], store)
    assert result.returncode == 0
    assert session_id[:8] in result.stdout
    assert "open" in result.stdout


def test_empty_session_shows_the_record_count_command(store: Path) -> None:
    add_shower(store, "Perseids")
    session_id = start_session(store, "Perseids", "2023-08-12")

    result = run(["session", "show", session_id], store)

    assert result.returncode == 0
    assert "No counts yet" in result.stdout
    assert f"astroarc session log {session_id} --hour 22 --count 7" in result.stdout


@pytest.mark.parametrize(
    ("hour", "count", "expected"),
    [
        ("22", "-3", "negative"),
        ("22", "abc", "not a whole number"),
        ("24", "5", "outside 00-23"),
        ("-1", "5", "outside 00-23"),
        ("22:00", "5", "not a whole number"),
    ],
)
def test_bad_hours_and_counts_are_rejected_without_partial_writes(
    store: Path, hour: str, count: str, expected: str
) -> None:
    add_shower(store, "Perseids")
    session_id = start_session(store, "Perseids", "2023-08-12")

    result = run(["session", "log", session_id, "--hour", hour, "--count", count], store)

    assert result.returncode == 2
    assert expected in result.stderr
    assert "nothing was written" in result.stderr
    assert "No counts yet" in run(["session", "show", session_id], store).stdout


def test_re_recording_an_hour_overwrites_instead_of_double_counting(store: Path) -> None:
    add_shower(store, "Perseids")
    session_id = start_session(store, "Perseids", "2023-08-12")

    log_hour(store, session_id, 22, 7)
    second = log_hour(store, session_id, 22, 9)

    assert "overwrote 7" in second.stdout
    view = run(["session", "show", session_id], store).stdout
    hour_rows = [line for line in view.splitlines() if line.startswith("\u2502")]
    assert len(hour_rows) == 2, hour_rows  # header + the single 22:00 row
    assert hour_rows[1].count("22:00") == 1
    assert "total 9" in view


def test_session_view_lists_hours_with_total_and_peak(store: Path) -> None:
    add_shower(store, "Perseids")
    session_id = start_session(store, "Perseids", "2023-08-12")

    log_hour(store, session_id, 22, 9)
    log_hour(store, session_id, 21, 4)

    view = run(["session", "show", session_id], store).stdout

    assert "21:00" in view and "22:00" in view
    assert "total 13" in view
    assert "peak hour 9 at 22:00" in view


def test_closed_session_refuses_further_counts(store: Path) -> None:
    add_shower(store, "Perseids")
    session_id = start_session(store, "Perseids", "2023-08-12")
    log_hour(store, session_id, 21, 4)
    log_hour(store, session_id, 22, 9)
    assert run(["session", "close", session_id], store).returncode == 0

    refused = run(["session", "log", session_id, "--hour", "23", "--count", "3"], store)

    assert refused.returncode == 2
    assert "closed" in refused.stderr
    view = run(["session", "show", session_id], store).stdout
    assert "total 13" in view
    assert "23:00" not in view


def test_closing_twice_changes_nothing(store: Path) -> None:
    add_shower(store, "Perseids")
    session_id = start_session(store, "Perseids", "2023-08-12")
    log_hour(store, session_id, 22, 9)
    run(["session", "close", session_id], store)
    before = store.read_bytes()

    again = run(["session", "close", session_id], store)

    assert again.returncode == 0
    assert "already closed" in again.stdout
    assert store.read_bytes() == before


def test_unknown_session_id_is_reported(store: Path) -> None:
    add_shower(store, "Perseids")
    start_session(store, "Perseids", "2023-08-12")

    result = run(["session", "show", "ffffffff"], store)

    assert result.returncode == 2
    assert "no session with id" in result.stderr


def test_unique_session_id_prefixes_are_accepted(store: Path) -> None:
    add_shower(store, "Perseids")
    session_id = start_session(store, "Perseids", "2023-08-12")
    log_hour(store, session_id, 22, 9)

    short = run(["session", "show", session_id[:8]], store)

    assert short.returncode == 0
    assert "total 9" in short.stdout


def test_session_state_is_visible_in_the_session_list(store: Path) -> None:
    add_shower(store, "Perseids")
    open_id = start_session(store, "Perseids", "2023-08-12")
    closed_id = start_session(store, "Perseids", "2024-08-12")
    log_hour(store, closed_id, 23, 65)
    run(["session", "close", closed_id], store)

    listing = run(["sessions"], store).stdout

    open_row = next(line for line in listing.splitlines() if open_id[:8] in line)
    closed_row = next(line for line in listing.splitlines() if closed_id[:8] in line)
    assert "open" in open_row
    assert "closed" in closed_row
    assert "65" in closed_row
