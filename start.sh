#!/usr/bin/env bash
# FlagPilot: start the web app with one command.
#
#   ./start.sh                  start on http://127.0.0.1:8080 and open the browser
#   ./start.sh --port 9000      use another port
#   ./start.sh --model mock     choose the model (default "auto": Gemini with the repository's keys,
#                               another configured provider if Gemini has no key, else the offline demo model)
#   ./start.sh --restart        restart (after pulling new code)
#   ./start.sh --stop           stop it
#
# Needs only Python 3.10 or newer (and git, to fetch the evaluation challenges on the first run): no packages to
# install. Keys come from shared.env (shipped with this private repository) or from your own .env, which wins.
set -eu

ROOT="$(cd "$(dirname "$0")" && pwd)"
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
    --stop) ACTION=stop; shift ;;
    --restart) ACTION=restart; shift ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (see ./start.sh --help)"; exit 2 ;;
  esac
done

PY=""
for c in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || { echo "FlagPilot needs Python 3.10 or newer: https://www.python.org/downloads/"; exit 1; }

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

# the FlagPilot server(s) on this port: the one this script started, and any other "flagpilot web" listening on it
pids() {
  [ -f "$RUN/web-$PORT.pid" ] && cat "$RUN/web-$PORT.pid"
  if command -v lsof >/dev/null 2>&1; then
    for p in $(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null); do
      ps -o command= -p "$p" 2>/dev/null | grep -q "flagpilot web" && echo "$p"
    done
  fi
}

stop() {
  found=""
  for p in $(pids | sort -u); do kill "$p" 2>/dev/null && found=1; done
  rm -f "$RUN/web-$PORT.pid"
  if [ -z "$found" ]; then echo "FlagPilot is not running on port $PORT."; return; fi
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do up || break; sleep 0.3; done
  if up; then for p in $(pids | sort -u); do kill -9 "$p" 2>/dev/null; done; sleep 0.5; fi
  if up; then echo "Port $PORT is still answering (not a FlagPilot started here?)"; exit 1; fi
  echo "FlagPilot on port $PORT stopped."
}

if [ "$ACTION" = stop ]; then stop; exit 0; fi
if [ "$ACTION" = restart ]; then stop; fi

if up; then
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
