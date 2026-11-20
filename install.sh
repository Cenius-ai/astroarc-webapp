#!/usr/bin/env bash
# AstroArc setup - installs the tool and its test runner, then EXITS.
# Runs as an unprivileged user: no root, no system package manager, no service.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

# 1. modern tooling, so editable installs behave on any pip 22+
"${PYTHON}" -m pip install --upgrade pip setuptools wheel

# 2. dependencies (the test runner; AstroArc itself is stdlib only)
"${PYTHON}" -m pip install -r requirements.txt

# 3. the console script: `astroarc` on PATH, plus `python3 astroarc.py`
"${PYTHON}" -m pip install --no-build-isolation -e .

# 4. cheap self-checks - an incomplete environment fails here, not at boot
"${PYTHON}" -c "import astroarc; print('astroarc', astroarc.__version__, 'imports cleanly')"
"${PYTHON}" astroarc.py --version

echo
echo "AstroArc is installed."
echo "  astroarc --help        # every command, with an example line"
echo "  bash demo.sh           # non-destructive end-to-end walkthrough"
echo "  python3 -m pytest      # the test suite"
