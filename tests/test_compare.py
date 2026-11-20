"""F3 - the cross-year comparison table."""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import add_shower, log_hour, run, start_session

import astroarc

EM_DASH = "\u2014"

REAL_SHOWERS = (
    "Perseids",
    "Geminids",
    "Quadrantids",
    "Lyrids",
    "Eta Aquariids",
    "Orionids",
    "Leonids",
    "Draconids",
    "Southern Delta Aquariids",
    "Alpha Capricornids",
    "Kappa Cygnids",
    "Aurigids",
    "Andromedids",
    "Ursids",
    "Monocerotids",
    "Hydrids",
    "Phoenicids",
    "Puppid-Velids",
    "Sigma Hydrids",
    "Northern Taurids",
    "Southern Taurids",
    "Arietids",
    "Sextantids",
    "June Bootids",
    "Camelopardalids",
    "April Lyrids",
    "Eta Lyrids",
    "Delta Velids",
    "Epsilon Geminids",
    "Zeta Perseids",
    "Alpha Centaurids",
    "Beta Centaurids",
    "Theta Ophiuchids",
    "Omicron Cetids",
    "Delta Cancrids",
    "Alpha Aurigids",
    "Northern Delta Aquariids",
    "Southern Iota Aquariids",
    "Northern Iota Aquariids",
    "Piscis Austrinids",
    "Alpha Hydrids",
    "Nu Draconids",
    "September Perseids",
    "October Draconids",
    "November Orionids",
    "December Phoenicids",
    "Coma Berenicids",
    "Canis Minorids",
    "Delta Leonids",
    "Psi Cassiopeiids",
)

TIMESTAMP = "2024-01-01T00:00:00+00:00"


def record_session(store: Path, shower: str, session_date: str, counts: dict[int, int]) -> None:
    session_id = start_session(store, shower, session_date)
    for hour, count in counts.items():
        log_hour(store, session_id, hour, count)
    run(["session", "close", session_id], store)


def test_compare_filters_to_one_shower_and_shows_both_years(store: Path) -> None:
    add_shower(store, "Perseids", "Perseus", "2024-08-12")
    add_shower(store, "Geminids", "[redacted]", "2024-12-14")
    record_session(store, "Perseids", "2023-08-12", {23: 40})
    record_session(store, "Perseids", "2024-08-12", {23: 65})
    record_session(store, "Geminids", "2024-12-14", {2: 72})

    result = run(["compare", "Perseids"], store)

    assert result.returncode == 0
    assert "2023" in result.stdout and "2024" in result.stdout
    row = next(line for line in result.stdout.splitlines() if "Perseids" in line)
    assert "40" in row and "65" in row
    assert "Geminids" not in result.stdout


def test_compare_without_a_filter_shows_both_showers_and_an_em_dash(store: Path) -> None:
    add_shower(store, "Perseids", "Perseus", "2024-08-12")
    add_shower(store, "Geminids", "[redacted]", "2024-12-14")
    record_session(store, "Perseids", "2023-08-12", {23: 40})
    record_session(store, "Perseids", "2024-08-12", {23: 65})
    record_session(store, "Geminids", "2024-12-14", {2: 72})

    result = run(["compare"], store)

    assert result.returncode == 0
    geminids_row = next(line for line in result.stdout.splitlines() if "Geminids" in line)
    perseids_row = next(line for line in result.stdout.splitlines() if "Perseids" in line)
    # Geminids has no 2023 session: the 2023 cell is an em dash, never a 0
    cells = [cell.strip() for cell in geminids_row.split("\u2502")[1:-1]]
    assert cells[1] == EM_DASH
    assert cells[2] == "72"
    assert EM_DASH not in perseids_row


def test_compare_uses_only_years_that_have_counts(store: Path) -> None:
    add_shower(store, "Perseids", "Perseus", "2024-08-12")
    record_session(store, "Perseids", "2023-08-12", {23: 40})
    start_session(store, "Perseids", "2022-08-12")  # opened, never logged

    result = run(["compare"], store)

    assert "2023" in result.stdout
    assert "2022" not in result.stdout


def test_compare_empty_state_invites_a_first_session(store: Path) -> None:
    add_shower(store, "Perseids", "Perseus", "2024-08-12")

    result = run(["compare"], store)

    assert result.returncode == 0
    assert "No sessions recorded yet" in result.stdout
    assert "astroarc session start --shower Perseids --date 2024-08-12" in result.stdout


def test_compare_with_no_showers_at_all_points_at_shower_add(store: Path) -> None:
    result = run(["compare"], store)

    assert result.returncode == 0
    assert "No showers yet" in result.stdout


def test_compare_rejects_an_unknown_shower_filter(store: Path) -> None:
    add_shower(store, "Perseids", "Perseus", "2024-08-12")

    result = run(["compare", "Draconids"], store)

    assert result.returncode == 2
    assert "no shower named" in result.stderr


def _write_large_store(path: Path) -> None:
    store = astroarc.Store(path=path)
    for index, name in enumerate(REAL_SHOWERS):
        shower = astroarc.Shower(
            id=str(uuid.uuid4()),
            name=name,
            constellation="Lyra",
            peak_date="2024-08-12",
            created_at=TIMESTAMP,
        )
        store.showers.append(shower)
        for year in range(2015, 2025):
            session = astroarc.ObservationSession(
                id=str(uuid.uuid4()),
                shower_id=shower.id,
                session_date=f"{year}-08-12",
                started_at=TIMESTAMP,
            )
            store.sessions.append(session)
            store.counts.append(
                astroarc.HourlyCount(
                    id=str(uuid.uuid4()),
                    session_id=session.id,
                    hour=22,
                    count=(index * 7 + year) % 120,
                    recorded_at=TIMESTAMP,
                )
            )
    store.save()


def test_compare_stays_aligned_for_50_showers_across_10_years(store: Path) -> None:
    _write_large_store(store)

    result = run(["compare"], store)

    assert result.returncode == 0, result.stderr
    table_lines = [
        line
        for line in result.stdout.splitlines()
        if line[:1] in {"\u250c", "\u2502", "\u251c", "\u2514"}
    ]
    assert len(table_lines) == 50 + 4
    assert len({len(line) for line in table_lines}) == 1, "every row shares one width"

    body = [line for line in table_lines if line.startswith("\u2502")]
    pipe_positions = {tuple(i for i, char in enumerate(line) if char == "\u2502") for line in body}
    assert len(pipe_positions) == 1, "columns line up between header and data rows"

    header = body[0]
    assert all(str(year) in header for year in range(2015, 2025))


def test_compare_renders_far_under_the_time_budget(store: Path) -> None:
    _write_large_store(store)
    loaded = astroarc.Store.load(store)

    started = time.perf_counter()
    table = astroarc.build_compare(loaded, None)
    rendered = astroarc.render_table(
        ["Shower"] + [str(year) for year in table.years],
        [
            [row.label] + [str(row.peaks.get(year, EM_DASH)) for year in table.years]
            for row in table.rows
        ],
    )
    elapsed = time.perf_counter() - started

    assert len(table.rows) == 50
    assert table.years == list(range(2015, 2025))
    assert rendered.count("\n") > 50
    assert elapsed < 0.2, f"comparison took {elapsed * 1000:.1f} ms"
