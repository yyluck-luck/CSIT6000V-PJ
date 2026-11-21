"""Lab：统计（Wilson 区间、配对 McNemar）、实验读取、一句话结论、导出、本地实验调度。"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from app import lab
from flagpilot import storage


def _write_arm(d: Path, rows: list[dict]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for r in rows:
        rd = d / r["run_id"]
        rd.mkdir(exist_ok=True)
        storage.append_record(rd / "trace.jsonl", {"type": "meta", "task": r["task"]})
        storage.append_record(rd / "trace.jsonl", {"type": "summary", "verdict": r["verdict"]})
        storage.append_record(d / "runs.jsonl", r)


def _row(task, verdict, seed=0, cat="Crypto", turns=5):
    return {"run_id": f"{task}-s{seed}", "task": task, "category": cat, "seed": seed, "verdict": verdict,
            "turns": turns, "wall_s": 10.0, "total_tokens": 1000, "model": "qwen3.8-27b"}


class TestStats(unittest.TestCase):
    def test_wilson(self):
        self.assertEqual(lab.wilson(0, 0), (0.0, 0.0))
        lo, hi = lab.wilson(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.2775, places=3)
        lo, hi = lab.wilson(10, 10)
        self.assertAlmostEqual(lo, 0.7225, places=3)
        self.assertEqual(hi, 1.0)
        lo, hi = lab.wilson(14, 20)
        self.assertAlmostEqual(lo, 0.481, places=2)
        self.assertAlmostEqual(hi, 0.855, places=2)

    def test_mcnemar_exact(self):
        self.assertEqual(lab.mcnemar(0, 0), 1.0)
        self.assertAlmostEqual(lab.mcnemar(5, 0), 0.0625)
        self.assertAlmostEqual(lab.mcnemar(3, 1), 0.625)
        self.assertAlmostEqual(lab.mcnemar(1, 3), 0.625)
        self.assertLess(lab.mcnemar(10, 0), 0.01)

    def test_pretty_model(self):
        self.assertEqual(lab.pretty_model("gemini-flash-lite-latest"), "Gemini Flash-Lite")
        self.assertEqual(lab.pretty_model("mock"), "Demo model")


class TestExperiments(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fp-lab-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _tacc(self):
        exp = self.root / "tacc" / "v9"
        _write_arm(exp / "tacc-dev-v9-B0", [_row("a", "verified"), _row("b", "verified"), _row("c", "verified"), _row("d", "wrong", cat="Web")])
        _write_arm(exp / "tacc-dev-v9-B3", [_row("a", "verified"), _row("b", "budget"), _row("c", "unverified"), _row("d", "wrong", cat="Web")])
        return exp

    def test_reads_tacc_batches_with_stats_and_pairs(self):
        self._tacc()
        (exps,) = lab.experiments(self.root)
        self.assertEqual(exps["id"], "tacc/v9")
        arms = {a["key"]: a for a in exps["arms"]}
        self.assertEqual((arms["B0"]["verified"], arms["B0"]["n"], arms["B3"]["verified"]), (3, 4, 1))
        self.assertEqual(arms["B0"]["ci"], [round(x, 4) for x in lab.wilson(3, 4)])
        (pair,) = exps["pairs"]
        self.assertEqual((pair["n"], pair["only_a"], pair["only_b"]), (4, 2, 0))
        self.assertAlmostEqual(pair["p"], 0.5)
        self.assertIn("B0 verified 3/4", exps["conclusion"])
        self.assertIn("not significant", exps["conclusion"])

    def test_detail_has_cells_categories_and_replay_ids(self):
        self._tacc()
        exp = lab.experiment(self.root, "tacc/v9")
        cells = {t["task"]: t["cells"] for t in exp["tasks"]}
        self.assertEqual(cells["b"]["B3"][0]["verdict"], "budget")
        rid = cells["a"]["B0"][0]["run"]
        self.assertEqual(lab.find_run(self.root, rid), (self.root / "tacc" / "v9" / "tacc-dev-v9-B0" / "a-s0").resolve())
        self.assertEqual(exp["categories"]["Web"]["B0"], [0, 1])
        csv = lab.csv_rows(exp).splitlines()
        self.assertEqual(csv[0], "experiment,arm,model,tier,task,category,seed,verdict,turns,wall_s,counted")
        self.assertEqual(len(csv), 1 + 8)

    def test_bad_ids_are_refused(self):
        self._tacc()
        for bad in ("tacc/../..", "../tacc/v9", "other/v9", "tacc/", "local/nope"):
            self.assertIsNone(lab.experiment(self.root, bad), bad)

    def test_names(self):
        self.assertEqual(lab._tacc_name("v5", [Path("tacc-dev-v5-B0")]), "Dev split · v5")
        self.assertEqual(lab._tacc_name("web-11201305", [Path("crypto-005-B3")]), "Single runs on TACC · 11-20 13:05")
        self.assertEqual(lab._tacc_name("odd", [Path("x")]), "odd")

    def test_single_runs_under_tacc_are_not_experiments(self):
        _write_arm(self.root / "tacc" / "web-1" / "x-B3", [_row("a", "verified")])
        self.assertEqual(lab.experiments(self.root), [])

    def test_conclusion_when_all_equal_and_when_significant(self):
        arms = [{"key": "B0", "label": "B0", "rate": .5, "turns": 3, "verified": 5, "n": 10, "ci": [.2, .8]},
                {"key": "B3", "label": "B3", "rate": .5, "turns": 4, "verified": 5, "n": 10, "ci": [.2, .8]}]
        self.assertIn("cannot separate", lab.conclude(arms, [], 10))
        arms[1].update(rate=1.0, verified=10)
        pairs = [{"a": "B0", "b": "B3", "n": 10, "only_a": 0, "only_b": 5, "p": lab.mcnemar(0, 5)}]
        self.assertIn("not significant (paired McNemar p = 0.06", lab.conclude(arms, pairs, 10))   # 5:0 is still p = .0625
        pairs[0].update(only_b=10, p=lab.mcnemar(0, 10))
        self.assertIn("The gap is significant (paired McNemar p = 0.002)", lab.conclude(arms, pairs, 10))


class TestCross(unittest.TestCase):
    """跨实验：同一档位换模型 / 换版本，按 (题, seed) 配对，只算两边都有、都不是基础设施失败的。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fp-lab-cross-"))
        _write_arm(self.root / "tacc" / "v9" / "tacc-dev-v9-B0", [_row("a", "verified"), _row("b", "verified"), _row("c", "verified"), _row("d", "wrong")])
        d = self.root / "lab" / "g1"
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({"label": "Gemini run", "keys": ["gem~B0"]}))
        quota = dict(_row("c", "error"), reason="model: HTTP 429: quota")
        _write_arm(d / "gem~B0", [_row("a", "verified"), _row("b", "wrong"), quota, _row("d", "verified"), _row("e", "verified")])

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_pairs_only_what_both_ran(self):
        r = lab.cross(self.root, "tacc/v9::B0", "local/g1::gem~B0")
        self.assertEqual(r["n"], 3, "c is a rate limit on one side and e ran on one side only")
        self.assertEqual((r["a"]["verified"], r["b"]["verified"], r["only_a"], r["only_b"]), (2, 2, 1, 1))
        self.assertEqual(r["p"], 1.0)
        self.assertIn("Dev split · v9 · B0", r["a"]["label"])
        self.assertIn("not significant", r["conclusion"])

    def test_bad_refs(self):
        for a, b in (("tacc/nope::B0", "local/g1::gem~B0"), ("tacc/v9::B7", "local/g1::gem~B0"), ("", "")):
            with self.assertRaises(ValueError):
                lab.cross(self.root, a, b)


