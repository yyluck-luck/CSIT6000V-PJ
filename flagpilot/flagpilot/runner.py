"""批量运行器：并发闸门 + 断点续跑 + 分片。

三个都是 proposal 里被算力账逼出来的必需品，不是优化：
  * 2 小时作业上限 → 必须能续跑
  * 单作业 4 并发闸门 → 必须限流，否则 vLLM 排队把 TTFT 打爆
  * 一份作业跑不完 → 必须能按分片切
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import gpu, schedule, storage
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
        for r in storage.read_records(self.results_path):
            if r.get("run_id") and r.get("verdict"):
                self._done.add(r["run_id"])

    def _append(self, rec: dict) -> None:
        with self._lock:
            storage.append_record(self.results_path, rec)

    def run(self, tasks: list[Task], resume: bool = True, hook=None, order: str = "lpt") -> list[dict]:
        jobs = [(t, s) for s in range(self.cfg.seeds) for t in tasks]
        if resume:
            jobs = [(t, s) for (t, s) in jobs if f"{t.name}-s{s}" not in self._done]

        if not jobs:
            print("[runner] 没有待跑的 attempt（已全部完成）")
            return []

        sched = "提交顺序"
        if order == "lpt" and len(jobs) > self.workers:
            # 历史来自同级批次目录（runs/<batch>/runs.jsonl）：同一台机器、同一套题，量级最可信
            est = schedule.Estimator(schedule.load_history([self.dir.parent]))
            jobs = schedule.lpt_order(jobs, est)
            sched = f"最长预计优先（LPT，参考 {est.n} 条历史）"
        print(f"[runner] 待跑 {len(jobs)} 个 attempt，并发 {self.workers}，调度：{sched}")
        out: list[dict] = []
        t0 = time.monotonic()
        t_start = time.time()           # GPU 账本按墙上时钟对齐 nvidia-smi 采样

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
        self._record_gpu(t_start, time.time(), len(out))
        return out

    def _record_gpu(self, t_start: float, t_end: float, attempts: int) -> None:
        """Track L 成本：把本次运行占用的 GPU 秒记进批次账本（续跑会累加）。

        只在作业里检测到 GPU 时才记——本地没有卡就不写，报告显示"—"而不是 0。
        利用率采样文件由 run.slurm 通过 FP_GPU_UTIL_CSV 传进来，按本次运行的时间窗切片。
        """
        n = gpu.detect_gpus()
        if n <= 0:
            return
        wall = max(0.0, t_end - t_start)
        csv = os.environ.get("FP_GPU_UTIL_CSV")
        busy = gpu.busy_gpu_seconds(csv, t_start, t_end) if csv else None
        gpu.append_ledger(self.dir, {
            "job_id": os.environ.get("SLURM_JOB_ID") or "local",
            "gpus": n,
            "started": round(t_start, 3),
            "finished": round(t_end, 3),
            "wall_s": round(wall, 2),
            "gpu_seconds": round(n * wall, 1),
            "busy_gpu_seconds": round(busy, 1) if busy is not None else None,
            "attempts": attempts,
            "workers": self.workers,
        })

    def load_results(self) -> list[dict]:
        if not self.results_path.exists():
            return []
        return storage.read_records(self.results_path)
