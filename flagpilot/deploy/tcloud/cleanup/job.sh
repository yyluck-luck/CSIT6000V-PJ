#!/usr/bin/env bash
# FlagPilot: free space in the TACC home directory (a CPU job of a few seconds).
#
# 2026-11-21: /mnt/home ran out of space in the middle of the v7 batch ("No space left on device"), and after that even
# submitting a job failed (the SDK could not upload its WORKDIR). The SDK has no delete command, so this job deletes on a
# compute node. Submitted as deploy/tcloud/submit.sh cleanup; it reuses the client WORKDIR (flagpilot-client), whose
# unpacked payload from the last batch (challenges, vendored libraries) is replaced by these few files.
#
#   FP_DROP   whole batches to delete (space separated; their records are kept locally or in the repository)
#   every other attempt keeps only its records, as scripts/export_records.py does: trace.jsonl, writeup.md and
#   replay/solve.py; its working directory and the copies of the challenge made for the replay checks go.
#   FP_PRUNE_LOGS=1  also delete job logs in slurm_log older than two days (off by default: they are small, and old
#   runs may still be read from them). The course model's compile cache stays (it lets the server start in ~5 min).
set -u
: "${TACC_USERDIR:?Submit with tcloud submit}"
cd "${TACC_WORKDIR:?Submit with tcloud submit}" || exit 1
# shellcheck disable=SC1091
source ./run.env
R="$TACC_USERDIR/flagpilot-runs"
echo "[fp] cleanup job=${SLURM_JOB_ID:-} node=$(hostname)"
df -h "$TACC_USERDIR" 2>/dev/null | sed 's/^/[fp] df-before /'
for b in ${FP_DROP:-}; do
  [[ "$b" =~ ^[A-Za-z0-9._-]+$ ]] || continue
  [ -e "$R/$b" ] || [ -e "$R/$b.md" ] || continue
  chmod -R u+w "$R/$b" 2>/dev/null
  rm -rf -- "${R:?}/$b" "${R:?}/$b.md" && echo "[fp] dropped $b"
done
n=0
for a in "$R"/*/*/; do
  [ -f "$a/trace.jsonl" ] || continue
  chmod -R u+w "$a" 2>/dev/null       # the sandbox leaves challenge copies read-only
  find "$a" -mindepth 1 -maxdepth 1 ! -name trace.jsonl ! -name writeup.md ! -name replay -exec rm -rf -- {} + 2>/dev/null
  [ -d "$a/replay" ] && find "$a/replay" -mindepth 1 -maxdepth 1 ! -name solve.py -exec rm -rf -- {} + 2>/dev/null
  n=$((n + 1))
done
echo "[fp] pruned $n attempts to their records"
if [ "${FP_PRUNE_LOGS:-0}" = 1 ]; then
  find "$TACC_USERDIR/slurm_log" -type f -mtime +2 -delete 2>/dev/null && echo "[fp] deleted job logs older than two days"
else
  echo "[fp] slurm_log kept"
fi
du -sh "$TACC_USERDIR"/* "$R" 2>/dev/null | sed 's/^/[fp] du-after /'
df -h "$TACC_USERDIR" 2>/dev/null | sed 's/^/[fp] df-after /'
echo "[fp] done: cleanup"
