"""边生成边看：run_attempt(on_delta=...) 把模型逐字输出交给界面，但对评测零影响。

两条硬要求：
  1. 回调拿到的 (turn, chunk) 能拼回每一轮的完整回复，轮次与 trace 里的 model 事件一一对应；
  2. 传不传 on_delta，trace 里的事件序列与结论完全一样（逐字输出不落盘、不进 messages）。
另外确认 Web 端把它接上了：一次真实运行结束后，服务端的 partial 缓冲里是最后一轮的完整文本。
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import agent, config  # noqa: E402
from flagpilot.client import Completion, ServerMetrics  # noqa: E402
from flagpilot.config import Budget, ModelSpec, RunConfig, SandboxLimits  # noqa: E402
from flagpilot.tasks import Task  # noqa: E402

FLAG = "picoCTF{live_stream_0123456789}"
SCRIPT = ("import base64\n"
          "for line in open('challenge/data.txt'):\n"
          "    if 'encoded=' in line:\n"
          "        print(base64.b64decode(line.split('=', 1)[1]).decode())\n")
REPLIES = [
    json.dumps({"thought": "look", "action": "shell", "cmd": "cat challenge/data.txt"}),
    json.dumps({"thought": "decode", "action": "submit", "flag": FLAG, "script": SCRIPT}),
]


def make_task(root: Path) -> Task:
    d = root / "tasks" / "live-001"
    d.mkdir(parents=True, exist_ok=True)
    enc = base64.b64encode(FLAG.encode()).decode()
    (d / "data.txt").write_text(f"header\nencoded={enc}\n", encoding="utf-8")
    return Task(task_id=1, name="live-001", category="Synthetic", title="live", query="decode",
                gold_sha256=hashlib.sha256(FLAG.encode()).hexdigest(), assets=["data.txt"],
                assets_dir="tasks/live-001", suggested_tools=[], scope="core")


class ChunkingClient:
    """把每条回复切成三段逐段回调——和真实 SSE 流一样，回调发生在 complete 返回之前。"""

    def __init__(self):
        self.replies = list(REPLIES)

    def complete(self, messages, max_tokens=None, on_delta=None):
        text = self.replies.pop(0) if self.replies else REPLIES[0]
        if on_delta is not None:
            k = max(1, len(text) // 3)
            for i in range(0, len(text), k):
                on_delta(text[i:i + k])
        return Completion(text=text, prompt_tokens=10, completion_tokens=5, ttft_ms=1, latency_ms=2)

    def scrape_metrics(self):
        return ServerMetrics()


VOLATILE = {"ts", "t_ms", "duration_ms", "latency_ms", "ttft_ms", "wall_s", "model_s", "tool_s", "setup_s",
            "tail_s", "total_s", "replay_s", "run_dir", "caps", "elapsed_s", "obs_head", "stderr_head",
            "detail", "workdir", "time_s", "brief"}


def shape(v):
    """递归去掉时间与路径，只比"发生了什么"。"""
    if isinstance(v, dict):
        return {k: shape(x) for k, x in v.items()
                if k not in VOLATILE and k != "ms" and not k.endswith(("_ms", "_s", "_dir", "_path"))}
    if isinstance(v, list):
        return [shape(x) for x in v]
    return v


class TestOnDelta(unittest.TestCase):
    def attempt(self, on_delta):
        tmp = Path(tempfile.mkdtemp(prefix="fp-live-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cfg = RunConfig(model=ModelSpec(name="mock"), budget=Budget(max_turns=6, max_wall_s=120),
                        limits=SandboxLimits(action_timeout_s=10), replay_timeout_s=20.0, tasks_root=tmp)
        original = agent.build_client
        agent.build_client = lambda spec: ChunkingClient()
        try:
            summary = agent.run_attempt(make_task(tmp), cfg, tmp, seed=0, on_delta=on_delta)
        finally:
            agent.build_client = original
        rows = [json.loads(x) for x in (tmp / "live-001-s0" / "trace.jsonl").read_text().splitlines() if x.strip()]
        return summary, rows

    def test_chunks_rebuild_each_turn_and_trace_is_unchanged(self):
        got: dict[int, str] = {}
        s1, rows1 = self.attempt(lambda turn, chunk: got.__setitem__(turn, got.get(turn, "") + chunk))
        s0, rows0 = self.attempt(None)
        self.assertEqual(s1["verdict"], "verified")
        self.assertEqual(s0["verdict"], s1["verdict"])
        models = {r["turn"]: r["text"] for r in rows1 if r["type"] == "model"}
        self.assertEqual(got, models)                         # 逐字拼回 = trace 里的整段
        self.assertNotIn("model_partial", {r["type"] for r in rows1})
        self.assertEqual(shape(rows1), shape(rows0))          # 传不传回调，trace 一模一样


class TestWebPartialBuffer(unittest.TestCase):
    def test_worker_fills_partial_for_live_runs(self):
        from app import server as S
        tmp = Path(tempfile.mkdtemp(prefix="fp-live-web-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        saved = (config.RUNS_ROOT, config.TASKS_ROOT)
        demo = Path(__file__).resolve().parent.parent / "demo-tasks"
        if not (demo / "index" / "tasks.jsonl").exists():
            self.skipTest("没有内置 demo-tasks")
        config.RUNS_ROOT, config.TASKS_ROOT = tmp, demo
        try:
            name = json.loads((demo / "index" / "tasks.jsonl").read_text().splitlines()[0])["name"]
            rid = "livetest0001"
            import queue
            import threading
            S.STATE[rid] = {"q": queue.Queue(), "summary": None, "task": name, "events": [],
                            "cond": threading.Condition(), "t0": time.time(), "ablate": None,
                            "partial": {"turn": 0, "text": "", "seq": 0, "t": 0.0}}
            with S.LOCK:
                S.RUNNING += 1
            S._worker(rid, name, None)
            st = S.STATE.pop(rid)
            last = [e for e in st["events"] if e.get("type") == "model"][-1]
            self.assertEqual(st["partial"]["turn"], last["turn"])
            self.assertEqual(st["partial"]["text"], last["text"])
            self.assertGreater(st["partial"]["seq"], 0)
            self.assertEqual(st["events"][-1]["type"], "done")
            self.assertNotIn("model_partial", {e.get("type") for e in st["events"]})   # 不进事件历史
        finally:
            config.RUNS_ROOT, config.TASKS_ROOT = saved


if __name__ == "__main__":
    unittest.main()
