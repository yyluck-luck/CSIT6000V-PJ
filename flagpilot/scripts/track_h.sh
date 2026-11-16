#!/usr/bin/env bash
# Track H 一命令：用 DeepSeek V4.1（deepseek-flash）跑一个批次，并导出报告。
#
#   DSV41_API_KEY=sk-... scripts/track_h.sh h-dev --limit 5 --workers 4
#   scripts/track_h.sh h-smoke --tasks general-004 --workers 1
#
# 模型、端点、价目表由 `--track H` 自动补齐（见 flagpilot/config.py 的 TRACK_H）。
# 密钥按顺序找 DSV41_API_KEY、DEEPSEEK_API_KEY；本目录下有 .env 会先载入（已 gitignore）。
# 托管 API 不需要 GPU，在笔记本上跑即可；按官方价目计费（高峰价翻倍，见 pricing.py）。
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

BATCH="${1:-h-dev}"
[[ $# -gt 0 ]] && shift

python3 -m flagpilot batch --track H --batch "${BATCH}" "$@"
OUT="${FLAGPILOT_RUNS:-runs}"   # 报告跟批次放在一起（FLAGPILOT_RUNS 改了运行目录也一样）
mkdir -p "${OUT}"
python3 -m flagpilot report --batch "${BATCH}" --md "${OUT}/${BATCH}.md"
