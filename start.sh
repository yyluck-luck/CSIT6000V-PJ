#!/usr/bin/env bash
# FlagPilot: start the web app with one command.
#
#   ./start.sh                  start on http://127.0.0.1:8080 and open the browser
#   ./start.sh --port 9000      use another port (to run a second copy next to the first)
#   ./start.sh --model mock     choose the model (default "auto": Gemini with the repository's keys,
#                               another configured provider if Gemini has no key, else the offline demo model)
#   ./start.sh --check          check that every model provider answers with this copy's keys (no server started)
#   ./start.sh --restart        restart (after pulling new code)
#   ./start.sh --stop           stop it
#
# Needs only Python 3.11 or newer (and git, to fetch the evaluation challenges on the first run): no packages to
# install. Keys come from shared.env (shipped with this private repository) or from your own .env, which wins.
# --stop and --restart only touch the FlagPilot started from this folder, never another copy's.
set -eu

ROOT="$(cd "$(dirname "$0")" && pwd -P)"
APP="$ROOT/flagpilot"
RUN="$APP/.run"
PORT=8080
MODEL=auto
OPEN=1
ACTION=start

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="${2:?--port needs a number}"; shift 2 ;;
    --model) MODEL="${2:?--model needs a name}"; shift 2 ;;
    --no-open) OPEN=0; shift ;;
    --check) ACTION=check; shift ;;
    --stop) ACTION=stop; shift ;;
    --restart) ACTION=restart; shift ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (see ./start.sh --help)"; exit 2 ;;
  esac
done

PY=""
for c in python3 python3.14 python3.13 python3.12 python3.11 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || { echo "FlagPilot needs Python 3.11 or newer: https://www.python.org/downloads/"; exit 1; }

if [ "$ACTION" = check ]; then cd "$APP" && exec "$PY" scripts/check_apis.py; fi

URL="http://127.0.0.1:$PORT"
mkdir -p "$RUN"

# is FlagPilot answering on this port? (straight to localhost, ignoring any proxy settings)
up() {
  "$PY" - "$URL/api/tasks" <<'PY' 2>/dev/null
import sys, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    opener.open(sys.argv[1], timeout=2).read(1)
except Exception:
    sys.exit(1)
PY
}

is_flagpilot() { ps -o command= -p "$1" 2>/dev/null | grep -q "flagpilot web"; }
dir_of() { lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1; }

# the folder of the FlagPilot listening on this port, whichever copy it belongs to (empty: none, or no lsof)
listener_dir() {
  command -v lsof >/dev/null 2>&1 || return 0
  for p in $(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null); do
    if is_flagpilot "$p"; then dir_of "$p"; return 0; fi
  done
}

# this copy's FlagPilot on this port: the one this script started, and one started by hand from this folder
pids() {
  if [ -f "$RUN/web-$PORT.pid" ]; then
    p="$(cat "$RUN/web-$PORT.pid")"
    if kill -0 "$p" 2>/dev/null && is_flagpilot "$p"; then echo "$p"; fi
  fi
  if command -v lsof >/dev/null 2>&1; then
    for p in $(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null); do
      if is_flagpilot "$p" && [ "$(dir_of "$p")" = "$APP" ]; then echo "$p"; fi
    done
  fi
}

stop() {
  mine="$(pids | sort -u)"
  rm -f "$RUN/web-$PORT.pid"
  if [ -z "$mine" ]; then
    other="$(listener_dir)"
    if [ -n "$other" ]; then echo "The FlagPilot on port $PORT belongs to another copy (${other%/flagpilot}); it was left running."
    else echo "FlagPilot is not running on port $PORT."; fi
    return 0
  fi
  for p in $mine; do kill "$p" 2>/dev/null || true; done
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do up || break; sleep 0.3; done
  if up; then for p in $mine; do kill -9 "$p" 2>/dev/null || true; done; sleep 0.5; fi
  if up; then echo "Port $PORT is still answering."; exit 1; fi
  echo "FlagPilot on port $PORT stopped."
}

if [ "$ACTION" = stop ]; then stop; exit 0; fi
if [ "$ACTION" = restart ]; then stop; fi

if up; then
  other="$(listener_dir)"
  if [ -n "$other" ] && [ "$other" != "$APP" ]; then
    echo "Port $PORT is already used by FlagPilot from another copy: ${other%/flagpilot}"
    echo "To run this copy as well, pick another port:  ./start.sh --port $((PORT + 10))"
    exit 1
  fi
  echo "FlagPilot is already running: $URL   (./start.sh --restart to restart it)"
else
  # first run on a fresh clone: fetch the evaluation challenges once (they are not stored in the repository)
  if [ -z "${FLAGPILOT_TASKS:-}" ] && [ ! -f "$APP/tasks/index/tasks.jsonl" ]; then
    echo "First run: fetching the evaluation challenges (InterCode-CTF, about 100 MB, only this once)..."
    if ! "$PY" "$APP/scripts/fetch_taskset.py"; then
      echo "Starting with the 3 bundled demo challenges instead; run ./start.sh --restart later to try again."
      export FLAGPILOT_TASKS="$APP/demo-tasks"
    fi
  fi
  # the evaluation's run records ship in flagpilot/results/tacc; Runs and the Lab read them from runs/tacc
  if [ -d "$APP/results/tacc" ]; then
    mkdir -p "$APP/runs/tacc"
    for d in "$APP/results/tacc"/*/; do
      n="$(basename "$d")"
      [ -e "$APP/runs/tacc/$n" ] || cp -R "$d" "$APP/runs/tacc/$n"
    done
  fi
  set -- --host 127.0.0.1 --port "$PORT" --model "$MODEL"
  [ -d "$APP/runs/tacc" ] && set -- "$@" --replay runs/tacc        # finished TACC runs show up in Runs and the Lab
  ( cd "$APP" || exit 1
    nohup "$PY" -m flagpilot web "$@" > "$RUN/web-$PORT.log" 2>&1 &       # $! is the server itself
    echo $! > "$RUN/web-$PORT.pid" )
  i=0
  until up; do
    i=$((i + 1))
    if [ "$i" -gt 60 ] || ! kill -0 "$(cat "$RUN/web-$PORT.pid")" 2>/dev/null; then
      echo "FlagPilot did not start. The end of flagpilot/.run/web-$PORT.log:"
      tail -n 20 "$RUN/web-$PORT.log"
      exit 1
    fi
    sleep 0.5
  done
  echo "FlagPilot is running: $URL"
  echo "  model: $(grep -m1 -o 'model=[^ ]*' "$RUN/web-$PORT.log" | cut -d= -f2)   log: flagpilot/.run/web-$PORT.log   stop: ./start.sh --stop"
fi

if [ "$OPEN" = 1 ]; then
  if command -v open >/dev/null 2>&1; then open "$URL"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true
  fi
fi
