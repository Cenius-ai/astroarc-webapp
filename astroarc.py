#!/usr/bin/env python3
"""AstroArc - a single-file meteor-shower observation log for the terminal.

Record showers (name, radiant constellation, peak date), run observation
sessions that capture an integer meteor count per hour, and compare each
shower's peak hourly count across every year present in the data.

Standard library only. Data lives in one JSON document (default
``~/.astroarc/store.json``, override with ``$ASTROARC_DATA``) that is written
atomically, so an interrupted save can never truncate it.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

__version__ = "1.0.0"

APP_NAME = "astroarc"
SCHEMA_VERSION = 1
DEFAULT_DATA_PATH = Path.home() / ".astroarc" / "store.json"
ACCENT_HEX = "#288f5e"
PBKDF2_ITERATIONS = 120_000
EM_DASH = "\u2014"

EXIT_OK = 0
EXIT_IO = 1
EXIT_USAGE = 2
EXIT_AUTH = 3

_DATE_PATTERN = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")
_INT_PATTERN = re.compile(r"\A-?\d+\Z")


# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------


class AstroArcError(Exception):
    """Base class for errors reported to the user as one clean stderr line."""

    exit_code = EXIT_IO


class UsageError(AstroArcError):
    """Bad input: nothing was written."""

    exit_code = EXIT_USAGE


class ConflictError(UsageError):
    """The write would duplicate an existing record."""


class AuthError(AstroArcError):
    """The local passphrase gate refused the write."""

    exit_code = EXIT_AUTH


class StoreError(AstroArcError):
    """The data file is missing, unreadable or structurally broken."""

    exit_code = EXIT_IO


# --------------------------------------------------------------------------
# design tokens - committed direction, not a default
#   archetype   CLI: the surface is the operator's terminal (dark), mono type
#   accent      oklch(0.58 0.12 158) == #288f5e, used for primary status only;
#               no colour-only signal, every status is also a word
#   neutral     the terminal's own foreground plus dim for chrome
#   shape       no chrome: box-drawing table frames and rules, two-space gutters
#   density     compact: one spacing unit, one type ramp, aligned columns
# --------------------------------------------------------------------------

_XTERM_LEVELS = (0, 95, 135, 175, 215, 255)
_RESET = "\x1b[0m"


def xterm_256_from_hex(colour: str) -> int:
    """Nearest xterm-256 colour-cube index for a hex colour.

    Terminal and chart APIs cannot parse ``oklch()``, so the committed accent
    travels as hex and is translated here.
    """
    value = colour.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"expected a 6-digit hex colour, got {colour!r}")
    channels = [int(value[i : i + 2], 16) for i in (0, 2, 4)]
    nearest = [min(range(6), key=lambda index: abs(_XTERM_LEVELS[index] - c)) for c in channels]
    return 16 + 36 * nearest[0] + 6 * nearest[1] + nearest[2]


ACCENT = f"\x1b[38;5;{xterm_256_from_hex(ACCENT_HEX)}m"


class Palette:
    """Minimal ANSI layer.

    Colour only ever wraps a status word or a heading, never a table cell, so
    escape codes can never disturb column alignment.
    """

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _paint(self, code: str, text: str) -> str:
        return f"{code}{text}{_RESET}" if self.enabled else text

    def accent(self, text: str) -> str:
        return self._paint(ACCENT, text)

    def bold(self, text: str) -> str:
        return self._paint("\x1b[1m", text)

    def dim(self, text: str) -> str:
        return self._paint("\x1b[2m", text)


def colour_enabled(mode: str, stream: Any) -> bool:
    """--color wins, then the NO_COLOR convention (presence, even empty), then TTY."""
    if mode == "never":
        return False
    if mode == "always":
        return True
    if "NO_COLOR" in os.environ:
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def make_palette(args: argparse.Namespace) -> Palette:
    enabled = colour_enabled(getattr(args, "color", "auto"), sys.stdout)
    if getattr(args, "no_color", False):
        enabled = False
    return Palette(enabled)


def out(text: str = "") -> None:
    print(text)


def err(text: str) -> None:
    print(text, file=sys.stderr)


def print_json(payload: Any) -> None:
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    right_align: frozenset[int] | set[int] = frozenset(),
) -> str:
    """Box-drawing table whose widths are computed from the data itself."""

    def rule(left: str, joint: str, right: str) -> str:
        return left + joint.join("\u2500" * (width + 2) for width in widths) + right

    def line(cells: Sequence[str]) -> str:
        padded = []
        for index, cell in enumerate(cells):
            body = cell.rjust(widths[index]) if index in right_align else cell.ljust(widths[index])
            padded.append(f" {body} ")
        return "\u2502" + "\u2502".join(padded) + "\u2502"

    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    lines = [rule("\u250c", "\u252c", "\u2510"), line(headers), rule("\u251c", "\u253c", "\u2524")]
    lines.extend(line(row) for row in rows)
    lines.append(rule("\u2514", "\u2534", "\u2518"))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# time helpers
# --------------------------------------------------------------------------


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def human_timestamp(stamp: str) -> str:
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def short_id(identifier: str) -> str:
    return identifier[:8]


def format_hour(hour: int) -> str:
    return f"{hour:02d}:00"


def plural(count: int, singular: str) -> str:
    """'1 shower' / '3 showers' - never '3 shower(s)'."""
    return f"{count} {singular if count == 1 else singular + 's'}"


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def parse_iso_date(raw: str, *, field_name: str) -> date:
    """Strict YYYY-MM-DD plus a real calendar check (2024-13-40 is rejected)."""
    text = (raw or "").strip()
    if not _DATE_PATTERN.match(text):
        raise UsageError(
            f"{field_name} {raw!r} is not in YYYY-MM-DD format "
            f"(example: 2024-08-12) - nothing was written"
        )
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise UsageError(
            f"{field_name} {raw!r} is not a real calendar date "
            f"(example: 2024-08-12) - nothing was written"
        ) from None


def parse_hour(raw: str) -> int:
    text = str(raw).strip()
    if not _INT_PATTERN.match(text):
        raise UsageError(
            f"hour {raw!r} is not a whole number (expected an integer hour 0-23, "
            f"example: 22) - nothing was written"
        )
    hour = int(text)
    if not 0 <= hour <= 23:
        raise UsageError(f"hour {hour} is outside 00-23 (expected 0-23) - nothing was written")
    return hour


def parse_count(raw: str) -> int:
    text = str(raw).strip()
    if not _INT_PATTERN.match(text):
        raise UsageError(
            f"count {raw!r} is not a whole number of meteors (expected 0 or more, "
            f"example: 7) - nothing was written"
        )
    count = int(text)
    if count < 0:
        raise UsageError(f"count {count} is negative (expected 0 or more) - nothing was written")
    return count


def clean_text(raw: str, field_name: str, *, max_length: int = 64) -> str:
    text = " ".join((raw or "").split())
    if not text:
        raise UsageError(f"{field_name} must not be empty - nothing was written")
    if len(text) > max_length:
        raise UsageError(
            f"{field_name} is longer than {max_length} characters - nothing was written"
        )
    return text


# --------------------------------------------------------------------------
# data model
# --------------------------------------------------------------------------


def _record(raw: Any, kind: str, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TypeError(f"{kind} record {index} is not a JSON object")
    return raw


def _text_field(raw: dict[str, Any], key: str, kind: str, index: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{kind} record {index} has no usable {key!r} field")
    return value


def _int_field(raw: dict[str, Any], key: str, kind: str, index: int) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{kind} record {index} has no usable {key!r} field")
    return value


@dataclass(frozen=True)
class Shower:
    id: str
    name: str
    constellation: str
    peak_date: str
    created_at: str

    @property
    def peak_year(self) -> int:
        return int(self.peak_date[:4])

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "constellation": self.constellation,
            "peak_date": self.peak_date,
            "peak_year": self.peak_year,
            "created_at": self.created_at,
        }

    @classmethod
    def from_json(cls, raw: Any, index: int = 0) -> Shower:
        data = _record(raw, "shower", index)
        peak_date = _text_field(data, "peak_date", "shower", index)
        if not _DATE_PATTERN.match(peak_date):
            raise ValueError(f"shower record {index} has a malformed peak_date {peak_date!r}")
        return cls(
            id=_text_field(data, "id", "shower", index),
            name=_text_field(data, "name", "shower", index),
            constellation=_text_field(data, "constellation", "shower", index),
            peak_date=peak_date,
            created_at=_text_field(data, "created_at", "shower", index),
        )


@dataclass(frozen=True)
class ObservationSession:
    id: str
    shower_id: str
    session_date: str
    started_at: str
    closed_at: str | None = None

    @property
    def year(self) -> int:
        return int(self.session_date[:4])

    @property
    def is_closed(self) -> bool:
        return self.closed_at is not None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "shower_id": self.shower_id,
            "session_date": self.session_date,
            "year": self.year,
            "started_at": self.started_at,
            "closed_at": self.closed_at,
        }

    @classmethod
    def from_json(cls, raw: Any, index: int = 0) -> ObservationSession:
        data = _record(raw, "session", index)
        session_date = _text_field(data, "session_date", "session", index)
        if not _DATE_PATTERN.match(session_date):
            raise ValueError(
                f"session record {index} has a malformed session_date {session_date!r}"
            )
        closed_at = data.get("closed_at")
        if closed_at is not None and not isinstance(closed_at, str):
            raise ValueError(f"session record {index} has an unusable closed_at field")
        return cls(
            id=_text_field(data, "id", "session", index),
            shower_id=_text_field(data, "shower_id", "session", index),
            session_date=session_date,
            started_at=_text_field(data, "started_at", "session", index),
            closed_at=closed_at,
        )


@dataclass(frozen=True)
class HourlyCount:
    id: str
    session_id: str
    hour: int
    count: int
    recorded_at: str

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "hour": self.hour,
            "count": self.count,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_json(cls, raw: Any, index: int = 0) -> HourlyCount:
        data = _record(raw, "hourly count", index)
        hour = _int_field(data, "hour", "hourly count", index)
        count = _int_field(data, "count", "hourly count", index)
        if not 0 <= hour <= 23:
            raise ValueError(f"hourly count record {index} has hour {hour} outside 0-23")
        if count < 0:
            raise ValueError(f"hourly count record {index} has a negative count")
        return cls(
            id=_text_field(data, "id", "hourly count", index),
            session_id=_text_field(data, "session_id", "hourly count", index),
            hour=hour,
            count=count,
            recorded_at=_text_field(data, "recorded_at", "hourly count", index),
        )


@dataclass(frozen=True)
class Profile:
    """The local passphrase gate. Only a salted PBKDF2 hash is ever stored."""

    name: str
    salt: str
    passphrase_hash: str
    iterations: int
    created_at: str

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "salt": self.salt,
            "passphrase_hash": self.passphrase_hash,
            "iterations": self.iterations,
            "created_at": self.created_at,
        }

    @classmethod
    def from_json(cls, raw: Any) -> Profile:
        data = _record(raw, "profile", 0)
        iterations = _int_field(data, "iterations", "profile", 0)
        if iterations <= 0:
            raise ValueError("profile has a non-positive iteration count")
        return cls(
            name=_text_field(data, "name", "profile", 0),
            salt=_text_field(data, "salt", "profile", 0),
            passphrase_hash=_text_field(data, "passphrase_hash", "profile", 0),
            iterations=iterations,
            created_at=_text_field(data, "created_at", "profile", 0),
        )


# --------------------------------------------------------------------------
# the JSON store
# --------------------------------------------------------------------------


def empty_document() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "app": APP_NAME,
        "profile": None,
        "showers": [],
        "sessions": [],
        "hourly_counts": [],
    }


def write_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Serialise to a sibling temp file, fsync, then os.replace into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def read_document(path: Path) -> dict[str, Any]:
    """Load the store, creating an empty versioned one when it does not exist."""
    if not path.exists():
        document = empty_document()
        write_atomic(path, document)
        return document
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StoreError(f"store unreadable at {path}: {exc.strerror or exc}") from exc
    if not raw.strip():
        raise StoreError(f"store unreadable at {path}: the file is empty")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StoreError(
            f"store unreadable at {path}: not valid JSON ({exc.msg} on line {exc.lineno})"
        ) from exc
    if not isinstance(document, dict):
        raise StoreError(f"store unreadable at {path}: expected a JSON object at the top level")
    version = document.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise StoreError(f"store unreadable at {path}: no schema_version field")
    if version > SCHEMA_VERSION:
        raise StoreError(
            f"store unreadable at {path}: schema version {version} is newer than this "
            f"astroarc understands (up to {SCHEMA_VERSION}) - upgrade astroarc"
        )
    for key in ("showers", "sessions", "hourly_counts"):
        if not isinstance(document.get(key), list):
            raise StoreError(f"store unreadable at {path}: {key!r} is missing or is not a list")
    return document


@dataclass
class Store:
    path: Path
    profile: Profile | None = None
    showers: list[Shower] = field(default_factory=list)
    sessions: list[ObservationSession] = field(default_factory=list)
    counts: list[HourlyCount] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Store:
        document = read_document(path)
        raw_profile = document.get("profile")
        try:
            profile = Profile.from_json(raw_profile) if raw_profile is not None else None
            showers = [
                Shower.from_json(raw, index) for index, raw in enumerate(document["showers"])
            ]
            sessions = [
                ObservationSession.from_json(raw, index)
                for index, raw in enumerate(document["sessions"])
            ]
            counts = [
                HourlyCount.from_json(raw, index)
                for index, raw in enumerate(document["hourly_counts"])
            ]
        except (TypeError, ValueError) as exc:
            raise StoreError(f"store unreadable at {path}: {exc}") from exc
        store = cls(path=path, profile=profile, showers=showers, sessions=sessions, counts=counts)
        store.check_links()
        return store

    def check_links(self) -> None:
        shower_ids = {shower.id for shower in self.showers}
        for session in self.sessions:
            if session.shower_id not in shower_ids:
                raise StoreError(
                    f"store unreadable at {self.path}: session {session.id} refers to "
                    f"an unknown shower {session.shower_id}"
                )
        session_ids = {session.id for session in self.sessions}
        for entry in self.counts:
            if entry.session_id not in session_ids:
                raise StoreError(
                    f"store unreadable at {self.path}: hourly count {entry.id} refers to "
                    f"an unknown session {entry.session_id}"
                )

    def save(self) -> None:
        write_atomic(
            self.path,
            {
                "schema_version": SCHEMA_VERSION,
                "app": APP_NAME,
                "profile": self.profile.to_json() if self.profile is not None else None,
                "showers": [shower.to_json() for shower in self.showers],
                "sessions": [session.to_json() for session in self.sessions],
                "hourly_counts": [entry.to_json() for entry in self.counts],
            },
        )

    # -- lookups ---------------------------------------------------------

    def shower(self, shower_id: str) -> Shower | None:
        return next((shower for shower in self.showers if shower.id == shower_id), None)

    def resolve_shower(self, reference: str) -> Shower:
        text = (reference or "").strip()
        if not text:
            raise UsageError("a shower name or id is required - run: astroarc showers")
        for shower in self.showers:
            if shower.id == text:
                return shower
        if len(text) >= 4:
            prefixed = [shower for shower in self.showers if shower.id.startswith(text)]
            if len(prefixed) == 1:
                return prefixed[0]
            if len(prefixed) > 1:
                raise UsageError(f"shower id prefix {text!r} is ambiguous - run: astroarc showers")
        named = [shower for shower in self.showers if shower.name.casefold() == text.casefold()]
        if len(named) == 1:
            return named[0]
        if len(named) > 1:
            listing = "\n".join(
                f"  {shower.id}  {shower.name} / peak {shower.peak_date}" for shower in named
            )
            raise UsageError(
                f"{len(named)} showers are named {text!r} - pass the id instead:\n{listing}"
            )
        if not self.showers:
            raise UsageError(
                "no showers recorded yet - add one with: astroarc shower add "
                "--name Perseids --constellation Perseus --peak 2024-08-12"
            )
        known = ", ".join(sorted(shower.name for shower in self.showers))
        raise UsageError(f"no shower matching {text!r} - recorded showers: {known}")

    def resolve_session(self, reference: str) -> ObservationSession:
        text = (reference or "").strip()
        if not text:
            raise UsageError("a session id is required - run: astroarc sessions")
        for session in self.sessions:
            if session.id == text:
                return session
        if len(text) >= 4:
            prefixed = [session for session in self.sessions if session.id.startswith(text)]
            if len(prefixed) == 1:
                return prefixed[0]
            if len(prefixed) > 1:
                raise UsageError(
                    f"session id prefix {text!r} is ambiguous - run: astroarc sessions"
                )
        if not self.sessions:
            raise UsageError(
                "no sessions recorded yet - start one with: astroarc session start "
                "--shower Perseids --date 2024-08-12"
            )
        raise UsageError(f"no session with id {text!r} - run: astroarc sessions")

    def sessions_of(self, shower_id: str) -> list[ObservationSession]:
        return [session for session in self.sessions if session.shower_id == shower_id]

    def counts_of(self, session_id: str) -> list[HourlyCount]:
        return sorted(
            (entry for entry in self.counts if entry.session_id == session_id),
            key=lambda entry: entry.hour,
        )


def data_path(args: argparse.Namespace) -> Path:
    override = getattr(args, "data", None) or os.environ.get("ASTROARC_DATA", "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_DATA_PATH


# --------------------------------------------------------------------------
# derived views (totals and peaks are never stored, always recomputed)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HourRow:
    hour: int
    count: int
    running_total: int


@dataclass(frozen=True)
class SessionSummary:
    rows: list[HourRow]
    total: int
    peak: HourRow | None

    @property
    def hour_count(self) -> int:
        return len(self.rows)


def summarise_session(store: Store, session: ObservationSession) -> SessionSummary:
    rows: list[HourRow] = []
    running_total = 0
    for entry in store.counts_of(session.id):
        running_total += entry.count
        rows.append(HourRow(hour=entry.hour, count=entry.count, running_total=running_total))
    peak = max(rows, key=lambda row: (row.count, -row.hour)) if rows else None
    return SessionSummary(rows=rows, total=running_total, peak=peak)


@dataclass(frozen=True)
class CompareRow:
    shower: Shower
    label: str
    peaks: dict[int, int | None]


@dataclass(frozen=True)
class CompareTable:
    years: list[int]
    rows: list[CompareRow]


def peak_by_session(store: Store) -> dict[str, int]:
    """Highest single-hour count per session, computed in one pass."""
    maxima: dict[str, int] = {}
    for entry in store.counts:
        current = maxima.get(entry.session_id)
        if current is None or entry.count > current:
            maxima[entry.session_id] = entry.count
    return maxima


def build_compare(store: Store, name_filter: str | None) -> CompareTable:
    """One row per shower, one column per year present in the matching sessions."""
    if name_filter:
        wanted = name_filter.strip().casefold()
        selected = [shower for shower in store.showers if shower.name.casefold() == wanted]
        if not selected:
            known = ", ".join(sorted({shower.name for shower in store.showers})) or "(none yet)"
            raise UsageError(
                f"no shower named {name_filter!r} - recorded showers: {known}. "
                f"Run: astroarc showers"
            )
    else:
        selected = sorted(
            store.showers, key=lambda shower: (shower.name.casefold(), shower.peak_date)
        )

    selected_ids = {shower.id for shower in selected}
    maxima = peak_by_session(store)
    peaks: dict[str, dict[int, int]] = {}
    years: set[int] = set()
    for session in store.sessions:
        if session.shower_id not in selected_ids:
            continue
        peak = maxima.get(session.id)
        if peak is None:
            continue
        years.add(session.year)
        bucket = peaks.setdefault(session.shower_id, {})
        if session.year not in bucket or peak > bucket[session.year]:
            bucket[session.year] = peak

    name_counts: dict[str, int] = {}
    for shower in selected:
        key = shower.name.casefold()
        name_counts[key] = name_counts.get(key, 0) + 1

    rows = []
    for shower in selected:
        duplicated = name_counts[shower.name.casefold()] > 1
        label = f"{shower.name} ({shower.peak_date})" if duplicated else shower.name
        bucket = peaks.get(shower.id, {})
        rows.append(
            CompareRow(
                shower=shower,
                label=label,
                peaks={year: bucket.get(year) for year in years},
            )
        )
    return CompareTable(years=sorted(years), rows=rows)


# --------------------------------------------------------------------------
# passphrase gate
# --------------------------------------------------------------------------


def hash_passphrase(passphrase: str, salt_hex: str, iterations: int) -> str:
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError as exc:
        raise StoreError("store unreadable: the stored passphrase salt is not valid hex") from exc
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations).hex()


def build_profile(passphrase: str) -> Profile:
    salt = secrets.token_hex(16)
    iterations = PBKDF2_ITERATIONS
    return Profile(
        name="local observer",
        salt=salt,
        passphrase_hash=hash_passphrase(passphrase, salt, iterations),
        iterations=iterations,
        created_at=utc_now(),
    )


def verify_passphrase(profile: Profile, passphrase: str) -> bool:
    candidate = hash_passphrase(passphrase, profile.salt, profile.iterations)
    return hmac.compare_digest(candidate, profile.passphrase_hash)


def supplied_passphrase(args: argparse.Namespace) -> str | None:
    """--passphrase, then $ASTROARC_PASSPHRASE, then one piped stdin line."""
    value = getattr(args, "passphrase", None)
    if value is None:
        value = os.environ.get("ASTROARC_PASSPHRASE")
    if value is not None:
        return value
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        if line.strip():
            return line.rstrip("\n")
    return None


def read_passphrase(args: argparse.Namespace) -> str:
    provided = supplied_passphrase(args)
    if provided is not None:
        return provided
    if sys.stdin.isatty():
        return getpass.getpass("Passphrase: ")
    raise AuthError(
        "no passphrase available - export ASTROARC_PASSPHRASE or pass --passphrase "
        "(interactive terminals are prompted)"
    )


def read_new_passphrase(args: argparse.Namespace, palette: Palette) -> str:
    provided = supplied_passphrase(args)
    if provided is not None:
        if not provided:
            raise UsageError("passphrase must not be empty - nothing was written")
        return provided
    if not sys.stdin.isatty():
        raise AuthError(
            "no passphrase to set - export ASTROARC_PASSPHRASE or pass --passphrase "
            "(interactive terminals are prompted twice)"
        )
    out(
        palette.bold("No passphrase set yet.")
        + " Create one to protect writes; only a salted PBKDF2-HMAC-SHA256 hash is stored."
    )
    first = getpass.getpass("New passphrase: ")
    second = getpass.getpass("Repeat passphrase: ")
    if not first:
        raise UsageError("passphrase must not be empty - nothing was written")
    if first != second:
        raise UsageError("passphrases did not match - nothing was written")
    return first


def unlock_for_write(store: Store, args: argparse.Namespace, palette: Palette) -> Profile:
    """Verify (or create) the local passphrase. Never mutates the stored data itself."""
    if store.profile is None:
        profile = build_profile(read_new_passphrase(args, palette))
        store.profile = profile
        return profile
    passphrase = read_passphrase(args)
    if not verify_passphrase(store.profile, passphrase):
        raise AuthError("passphrase incorrect - nothing was changed")
    return store.profile


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def shell_quote(text: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_.:@%+=,-]+", text):
        return text
    return "'" + text.replace("'", "'\"'\"'") + "'"


def cmd_showers(args: argparse.Namespace) -> int:
    palette = make_palette(args)
    store = Store.load(data_path(args))
    showers = sorted(
        store.showers, key=lambda item: (item.peak_date, item.name.casefold()), reverse=True
    )
    if getattr(args, "json", False):
        print_json({"showers": [shower.to_json() for shower in showers]})
        return EXIT_OK
    if not showers:
        out("No showers yet - add one with:")
        out("  astroarc shower add --name Perseids --constellation Perseus --peak 2024-08-12")
        return EXIT_OK
    out(f"{palette.bold('Showers')}  {palette.dim(f'{len(showers)} recorded / newest peak first')}")
    out(
        render_table(
            ["Shower", "Constellation", "Peak date", "Year"],
            [
                [shower.name, shower.constellation, shower.peak_date, str(shower.peak_year)]
                for shower in showers
            ],
            right_align={3},
        )
    )
    return EXIT_OK


def cmd_shower_add(args: argparse.Namespace) -> int:
    palette = make_palette(args)
    name = clean_text(args.name, "shower name")
    constellation = clean_text(args.constellation, "radiant constellation")
    peak = parse_iso_date(args.peak, field_name="peak date")
    store = Store.load(data_path(args))
    for existing in store.showers:
        if existing.name.casefold() == name.casefold() and existing.peak_date == peak.isoformat():
            raise ConflictError(
                f"shower {existing.name!r} with peak date {existing.peak_date} is already "
                f"recorded (id {existing.id}) - nothing was added"
            )
    unlock_for_write(store, args, palette)
    shower = Shower(
        id=str(uuid.uuid4()),
        name=name,
        constellation=constellation,
        peak_date=peak.isoformat(),
        created_at=utc_now(),
    )
    store.showers.append(shower)
    store.save()
    if getattr(args, "json", False):
        print_json({"status": "ok", "shower": shower.to_json()})
        return EXIT_OK
    out(
        f"{palette.accent('saved')}  {shower.name} / {shower.constellation} / "
        f"peak {shower.peak_date} ({shower.peak_year})"
    )
    out(
        f"{palette.dim('next')}   astroarc session start --shower "
        f"{shell_quote(shower.name)} --date {shower.peak_date}"
    )
    return EXIT_OK


def cmd_sessions(args: argparse.Namespace) -> int:
    palette = make_palette(args)
    store = Store.load(data_path(args))
    sessions = sorted(
        store.sessions, key=lambda item: (item.session_date, item.started_at), reverse=True
    )
    rows = []
    payload = []
    for session in sessions:
        shower = store.shower(session.shower_id)
        summary = summarise_session(store, session)
        rows.append(
            [
                short_id(session.id),
                shower.name if shower else "(missing shower)",
                session.session_date,
                str(summary.hour_count),
                str(summary.total),
                str(summary.peak.count) if summary.peak else EM_DASH,
                "closed" if session.is_closed else "open",
            ]
        )
        payload.append(
            {
                "id": session.id,
                "shower": shower.name if shower else None,
                "session_date": session.session_date,
                "hours": summary.hour_count,
                "total": summary.total,
                "peak": summary.peak.count if summary.peak else None,
                "closed_at": session.closed_at,
            }
        )
    if getattr(args, "json", False):
        print_json({"sessions": payload})
        return EXIT_OK
    if not sessions:
        out("No sessions yet - start one with:")
        out("  astroarc session start --shower Perseids --date 2024-08-12")
        return EXIT_OK
    out(f"{palette.bold('Sessions')}  {palette.dim(f'{len(sessions)} recorded / newest first')}")
    out(
        render_table(
            ["Session", "Shower", "Date", "Hours", "Total", "Peak", "State"],
            rows,
            right_align={3, 4, 5},
        )
    )
    return EXIT_OK


def cmd_session_start(args: argparse.Namespace) -> int:
    palette = make_palette(args)
    session_date = parse_iso_date(args.date, field_name="session date")
    store = Store.load(data_path(args))
    shower = store.resolve_shower(args.shower)
    unlock_for_write(store, args, palette)
    session = ObservationSession(
        id=str(uuid.uuid4()),
        shower_id=shower.id,
        session_date=session_date.isoformat(),
        started_at=utc_now(),
        closed_at=None,
    )
    store.sessions.append(session)
    store.save()
    if getattr(args, "json", False):
        print_json({"status": "ok", "session": session.to_json()})
        return EXIT_OK
    out(
        f"{palette.accent('opened')}  {shower.name} / {shower.constellation} / "
        f"observation date {session.session_date}"
    )
    out(f"{palette.dim('session')} {session.id}")
    out(f"{palette.dim('next')}    astroarc session log {session.id} --hour 22 --count 7")
    return EXIT_OK


def cmd_session_log(args: argparse.Namespace) -> int:
    palette = make_palette(args)
    hour = parse_hour(args.hour)
    count = parse_count(args.count)
    store = Store.load(data_path(args))
    session = store.resolve_session(args.session)
    shower = store.shower(session.shower_id)
    if session.is_closed:
        raise UsageError(
            f"session {short_id(session.id)} is closed (closed "
            f"{human_timestamp(session.closed_at or '')}) - counts are refused. "
            f"Start a new session with: astroarc session start --shower "
            f"{shell_quote(shower.name if shower else '<shower>')} --date {session.session_date}"
        )
    unlock_for_write(store, args, palette)
    stamp = utc_now()
    previous: HourlyCount | None = None
    for index, entry in enumerate(store.counts):
        if entry.session_id == session.id and entry.hour == hour:
            previous = entry
            store.counts[index] = replace(entry, count=count, recorded_at=stamp)
            break
    if previous is None:
        store.counts.append(
            HourlyCount(
                id=str(uuid.uuid4()),
                session_id=session.id,
                hour=hour,
                count=count,
                recorded_at=stamp,
            )
        )
    store.save()
    summary = summarise_session(store, session)
    if getattr(args, "json", False):
        print_json(
            {
                "status": "ok",
                "session": session.to_json(),
                "hour": hour,
                "count": count,
                "replaced": previous.count if previous is not None else None,
                "total": summary.total,
                "peak": summary.peak.count if summary.peak else None,
            }
        )
        return EXIT_OK
    replaced_note = f" (overwrote {previous.count})" if previous is not None else ""
    out(
        f"{palette.accent('logged')}  {count} meteors at {format_hour(hour)}{replaced_note} / "
        f"total {summary.total} / peak hour {summary.peak.count if summary.peak else 0} "
        f"at {format_hour(summary.peak.hour) if summary.peak else format_hour(hour)}"
    )
    return EXIT_OK


def cmd_session_show(args: argparse.Namespace) -> int:
    palette = make_palette(args)
    store = Store.load(data_path(args))
    session = store.resolve_session(args.session)
    shower = store.shower(session.shower_id)
    summary = summarise_session(store, session)
    peak = summary.peak
    if getattr(args, "json", False):
        print_json(
            {
                "session": session.to_json(),
                "shower": shower.to_json() if shower else None,
                "hours": [
                    {"hour": row.hour, "count": row.count, "running_total": row.running_total}
                    for row in summary.rows
                ],
                "total": summary.total,
                "peak": {"hour": peak.hour, "count": peak.count} if peak else None,
            }
        )
        return EXIT_OK
    name = shower.name if shower else "(missing shower)"
    constellation = f" / {shower.constellation}" if shower else ""
    out(
        f"{palette.bold(name)}{palette.dim(constellation)}   "
        f"{palette.dim('session')} {short_id(session.id)}"
    )
    state = f"closed {human_timestamp(session.closed_at)}" if session.is_closed else "open"
    out(
        palette.dim(
            f"observation date {session.session_date} / {state} / "
            f"{plural(summary.hour_count, 'hour')} logged"
        )
    )
    if not summary.rows:
        out("")
        out("No counts yet - record one with:")
        out(f"  astroarc session log {session.id} --hour 22 --count 7")
        return EXIT_OK
    out("")
    out(
        render_table(
            ["Hour", "Count", "Total"],
            [
                [format_hour(row.hour), str(row.count), str(row.running_total)]
                for row in summary.rows
            ],
            right_align={1, 2},
        )
    )
    tail = f"total {summary.total}"
    if peak is not None:
        tail += f" / peak hour {peak.count} at {format_hour(peak.hour)}"
    out(tail)
    return EXIT_OK


def cmd_session_close(args: argparse.Namespace) -> int:
    palette = make_palette(args)
    store = Store.load(data_path(args))
    session = store.resolve_session(args.session)
    if session.is_closed:
        out(
            f"session {short_id(session.id)} is already closed "
            f"({human_timestamp(session.closed_at or '')}) - nothing changed"
        )
        return EXIT_OK
    unlock_for_write(store, args, palette)
    closed = replace(session, closed_at=utc_now())
    store.sessions[store.sessions.index(session)] = closed
    store.save()
    summary = summarise_session(store, closed)
    if getattr(args, "json", False):
        print_json({"status": "ok", "session": closed.to_json(), "total": summary.total})
        return EXIT_OK
    out(
        f"{palette.accent('closed')}  session {short_id(closed.id)} / "
        f"{plural(summary.hour_count, 'hour')} / total {summary.total} - further counts are refused"
    )
    return EXIT_OK


def cmd_compare(args: argparse.Namespace) -> int:
    palette = make_palette(args)
    store = Store.load(data_path(args))
    table = build_compare(store, args.shower)
    if getattr(args, "json", False):
        print_json(
            {
                "years": table.years,
                "rows": [
                    {
                        "label": row.label,
                        "shower": row.shower.name,
                        "peak_date": row.shower.peak_date,
                        "cells": {str(year): row.peaks.get(year) for year in table.years},
                    }
                    for row in table.rows
                ],
            }
        )
        return EXIT_OK
    if not store.showers:
        out("No showers yet - add one with:")
        out("  astroarc shower add --name Perseids --constellation Perseus --peak 2024-08-12")
        return EXIT_OK
    if not table.years:
        if args.shower and table.rows:
            example = table.rows[0].shower
            out(f"No sessions recorded for {example.name} yet - the comparison needs at least one.")
            out("Start one with:")
            out(
                f"  astroarc session start --shower {shell_quote(example.name)} --date {example.peak_date}"
            )
        else:
            example = store.showers[0]
            out("No sessions recorded yet - the comparison needs at least one. Start one with:")
            out(
                f"  astroarc session start --shower {shell_quote(example.name)} --date {example.peak_date}"
            )
        return EXIT_OK

    headers = ["Shower"] + [str(year) for year in table.years]
    rows = [
        [row.label]
        + [
            str(row.peaks[year]) if row.peaks.get(year) is not None else EM_DASH
            for year in table.years
        ]
        for row in table.rows
    ]
    counted = f"{plural(len(rows), 'shower')} / {plural(len(table.years), 'year')}"
    out(f"{palette.bold('Peak meteor count per hour')}  {palette.dim(counted)}")
    out(render_table(headers, rows, right_align=set(range(1, len(headers)))))
    out(palette.dim(f"{EM_DASH} means no session with counts in that year"))
    return EXIT_OK


def cmd_unlock(args: argparse.Namespace) -> int:
    palette = make_palette(args)
    store = Store.load(data_path(args))
    if store.profile is None:
        passphrase = read_new_passphrase(args, palette)
        store.profile = build_profile(passphrase)
        store.save()
        if getattr(args, "json", False):
            print_json({"status": "created"})
            return EXIT_OK
        out(
            f"{palette.accent('saved')}  passphrase set - write commands now require it "
            f"(stored as a salted PBKDF2-HMAC-SHA256 hash, never plaintext)"
        )
        return EXIT_OK
    passphrase = read_passphrase(args)
    if not verify_passphrase(store.profile, passphrase):
        raise AuthError("passphrase incorrect - nothing was changed")
    if getattr(args, "json", False):
        print_json({"status": "verified"})
        return EXIT_OK
    out(
        f"{palette.accent('ok')}     passphrase verified - each write command prompts again "
        f"(nothing is cached between runs)"
    )
    return EXIT_OK


# --------------------------------------------------------------------------
# command line surface
# --------------------------------------------------------------------------

EPILOG = """\
commands:
  shower add      record a shower            astroarc shower add --name Perseids --constellation Perseus --peak 2024-08-12
  showers         list recorded showers      astroarc showers
  session start   open an observation        astroarc session start --shower Perseids --date 2023-08-12
  session log     record one hour 00-23      astroarc session log SESSION --hour 22 --count 7
  session show    hourly rows, total, peak   astroarc session show SESSION
  session close   refuse further counts      astroarc session close SESSION
  sessions        list sessions              astroarc sessions
  compare         cross-year peak table      astroarc compare Perseids
  unlock          set or verify passphrase   astroarc unlock

