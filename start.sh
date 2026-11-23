#!/usr/bin/env bash
# FlagPilot: start the web app with one command.
#
#   ./start.sh                  start on http://127.0.0.1:8080 and open the browser
#   ./start.sh --port 9000      use another port (to run a second copy next to the first)
#   ./start.sh --model mock     choose the model (default "auto": Gemini with a configured key,
#                               another configured provider if Gemini has no key, else the offline demo model)
#   ./start.sh --check          check that every model provider answers with this copy's keys (no server started)
#   ./start.sh --restart        restart (after pulling new code)
#   ./start.sh --stop           stop it (and end its Qwen session on TACC, which would otherwise hold two GPUs for up to an hour)
#
# Needs only Python 3.11 or newer (and git, to fetch the evaluation challenges on the first run): no packages to
# install. Hosted model keys come from the environment or your local .env.
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

# end this copy's Qwen session on TACC before stopping it (--restart keeps the session: the new server picks it up)
end_tacc() {
  "$PY" - "$URL" <<'PY' 2>/dev/null
import json, sys, time, urllib.request
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
base = sys.argv[1]
def phase():
    return json.loads(op.open(base + "/api/tacc/session", timeout=10).read()).get("phase")
try:
    if phase() not in ("starting", "ready"):
        sys.exit(0)
    print("Ending the Qwen session on TACC (cancelling its GPU job)...", flush=True)
    req = urllib.request.Request(base + "/api/tacc/session/stop", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
    op.open(req, timeout=180).read()
    t = time.time()
    while time.time() - t < 90 and phase() not in ("idle", "failed"):
        time.sleep(2)
except Exception:
    sys.exit(1)
PY
}

# Is pid $1 this copy's web server? Deliberately not `ps -o command=`: ps is refused in
# sandboxes and some CI images ("operation not permitted"), and then pids() finds nothing -
# --stop cannot stop the server while the port still answers, and --restart prints both
# "not running" and "already running". A listener on our port whose cwd is this copy is the
# same statement without ps.
is_flagpilot() {
  lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null | grep -qx "$1" || return 1
  [ "$(dir_of "$1")" = "$APP" ]
}
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
  if [ "$ACTION" = stop ] && up; then end_tacc || true; fi
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
  # Qwen-on-TACC needs the course SDK. It is not searched for (that guesses, and can cross
  # permission boundaries on someone else's machine): the page asks for its path the first time,
  # and remembers it in runs/tacc/tcloud.txt, so this note only appears until that has happened.
  if ! command -v "${TCLOUD_BIN:-tcloud}" >/dev/null 2>&1 && [ ! -x "$(cat "$APP/runs/tacc/tcloud.txt" 2>/dev/null)" ]; then
    echo "Note: tcloud (the course SDK) is not on PATH; the page will ask for its path on the first connect."
  fi
  # The server is put in its own session, not merely behind nohup: nohup only ignores SIGHUP,
  # while the child stays in the caller's process group. Whatever tears that group down - an
  # agent's shell, a CI step, a terminal closed while the group is still attached - takes the
  # server with it a moment after "FlagPilot is running" was printed.
  #
  # The other half is the environment. A caller that wraps Python (a sandbox, an audit or
  # coverage hook) leaves a sitecustomize on PYTHONPATH; inherited by a long-lived server, every
  # open() goes through that caller's broker, and the day the caller exits the server keeps
  # listening but answers nothing ("_BrokerProtocolError: execution attempt 不存在或已关闭",
  # seen from httpcache.load). A server that outlives its caller must not inherit such a hook,
  # so the shim entry is dropped here and the brokered-fs switch is turned off.
  ( cd "$APP" || exit 1
    "$PY" - "$RUN/web-$PORT.log" "$RUN/web-$PORT.pid" "$PY" "$@" <<'PY'
import os, subprocess, sys

log, pidfile, py, args = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
env = dict(os.environ)


def drop_caller_hooks(env):
    """Remove what belongs to whoever launched us; nobody else's paths are touched."""
    dropped, kept = [], []
    for part in (env.get("PYTHONPATH") or "").split(os.pathsep):
        if part and os.path.exists(os.path.join(part, "sitecustomize.py")):
            dropped.append(part)
        elif part:
            kept.append(part)
    if not dropped:
        return env
    env["PYTHONPATH"] = os.pathsep.join(kept)
    if not kept:
        del env["PYTHONPATH"]
    # The same shim usually also sits in front of PATH (it wraps every executable and asks the
    # caller's broker for permission - "Brokered program policy check unavailable" once that
    # caller is gone, which is what stopped `tcloud submit`). Drop those entries with it.
    env["PATH"] = os.pathsep.join(
        p for p in (env.get("PATH") or "").split(os.pathsep)
        if p and not any(p == d or p.startswith(d + os.sep) for d in dropped))
    for k in [k for k in env if k.startswith("CODEBUDDY_SANDBOX_BROKER_")
              or k.startswith("CODEBUDDY_BROKERED_")
              or k in ("CODEBUDDY_SANDBOX_PROGRAM_POLICY_COMMAND", "SANDBOX_CENTER_IPC_ADDRESS",
                       "TOYBOX_SANDBOX_SOCK", "CODEBUDDY_TOYBOX_BIN")]:
        del env[k]
    return env


env = drop_caller_hooks(env)
with open(log, "ab", buffering=0) as out:
    proc = subprocess.Popen([py, "-m", "flagpilot", "web", *args], stdin=subprocess.DEVNULL,
                            stdout=out, stderr=subprocess.STDOUT, env=env,
                            start_new_session=True)
open(pidfile, "w").write(f"{proc.pid}\n")
PY
  )
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