class TestInfrastructureFailures(unittest.TestCase):
    """限流 / 过载不是模型的成绩：不进成功率、区间和配对检验，单独计数，可以原样重跑。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fp-lab-infra-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _local(self):
        d = self.root / "lab" / "e1"
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({"label": "infra", "keys": ["mock~B3"], "arms": [{"model": "mock", "tier": "B3"}],
                                                 "tasks": ["a", "b", "c"], "seeds": 1, "workers": 1}))
        quota = dict(_row("b", "error"), reason="model: HTTP 429: You exceeded your current quota")
        _write_arm(d / "mock~B3", [_row("a", "verified"), quota, _row("c", "wrong")])
        return d

    def test_rate_limited_attempts_are_not_counted(self):
        self._local()
        exp = lab.experiment(self.root, "local/e1")
        (arm,) = exp["arms"]
        self.assertEqual((arm["n"], arm["verified"], arm["infra"], exp["infra"], exp["attempts"]), (2, 1, 1, 1, 3))
        cell = {t["task"]: t["cells"]["mock~B3"][0] for t in exp["tasks"]}["b"]
        self.assertTrue(cell.get("infra"))
        self.assertIn("1 attempt hit rate limits or outages and is not counted", exp["conclusion"])
        self.assertIn(",no", lab.csv_rows(exp))
        self.assertTrue(lab.is_infra({"verdict": "error", "reason": "model: HTTP 503: overloaded"}))
        self.assertFalse(lab.is_infra({"verdict": "error", "reason": "model: HTTP 400: bad request"}))
        self.assertFalse(lab.is_infra({"verdict": "budget", "reason": "max_turns"}))

    def test_a_rerun_replaces_the_failed_row(self):
        d = self._local()
        tasks = {n: SimpleNamespace(name=n, category="Crypto", task_id=1) for n in "abc"}

        def fake_attempt(task, cfg, out_dir, seed, hook):
            return {"verdict": "verified", "turns": 3, "wall_s": 1.0}
        orig = lab.replace
        lab.replace = lambda cfg, **kw: SimpleNamespace(**{**vars(cfg), **kw})
        try:
            r = lab.retry_infra(self.root, "local/e1", tasks.get, lambda tier, model: SimpleNamespace(tier=tier, model=model, memory=True),
                                run_attempt=fake_attempt)
            self.assertEqual(r["total"], 1)
            for _ in range(200):
                if lab.status("e1")["finished"]:
                    break
                time.sleep(0.02)
        finally:
            lab.replace = orig
        exp = lab.experiment(self.root, "local/e1")
        (arm,) = exp["arms"]
        self.assertEqual((arm["n"], arm["verified"], arm["infra"]), (3, 2, 0))
        self.assertEqual(sum(1 for _ in (d / "mock~B3" / "runs.jsonl").read_text().splitlines()), 4, "the old row stays on disk; the newest wins")
        with self.assertRaises(ValueError):
            lab.retry_infra(self.root, "local/e1", tasks.get, lambda tier, model: None)
        with self.assertRaises(ValueError):
            lab.retry_infra(self.root, "tacc/v9", tasks.get, lambda tier, model: None)


class TestLocalExperiment(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fp-lab-run-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_runs_every_task_arm_seed_and_reports_live_progress(self):
        seen = []

        def fake_attempt(task, cfg, out_dir, seed, hook):
            seen.append((task.name, cfg.tier, cfg.model, cfg.memory, seed))
            hook({"type": "tool", "turn": 2})
            rd = Path(out_dir) / f"{task.name}-s{seed}"
            rd.mkdir(parents=True, exist_ok=True)
            storage.append_record(rd / "trace.jsonl", {"type": "summary", "verdict": "x"})
            time.sleep(0.05)
            return {"verdict": "verified" if cfg.tier == "B3" else "wrong", "turns": 2, "wall_s": 1.0,
                    "model": cfg.model or "mock", "ablation": cfg.tier}

        def make_cfg(tier, model):
            return SimpleNamespace(tier=tier, model=model, memory=True, __dataclass_fields__={})

        tasks = [SimpleNamespace(name=n, category="Crypto", task_id=i) for i, n in enumerate(("t1", "t2"))]
        import dataclasses
        orig = lab.replace
        lab.replace = lambda cfg, **kw: SimpleNamespace(**{**vars(cfg), **kw})
        try:
            r = lab.start(self.root, tasks, [{"model": "", "tier": "B0"}, {"model": "mock", "tier": "B3"}], seeds=2, workers=3,
                          make_cfg=make_cfg, label="unit", run_attempt=fake_attempt)
            name = r["id"].split("/", 1)[1]
            self.assertEqual(r["total"], 8)
            for _ in range(200):
                st = lab.status(name)
                if st["finished"]:
                    break
                time.sleep(0.02)
        finally:
            lab.replace = orig
        self.assertTrue(st["finished"])
        self.assertEqual((st["done"], st["running"], st["errors"]), (8, [], 0))
        self.assertTrue(all(m is False for (_, _, _, m, _) in seen), "experiments must run with memory off")
        exp = lab.experiment(self.root, r["id"])
        self.assertEqual(exp["name"], "unit")
        self.assertEqual([a["key"] for a in exp["arms"]], ["B0", "mock~B3"])
        self.assertEqual([a["label"] for a in exp["arms"]], ["B0", "Demo model · B3"])
        self.assertEqual([(a["verified"], a["n"]) for a in exp["arms"]], [(0, 4), (4, 4)])
        self.assertEqual(exp["pairs"][0]["only_b"], 4)
        self.assertFalse(lab.stop(name), "a finished experiment cannot be stopped")


if __name__ == "__main__":
    unittest.main()