write commands (shower add, session start, session log, session close, unlock)
verify the local passphrase: from $ASTROARC_PASSPHRASE, from --passphrase, or
from an interactive prompt. read commands never prompt.

environment:
  ASTROARC_DATA        JSON store path (default ~/.astroarc/store.json)
  ASTROARC_PASSPHRASE  passphrase for the non-interactive write path
  NO_COLOR             set to disable colour

exit codes: 0 ok, 1 store or I/O error, 2 usage or validation error,
3 passphrase refused. Errors go to stderr, results to stdout.
"""


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="JSON store path (default: $ASTROARC_DATA or ~/.astroarc/store.json)",
    )
    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print machine-readable JSON instead of a table",
    )
    common.add_argument(
        "--passphrase",
        metavar="TEXT",
        default=argparse.SUPPRESS,
        help="passphrase for write commands (prefer the ASTROARC_PASSPHRASE env var)",
    )
    common.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default=argparse.SUPPRESS,
        help="colourise output (default: auto, only when stdout is a terminal)",
    )
    common.add_argument(
        "--no-color",
        action="store_true",
        default=argparse.SUPPRESS,
        help="never colourise output",
    )

    formatter = argparse.RawDescriptionHelpFormatter
    parser = argparse.ArgumentParser(
        prog="astroarc",
        parents=[common],
        formatter_class=formatter,
        description=(
            "AstroArc - record meteor showers, log hourly meteor counts,\n"
            "and compare each shower's peak hourly count across years.\n"
            "Standard library only; all data lives in one local JSON file."
        ),
        epilog=EPILOG,
    )
    parser.add_argument("--version", action="version", version=f"astroarc {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    # -- shower add ------------------------------------------------------
    shower_group = commands.add_parser(
        "shower",
        parents=[common],
        formatter_class=formatter,
        help="record showers (example: astroarc shower add --name Perseids "
        "--constellation Perseus --peak 2024-08-12)",
        description="Record meteor showers.",
    )
    shower_group.set_defaults(handler=None, group_parser=shower_group)
    shower_commands = shower_group.add_subparsers(dest="shower_command", metavar="SUBCOMMAND")
    shower_add = shower_commands.add_parser(
        "add",
        parents=[common],
        formatter_class=formatter,
        help="record one shower (example: astroarc shower add --name Perseids "
        "--constellation Perseus --peak 2024-08-12)",
        description=(
            "Record a shower's name, radiant constellation and peak date.\n"
            "One record is kept per name plus peak date, so re-adding the same\n"
            "shower reports that it already exists instead of duplicating it."
        ),
        epilog=(
            "example:\n"
            "  astroarc shower add --name Perseids --constellation Perseus --peak 2024-08-12\n"
        ),
    )
    shower_add.add_argument("--name", required=True, help="shower name, for example Perseids")
    shower_add.add_argument(
        "--constellation",
        required=True,
        help="radiant constellation, for example Perseus",
    )
    shower_add.add_argument(
        "--peak",
        "--peak-date",
        dest="peak",
        required=True,
        metavar="YYYY-MM-DD",
        help="peak date; must be a real calendar date",
    )
    shower_add.set_defaults(handler=cmd_shower_add, group_parser=shower_group)

    # -- showers ---------------------------------------------------------
    showers_cmd = commands.add_parser(
        "showers",
        parents=[common],
        formatter_class=formatter,
        help="list recorded showers (example: astroarc showers)",
        description="List recorded showers, newest peak first.",
        epilog="example:\n  astroarc showers\n  astroarc showers --json\n",
    )
    showers_cmd.set_defaults(handler=cmd_showers)

    # -- session ---------------------------------------------------------
    session_group = commands.add_parser(
        "session",
        parents=[common],
        formatter_class=formatter,
        help="run observation sessions (example: astroarc session start "
        "--shower Perseids --date 2023-08-12)",
        description="Start, log, show and close observation sessions.",
    )
    session_group.set_defaults(handler=None, group_parser=session_group)
    session_commands = session_group.add_subparsers(dest="session_command", metavar="SUBCOMMAND")

    session_start = session_commands.add_parser(
        "start",
        parents=[common],
        formatter_class=formatter,
        help="open a session (example: astroarc session start --shower Perseids "
        "--date 2023-08-12)",
        description="Open an observation session for a recorded shower on a validated date.",
        epilog="example:\n  astroarc session start --shower Perseids --date 2023-08-12\n",
    )
    session_start.add_argument(
        "--shower",
        required=True,
        metavar="NAME|ID",
        help="name or id of an already recorded shower",
    )
    session_start.add_argument(
        "--date",
        required=True,
        metavar="YYYY-MM-DD",
        help="observation date; must be a real calendar date",
    )
    session_start.set_defaults(handler=cmd_session_start, group_parser=session_group)

    session_log = session_commands.add_parser(
        "log",
        parents=[common],
        formatter_class=formatter,
        help="record one hour's count (example: astroarc session log SESSION --hour 22 --count 7)",
        description=(
            "Record the meteor count for one hour of a session.\n"
            "Re-recording an hour overwrites that hour instead of double-counting."
        ),
        epilog=(
            "examples:\n"
            "  astroarc session log SESSION --hour 22 --count 7\n"
            "  astroarc session log SESSION --hour 22 --count 9   # overwrites the 7\n"
        ),
    )
    session_log.add_argument(
        "session", metavar="SESSION", help="session id (a unique prefix works)"
    )
    session_log.add_argument(
        "--hour", required=True, metavar="0-23", help="hour of the night, 0-23"
    )
    session_log.add_argument(
        "--count",
        required=True,
        metavar="N",
        help="whole number of meteors seen in that hour (0 or more)",
    )
    session_log.set_defaults(handler=cmd_session_log, group_parser=session_group)

    session_show = session_commands.add_parser(
        "show",
        parents=[common],
        formatter_class=formatter,
        help="show hourly rows, total and peak (example: astroarc session show SESSION)",
        description="Show one session's hourly rows with the running total and peak hourly count.",
        epilog="example:\n  astroarc session show SESSION\n",
    )
    session_show.add_argument(
        "session", metavar="SESSION", help="session id (a unique prefix works)"
    )
    session_show.set_defaults(handler=cmd_session_show, group_parser=session_group)

    session_close = session_commands.add_parser(
        "close",
        parents=[common],
        formatter_class=formatter,
        help="close a session so further counts are refused (example: astroarc session close SESSION)",
        description="Close a session; later count writes are refused.",
        epilog="example:\n  astroarc session close SESSION\n",
    )
    session_close.add_argument(
        "session", metavar="SESSION", help="session id (a unique prefix works)"
    )
    session_close.set_defaults(handler=cmd_session_close, group_parser=session_group)

    # -- sessions --------------------------------------------------------
    sessions_cmd = commands.add_parser(
        "sessions",
        parents=[common],
        formatter_class=formatter,
        help="list sessions (example: astroarc sessions)",
        description="List observation sessions, newest first, with totals and peak hourly counts.",
        epilog="example:\n  astroarc sessions\n",
    )
    sessions_cmd.set_defaults(handler=cmd_sessions)

    # -- compare ---------------------------------------------------------
    compare_cmd = commands.add_parser(
        "compare",
        parents=[common],
        formatter_class=formatter,
        help="cross-year peak table (example: astroarc compare Perseids)",
        description=(
            "Print a plain-text table comparing each shower's highest single-hour count\n"
            "across every year present in the data. One row per shower, one column per\n"
            "year, an em dash where a shower has no session with counts in that year."
        ),
        epilog="examples:\n  astroarc compare\n  astroarc compare Perseids\n",
    )
    compare_cmd.add_argument(
        "shower",
        nargs="?",
        metavar="SHOWER",
        help="limit the table to one shower name (default: every recorded shower)",
    )
    compare_cmd.set_defaults(handler=cmd_compare)

    # -- unlock ----------------------------------------------------------
    unlock_cmd = commands.add_parser(
        "unlock",
        parents=[common],
        formatter_class=formatter,
        help="set the passphrase the first time, or verify it (example: astroarc unlock)",
        description=(
            "Set the local passphrase on first use (stored only as a salted\n"
            "PBKDF2-HMAC-SHA256 hash) or verify it on later runs."
        ),
        epilog="example:\n  astroarc unlock\n",
    )
    unlock_cmd.set_defaults(handler=cmd_unlock)

    parser.set_defaults(handler=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    handler = getattr(args, "handler", None)
    if handler is None:
        group = getattr(args, "group_parser", None)
        if group is not None:
            group.print_help()
            return EXIT_OK
        parser.print_help()
        out("")
        out("Start with: astroarc showers")
        return EXIT_OK
    try:
        return handler(args)
    except AstroArcError as exc:
        err(f"error: {exc}")
        return exc.exit_code
    except BrokenPipeError:
        return EXIT_OK
    except OSError as exc:
        err(f"error: cannot write the data file: {exc}")
        return EXIT_IO
    except KeyboardInterrupt:
        err("cancelled")
        return 130


if __name__ == "__main__":
    sys.exit(main())
