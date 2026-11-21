#!/usr/bin/env bash
# Run the course's vLLM serve script in its own process group, and stop the whole group (API server and GPU
# workers) whenever this job ends: vLLM exits by itself, the job is cancelled, or it reaches its time limit.
# Same idea as the course's 2026-11 vLLM cleanup update for cinecontext-qwen-e2e: no worker may keep GPU memory
# after the job is gone. Stopping: SIGTERM to the group, up to FP_STOP_GRACE seconds (default 25, inside Slurm's
# 30 s kill wait) to exit, then SIGKILL. GPU memory after the stop goes into the job log as evidence.
#
#   bash supervise.sh <command> [args...]
set -u
set -m                                   # job control: the background command gets its own process group
"$@" &
pid=$!
echo "SUPERVISE started pid=$pid pgid=$pid"

group_alive() { kill -0 -- "-$pid" 2>/dev/null; }

stop() {
  trap - EXIT TERM INT
  if group_alive; then
    kill -TERM -- "-$pid" 2>/dev/null
    n=0
    while group_alive && [ "$n" -lt $(( ${FP_STOP_GRACE:-25} * 5 )) ]; do sleep 0.2; n=$((n + 1)); done
    if group_alive; then
      echo "SUPERVISE group $pid still running after ${FP_STOP_GRACE:-25} s: SIGKILL"
      kill -KILL -- "-$pid" 2>/dev/null
      sleep 1
    fi
  fi
  if group_alive; then echo "SUPERVISE_FAIL processes of group $pid are still alive"; else echo "SUPERVISE stopped group $pid"; fi
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=index,memory.used --format=csv,noheader 2>/dev/null | sed "s/^/SHUTDOWN gpu $(hostname) /"
  fi
}

trap 'stop' EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
wait "$pid"
