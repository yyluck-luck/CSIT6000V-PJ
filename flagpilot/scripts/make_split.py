#!/usr/bin/env python3
"""生成按类别分层、可复现的 dev / test 划分。

用法：
    python3 scripts/make_split.py --root "$FLAGPILOT_TASKS" --seed 0 --test-frac 0.5
默认写到 <root>/index/splits.json（与题库放在一起；文件里记了索引的 sha256）。

划分是团队决策：生成后状态是 "proposed"。确认后把 status 改成 "frozen"，
并在报告里写明 seed、test_frac 和文件哈希——之后就不能再动了。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import splits  # noqa: E402
from flagpilot.tasks import load_index  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, help="题库根目录（默认 FLAGPILOT_TASKS / tasks/）")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--test-frac", type=float, default=0.5)
    ap.add_argument("--out", type=Path, help="输出路径（默认 <root>/index/splits.json）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的划分文件")
    args = ap.parse_args()

    root = args.root.resolve() if args.root else None
    tasks = load_index(root)
    split = splits.make_split(tasks, seed=args.seed, test_frac=args.test_frac,
                              index_sha=splits.index_sha256(root))
    out = args.out or splits.default_split_path(root)
    if out.exists() and not args.force:
        old = json.loads(out.read_text(encoding="utf-8"))
        if old.get("status") == "frozen":
            print(f"拒绝覆盖已冻结的划分：{out}（确需重做请先和团队确认，再用 --force）")
            return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(split, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"写入 {out}")
    print(f"seed={split['seed']} test_frac={split['test_frac']} "
          f"dev={len(split['dev'])} test={len(split['test'])}")
    for cat, c in split["counts"].items():
        print(f"  {cat:<16} dev={c['dev']:<3} test={c['test']}")
    print(f"index sha256={split['index_sha256'][:16]}…  status={split['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
