#!/usr/bin/env bash
# AstroArc demo - a complete, non-destructive walkthrough of the tool.
#
#   bash demo.sh
#
# It builds its own throwaway JSON store (never your real ~/.astroarc data),
# records a few showers, logs hourly counts for six sessions across three
# years, and prints the cross-year comparison table. Re-running it rebuilds
# the demo store from scratch, so the recording is always identical.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
DEMO_DIR="${ASTROARC_DEMO_DIR:-demo-data}"
DEMO_PASSPHRASE="cenius"

rm -rf "$DEMO_DIR"
mkdir -p "$DEMO_DIR"
export ASTROARC_DATA="$DEMO_DIR/store.json"
export ASTROARC_PASSPHRASE="$DEMO_PASSPHRASE"

LAST=""

# run: show the exact command, then run it (tolerates a non-zero exit so the
# demo can show a refused write without aborting).
run() {
  printf '\n\033[2m$\033[0m astroarc %s\n' "$*"
  set +e
  "${PYTHON}" astroarc.py "$@"
  status=$?
  set -e
  if [ "$status" -ne 0 ]; then
    printf '(exit %s)\n' "$status"
  fi
  return 0
}

# run_refused: a write that the tool is expected to reject - stderr is merged so
# the message shows up in the recording, and the demo keeps going.
run_refused() {
  printf '\n\033[2m$\033[0m astroarc %s\n' "$*"
  set +e
  "${PYTHON}" astroarc.py "$@" 2>&1
  status=$?
  set -e
  printf '(refused, exit %s - nothing was written)\n' "$status"
  return 0
}

# capture: run a command and remember its stdout so an id can be pulled out.
capture() {
  printf '\n\033[2m$\033[0m astroarc %s\n' "$*"
  LAST="$("${PYTHON}" astroarc.py "$@")"
  printf '%s\n' "$LAST"
}

last_session_id() {
  printf '%s\n' "$LAST" \
    | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' \
    | head -n 1
}

printf 'AstroArc %s - meteor shower log\n' "$("${PYTHON}" astroarc.py --version | awk '{print $2}')"
printf 'demo store: %s  (your real data is untouched)\n' "$ASTROARC_DATA"

# --- empty states ---------------------------------------------------------
run showers
run sessions
run compare

# --- 1. record showers ----------------------------------------------------
run shower add --name Perseids --constellation Perseus --peak 2024-08-12
run shower add --name Geminids --constellation [redacted] --peak 2024-12-14
run shower add --name Lyrids --constellation Lyra --peak 2024-04-22
run shower add --name Quadrantids --constellation Bootes --peak 2024-01-04
run shower add --name Orionids --constellation Orion --peak 2024-10-21
run shower add --name Leonids --constellation Leo --peak 2024-11-17
run showers

# --- 2. validation refuses bad input -------------------------------------
run_refused shower add --name Draconids --constellation Draco --peak 2024-13-40
run_refused shower add --name Perseids --constellation Perseus --peak 2024-08-12

# --- 3. Perseids 2023 -----------------------------------------------------
capture session start --shower Perseids --date 2023-08-12
sid="$(last_session_id)"
run session log "$sid" --hour 21 --count 18
run session log "$sid" --hour 22 --count 31
run session log "$sid" --hour 23 --count 40
run session log "$sid" --hour 0 --count 27
run session log "$sid" --hour 1 --count 12
run session log "$sid" --hour 22 --count 34
run_refused session log "$sid" --hour 24 --count 5
run session show "$sid"
run session close "$sid"
run_refused session log "$sid" --hour 2 --count 6

# --- 4. Perseids 2024 and 2025 -------------------------------------------
capture session start --shower Perseids --date 2024-08-12
sid="$(last_session_id)"
run session log "$sid" --hour 22 --count 52
run session log "$sid" --hour 23 --count 65
run session log "$sid" --hour 0 --count 48
run session log "$sid" --hour 1 --count 21
run session close "$sid"

capture session start --shower Perseids --date 2025-08-12
sid="$(last_session_id)"
run session log "$sid" --hour 23 --count 57
run session log "$sid" --hour 0 --count 61
run session close "$sid"

# --- 5. Geminids and Lyrids ----------------------------------------------
capture session start --shower Geminids --date 2023-12-14
sid="$(last_session_id)"
run session log "$sid" --hour 1 --count 44
run session log "$sid" --hour 2 --count 58
run session log "$sid" --hour 3 --count 39
run session close "$sid"

capture session start --shower Geminids --date 2024-12-14
sid="$(last_session_id)"
run session log "$sid" --hour 2 --count 72
run session log "$sid" --hour 3 --count 64
run session log "$sid" --hour 4 --count 12
run session close "$sid"

capture session start --shower Lyrids --date 2024-04-22
sid="$(last_session_id)"
run session log "$sid" --hour 3 --count 19
run session log "$sid" --hour 4 --count 11

# --- 6. the payoff: cross-year comparison --------------------------------
run sessions
run compare
run compare Perseids
run compare Geminids --json

# --- 7. help --------------------------------------------------------------
run --help

printf '\nDone. The demo store is at %s - delete it with: rm -rf %s\n' "$ASTROARC_DATA" "$DEMO_DIR"
