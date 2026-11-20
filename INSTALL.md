# Installing AstroArc

AstroArc is a single Python file plus a console-script wrapper. Everything below runs as an
unprivileged user; nothing needs `sudo`, a system package manager or a database server.

## Prerequisites (installed by you, not by the script)

| Requirement | Why | Check |
| --- | --- | --- |
| Python 3.9 or newer | the tool and the test suite | `python3 --version` |
| `pip` for that Python | installs the console script and pytest | `python3 -m pip --version` |

No compiler, no system libraries, no network access at runtime.

## 1. Install

```bash
bash install.sh
```

This runs `pip install --upgrade pip setuptools wheel`, installs `requirements.txt` (the test
runner), installs AstroArc in editable mode so the `astroarc` command lands on your `PATH`,
verifies the module imports, and **exits** without starting anything in the foreground.

Prefer to do it by hand? The same four steps:

```bash
python3 -m pip install --upgrade pip setuptools wheel
python3 -m pip install -r requirements.txt
python3 -m pip install --no-build-isolation -e .
python3 astroarc.py --version
```

You can also skip installing entirely: `python3 astroarc.py ...` works from the checkout.

## 2. Seed / demo data

There is **no separate seed or migrate step**. AstroArc creates its versioned JSON store on
the first command that runs, and every write command creates the rows it needs.

To populate a throwaway store with realistic data for a demonstration:

```bash
bash demo.sh
```

This writes `demo-data/store.json` (override with `ASTROARC_DEMO_DIR`) and never touches
`~/.astroarc/store.json`. Re-running it rebuilds the demo store from scratch, so it is always
repeatable.

## 3. Run

```bash
astroarc --help
astroarc showers
```

Point AstroArc at a different store whenever you like:

```bash
ASTROARC_DATA=/path/to/store.json astroarc showers
astroarc --data /path/to/store.json showers
```

Write commands need the local passphrase: on a terminal they prompt once per command; for
scripts export `ASTROARC_PASSPHRASE` or pass `--passphrase`. The demo store uses the standard
demo credential `cenius`.

## 4. Test

```bash
python3 -m pytest
```

The suite is self-contained: every test creates its own temporary store, no network, no
external services, no prompts.

## Uninstall

```bash
python3 -m pip uninstall astroarc
rm -rf ~/.astroarc        # only if you want to delete your observation log
```
