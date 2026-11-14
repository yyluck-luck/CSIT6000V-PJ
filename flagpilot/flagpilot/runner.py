"""批量运行器：并发闸门 + 断点续跑 + 分片。

三个都是 proposal 里被算力账逼出来的必需品，不是优化：
  * 2 小时作业上限 → 必须能续跑
  * 单作业 4 并发闸门 → 必须限流，否则 vLLM 排队把 TTFT 打爆
  * 一份作业跑不完 → 必须能按分片切
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .agent import run_attempt
from .config import RunConfig
from .tasks import Task


class BatchRunner:
    def __init__(self, cfg: RunConfig, batch_dir: Path, workers: int = 4):
        self.cfg = cfg
        self.dir = Path(batch_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.workers = max(1, int(workers))
        self.results_path = self.dir / "runs.jsonl"
        self._lock = threading.Lock()
        self._done: set[str] = set()
        self._load_done()

    def _load_done(self) -> None:
        if not self.results_path.exists():
            return
        for line in self.results_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("run_id") and r.get("verdict"):
                self._done.add(r["run_id"])

    def _append(self, rec: dict) -> None:
        with self._lock:
            with self.results_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def run(self, tasks: list[Task], resume: bool = True, hook=None) -> list[dict]:
        jobs = [(t, s) for s in range(self.cfg.seeds) for t in tasks]
        if resume:
            jobs = [(t, s) for (t, s) in jobs if f"{t.name}-s{s}" not in self._done]

        if not jobs:
            print("[runner] 没有待跑的 attempt（已全部完成）")
            return []

        print(f"[runner] 待跑 {len(jobs)} 个 attempt，并发 {self.workers}")
        out: list[dict] = []
        t0 = time.monotonic()

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futs = {
                pool.submit(run_attempt, task, self.cfg, self.dir, seed, hook): (task, seed)
                for task, seed in jobs
            }
            for i, fut in enumerate(as_completed(futs), 1):
                task, seed = futs[fut]
                try:
                    summary = fut.result()
                except Exception as e:  # 单个 attempt 崩了不能拖垮整批
                    summary = {
                        "verdict": "error",
                        "reason": f"exception: {type(e).__name__}: {e}",
                        "turns": 0, "wall_s": 0, "total_tokens": 0, "cost_usd": 0,
                        "run_dir": "",
                    }
                rec = {
                    "run_id": f"{task.name}-s{seed}",
                    "task": task.name,
                    "task_id": task.task_id,
                    "category": task.category,
                    "seed": seed,
                    # 并发时服务端计数器被多个 attempt 分摊，缓存命中率会失真。
                    # 把并发度一起记下来，报告里才能判断那一列可不可信。
                    "workers": self.workers,
                    **summary,
                }
                self._append(rec)
                out.append(rec)
                flag = {"verified": "VERIFIED", "unverified": "unverified",
                        "wrong": "wrong", "budget": "budget", "error": "ERROR"}.get(
                    rec["verdict"], rec["verdict"])
                print(f"[{i}/{len(jobs)}] {rec['run_id']:<22} {flag:<10} "
                      f"turns={rec.get('turns')} {rec.get('wall_s')}s")

        print(f"[runner] 完成 {len(out)} 个，用时 {time.monotonic() - t0:.1f}s")
        return out

    def load_results(self) -> list[dict]:
        if not self.results_path.exists():
            return []
        out = []
        for line in self.results_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out
