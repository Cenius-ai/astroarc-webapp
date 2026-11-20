# Using AstroArc

Every example below is a real transcript from the built tool. `SESSION` stands for a session
id printed by `session start` (a unique prefix such as `0fc81f74` works too).

```bash
export ASTROARC_DATA=~/astroarc-demo.json    # keep the demo out of your real log
export ASTROARC_PASSPHRASE=cenius            # the passphrase this store will be created with
```

## 1. Record a shower

```console
$ astroarc shower add --name Perseids --constellation Perseus --peak 2024-08-12
saved  Perseids / Perseus / peak 2024-08-12 (2024)
next   astroarc session start --shower Perseids --date 2024-08-12
```

Bad input is refused before anything is stored:

```console
$ astroarc shower add --name Draconids --constellation Draco --peak 2024-13-40
error: peak date '2024-13-40' is not a real calendar date (example: 2024-08-12) - nothing was written
$ echo $?
2
```

Re-adding the same name and peak date reports the existing record instead of duplicating it.
The same name with a *different* peak date is a separate record (one row per name + peak date).

## 2. List what you have

```console
$ astroarc showers
Showers  2 recorded / newest peak first
┌──────────┬───────────────┬────────────┬──────┐
│ Shower   │ Constellation │ Peak date  │ Year │
├──────────┼───────────────┼────────────┼──────┤
│ Geminids │ [redacted]        │ 2024-12-14 │ 2024 │
│ Perseids │ Perseus       │ 2024-08-12 │ 2024 │
└──────────┴───────────────┴────────────┴──────┘
```

With nothing recorded yet, the empty state names the exact command that fills it:

```console
$ astroarc showers
No showers yet - add one with:
  astroarc shower add --name Perseids --constellation Perseus --peak 2024-08-12
```

## 3. Open a session and log hours

```console
$ astroarc session start --shower Perseids --date 2024-08-12
opened  Perseids / Perseus / observation date 2024-08-12
session 0fc81f74-3c2e-4b8f-9a51-77f0b1a1f4c9
next    astroarc session log 0fc81f74-3c2e-4b8f-9a51-77f0b1a1f4c9 --hour 22 --count 7

$ astroarc session log 0fc81f74 --hour 22 --count 7
logged  7 meteors at 22:00 / total 7 / peak hour 7 at 22:00

$ astroarc session log 0fc81f74 --hour 22 --count 9
logged  9 meteors at 22:00 (overwrote 7) / total 9 / peak hour 9 at 22:00
```

Re-recording an hour **overwrites** it, so a corrected count can never double-count. Hours must
be 0-23 and counts whole numbers of 0 or more; anything else is refused with no partial write:

```console
$ astroarc session log 0fc81f74 --hour 24 --count 5
error: hour 24 is outside 00-23 (expected 0-23) - nothing was written
```

## 4. Read a session back

```console
$ astroarc session show 0fc81f74
Perseids / Perseus   session 0fc81f74
observation date 2024-08-12 / open / 2 hours logged

┌───────┬───────┬───────┐
│ Hour  │ Count │ Total │
├───────┼───────┼───────┤
│ 21:00 │     4 │     4 │
│ 22:00 │     9 │    13 │
└───────┴───────┴───────┘
total 13 / peak hour 9 at 22:00
```

Totals and peaks are derived at read time, never stored, so an edited hour can never leave a
stale total behind.

## 5. Close a session

```console
$ astroarc session close 0fc81f74
closed  session 0fc81f74 / 2 hours / total 13 - further counts are refused

$ astroarc session log 0fc81f74 --hour 23 --count 3
error: session 0fc81f74 is closed (closed 2026-11-20 22:31 UTC) - counts are refused. Start a new session with: astroarc session start --shower Perseids --date 2024-08-12
```

## 6. Compare maxima across years

```console
$ astroarc compare
Peak meteor count per hour  6 showers / 3 years
┌─────────────┬──────┬──────┬──────┐
│ Shower      │ 2023 │ 2024 │ 2025 │
├─────────────┼──────┼──────┼──────┤
│ Geminids    │   58 │   72 │    — │
│ Leonids     │    — │    — │    — │
│ Lyrids      │    — │   19 │    — │
│ Orionids    │    — │    — │    — │
│ Perseids    │   40 │   65 │   61 │
│ Quadrantids │    — │    — │    — │
└─────────────┴──────┴──────┴──────┘
— means no session with counts in that year
```

- One row per shower, one column per year present in the data.
- Each cell is that shower's **highest single-hour count** in that year.
- An em dash means no session with counts that year - never a `0`, never an error.
- Column widths are computed from the data, so the table stays aligned for 50 showers across
  10 years.
- `astroarc compare Perseids` restricts the table to one shower; unknown names are refused with
  the list of recorded showers.

## 7. The passphrase gate

The first write on a fresh store asks you to create a passphrase (or reads
`ASTROARC_PASSPHRASE`). Later write commands ask again - nothing is cached between runs.
Reads (`showers`, `sessions`, `session show`, `compare`) never prompt.

```console
$ astroarc unlock
saved  passphrase set - write commands now require it (stored as a salted PBKDF2-HMAC-SHA256 hash, never plaintext)

$ ASTROARC_PASSPHRASE=wrong astroarc shower add --name Lyrids --constellation Lyra --peak 2025-04-22
error: passphrase incorrect - nothing was changed
```

One generic message, no hint about the real passphrase, and the data file is left
byte-identical. The passphrase itself is never echoed and never stored in the clear.

## 8. Scripting

Every read command speaks JSON:

```console
$ astroarc compare Perseids --json
{
  "years": [2023, 2024, 2025],
  "rows": [
    {
      "label": "Perseids",
      "shower": "Perseids",
      "peak_date": "2024-08-12",
      "cells": {"2023": 40, "2024": 65, "2025": 61}
    }
  ]
}
```

Exit codes: `0` ok, `1` store or I/O error, `2` usage or validation error, `3` passphrase
refused. Results go to stdout, errors to stderr.

## 9. The store file

`~/.astroarc/store.json` (or `$ASTROARC_DATA`) is one JSON document:

```json
{
  "schema_version": 1,
  "app": "astroarc",
  "profile": {"name": "local observer", "salt": "…", "passphrase_hash": "…", "iterations": 120000, "created_at": "…"},
  "showers": [{"id": "…", "name": "Perseids", "constellation": "Perseus", "peak_date": "2024-08-12", "peak_year": 2024, "created_at": "…"}],
  "sessions": [{"id": "…", "shower_id": "…", "session_date": "2024-08-12", "year": 2024, "started_at": "…", "closed_at": null}],
  "hourly_counts": [{"id": "…", "session_id": "…", "hour": 22, "count": 9, "recorded_at": "…"}]
}
```

Back it up by copying that one file. An interrupted save leaves the previous version intact.
If the file is ever corrupt, AstroArc says so - with its path - and exits non-zero instead of
showing a traceback.
