"""F4/F5 - the atomic JSON store and the local passphrase gate."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import DEMO_PASSPHRASE, add_shower, log_hour, run, start_session


def test_store_is_written_atomically_and_parses(store: Path) -> None:
    add_shower(store, "Perseids")

    document = json.loads(store.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["app"] == "astroarc"
    leftovers = [
        item.name
        for item in store.parent.iterdir()
        if item.name != store.name or ".tmp" in item.name
    ]
    assert leftovers == [], f"temporary files left behind: {leftovers}"


def test_corrupt_store_reports_the_path_and_exits_non_zero(store: Path) -> None:
    store.write_text("this is not json {", encoding="utf-8")

    result = run(["showers"], store)

    assert result.returncode == 1
    assert "store unreadable" in result.stderr
    assert str(store) in result.stderr
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


def test_store_from_the_future_is_refused(store: Path) -> None:
    store.write_text(
        json.dumps({"schema_version": 99, "showers": [], "sessions": [], "hourly_counts": []}),
        encoding="utf-8",
    )

    result = run(["showers"], store)

    assert result.returncode == 1
    assert "schema version 99" in result.stderr


def test_first_write_stores_only_a_salted_pbkdf2_hash(store: Path) -> None:
    add_shower(store, "Perseids")

    raw = store.read_text(encoding="utf-8")
    document = json.loads(raw)
    profile = document["profile"]

    assert DEMO_PASSPHRASE not in raw, "the passphrase must never be stored in the clear"
    assert len(profile["salt"]) == 32 and int(profile["salt"], 16) >= 0
    assert len(profile["passphrase_hash"]) == 64 and int(profile["passphrase_hash"], 16) >= 0
    assert profile["iterations"] >= 100_000
    # the stored hash is the app's own PBKDF2-HMAC-SHA256 of the passphrase
    expected = hashlib.pbkdf2_hmac(
        "sha256",
        DEMO_PASSPHRASE.encode("utf-8"),
        bytes.fromhex(profile["salt"]),
        profile["iterations"],
    ).hex()
    assert profile["passphrase_hash"] == expected


def test_a_wrong_passphrase_is_generic_and_leaves_the_file_untouched(store: Path) -> None:
    add_shower(store, "Perseids")
    before = store.read_bytes()

    result = run(
        [
            "shower",
            "add",
            "--name",
            "Geminids",
            "--constellation",
            "[redacted]",
            "--peak",
            "2024-12-14",
        ],
        store,
        passphrase="wrong-passphrase",
    )

    assert result.returncode == 3
    assert result.stderr.strip() == "error: passphrase incorrect - nothing was changed"
    assert store.read_bytes() == before
    assert "Geminids" not in run(["showers"], store).stdout


def test_a_write_without_any_passphrase_is_refused(store: Path) -> None:
    add_shower(store, "Perseids")
    before = store.read_bytes()

    result = run(
        [
            "shower",
            "add",
            "--name",
            "Geminids",
            "--constellation",
            "[redacted]",
            "--peak",
            "2024-12-14",
        ],
        store,
        passphrase=None,
    )

    assert result.returncode == 3
    assert "ASTROARC_PASSPHRASE" in result.stderr
    assert store.read_bytes() == before


def test_the_first_write_refuses_when_no_passphrase_can_be_created(store: Path) -> None:
    result = run(
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
        passphrase=None,
    )

    assert result.returncode == 3
    assert "no passphrase" in result.stderr


def test_reads_never_prompt_for_a_passphrase(store: Path) -> None:
    add_shower(store, "Perseids")
    session_id = start_session(store, "Perseids", "2023-08-12")
    log_hour(store, session_id, 22, 9)
    run(["session", "close", session_id], store)

    for args in (["showers"], ["sessions"], ["session", "show", session_id], ["compare"]):
        result = run(args, store, passphrase=None)
        assert result.returncode == 0, (args, result.stderr)
        assert "passphrase" not in result.stderr.lower()


def test_every_write_command_prompts_again_in_a_new_invocation(store: Path) -> None:
    add_shower(store, "Perseids")
    session_id = start_session(store, "Perseids", "2023-08-12")

    no_pass = run(
        ["session", "log", session_id, "--hour", "22", "--count", "9"], store, passphrase=None
    )

    assert no_pass.returncode == 3
    assert "no passphrase available" in no_pass.stderr
    # nothing is cached between commands: the same call needs the passphrase again
    assert (
        run(["session", "log", session_id, "--hour", "22", "--count", "9"], store).returncode == 0
    )


def test_a_piped_passphrase_unlocks_a_write(store: Path) -> None:
    add_shower(store, "Perseids")

    result = run(
        [
            "shower",
            "add",
            "--name",
            "Geminids",
            "--constellation",
            "[redacted]",
            "--peak",
            "2024-12-14",
        ],
        store,
        passphrase=None,
        stdin_text=f"{DEMO_PASSPHRASE}\n",
    )

    assert result.returncode == 0, result.stderr
    assert "Geminids" in run(["showers"], store).stdout


def test_unlock_sets_a_passphrase_on_first_use_then_verifies_it(store: Path) -> None:
    created = run(["unlock"], store)
    assert created.returncode == 0
    assert "passphrase set" in created.stdout

    verified = run(["unlock"], store)
    assert verified.returncode == 0
    assert "passphrase verified" in verified.stdout

    refused = run(["unlock"], store, passphrase="nope")
    assert refused.returncode == 3
    assert "passphrase incorrect" in refused.stderr


def test_json_mode_is_machine_readable(store: Path) -> None:
    add_shower(store, "Perseids")

    result = run(["showers", "--json"], store)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["showers"][0]["name"] == "Perseids"
    assert payload["showers"][0]["peak_year"] == 2024


def test_colour_is_off_when_output_is_piped(store: Path) -> None:
    add_shower(store, "Perseids")

    result = run(["showers"], store)

    assert "\x1b[" not in result.stdout


def test_colour_can_be_forced_and_is_disabled_by_no_color(store: Path) -> None:
    add_shower(store, "Perseids")

    forced = run(["showers", "--color", "always"], store)
    assert "\x1b[" in forced.stdout

    suppressed = run(["showers", "--color", "always", "--no-color"], store)
    assert "\x1b[" not in suppressed.stdout


def test_version_and_help_are_available(store: Path) -> None:
    version = run(["--version"], store)
    assert version.returncode == 0
    assert version.stdout.strip() == "astroarc 1.0.0"

    help_text = run(["--help"], store).stdout
    for command in (
        "shower add",
        "showers",
        "session start",
        "session log",
        "session show",
        "session close",
        "sessions",
        "compare",
        "unlock",
    ):
        assert command in help_text
    assert (
        "astroarc shower add --name Perseids --constellation Perseus --peak 2024-08-12" in help_text
    )
    assert "astroarc compare Perseids" in help_text


@pytest.mark.parametrize("group", ["shower", "session"])
def test_group_help_lists_its_subcommands(store: Path, group: str) -> None:
    result = run([group, "--help"], store)

    assert result.returncode == 0
    assert "SUBCOMMAND" in result.stdout
