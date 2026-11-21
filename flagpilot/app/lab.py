"""Lab：实验结果（带统计）+ 本地实验（对比 / 多题批量）。

一个实验 = 若干"组"（arm：某个模型 × 某个档位），每组在同一批题上各跑若干次（seed）。
磁盘上和 TACC 批次同形，所以一个读取器管两种来源：

  runs/tacc/<实验>/<批次>-B?/runs.jsonl  + <题>-s<seed>/trace.jsonl     （TACC 流水线）
  runs/lab/<实验>/meta.json
  runs/lab/<实验>/<组>/runs.jsonl        + <题>-s<seed>/trace.jsonl     （网页上发起的本地实验）

组目录名：只有档位时是 "B3"；带模型时是 "<模型>~B3"。

统计口径：
  * 成功率的 95% 区间用 Wilson（0/n、n/n 时也不会越界，小样本比正态近似靠谱）；
  * 两组之间按 (题, seed) 配对，做精确 McNemar 检验——同一道题在两组下的结果是配对数据，
    不能当两个独立样本比。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Callable

from flagpilot import storage

TIERS = ("B0", "B1", "B2", "B3")
MAX_TASKS, MAX_ARMS, MAX_SEEDS, MAX_JOBS = 60, 8, 5, 400
_EXP_RE = re.compile(r"^(tacc|local)/([\w.-]{1,80})$")
# 基础设施失败（限流、过载、断网、运行器自己崩了）：说明不了模型解不解得出，不进成功率、区间和配对检验，
# 单独计数、可以重跑。模型真的答错 / 超轮次 / 协议错都不算在这里。
_INFRA = re.compile(r"^(model: (HTTP (408|429|5\d\d)|stream error|URLError|TimeoutError|timeout|ConnectionError|RemoteDisconnected|IncompleteRead)|exception: )", re.I)
RUN_DIRS: dict[str, Path] = {}       # 回放 id → 运行目录（读实验时顺手建索引，/api/result 按它挂载）
JOBS: dict[str, dict] = {}           # 正在跑的本地实验：目录名 → 实时状态
_LOCK = threading.Lock()


# ---------------------------------------------------------------- 统计

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """成功率的 Wilson 区间（默认 95%）。"""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def mcnemar(only_a: int, only_b: int) -> float:
    """精确双侧 McNemar：只看不一致的配对（A 过 B 没过 / B 过 A 没过）。"""
    n = only_a + only_b
    if n == 0:
        return 1.0
    k = min(only_a, only_b)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def run_id_of(d: Path) -> str:
    """回放 id：运行目录绝对路径的 sha1 前 12 位（与 server._mount_replay、tacc.summarize_tier 一致）。"""
    return hashlib.sha1(str(Path(d).resolve()).encode()).hexdigest()[:12]


def pretty_model(m: str) -> str:
    if not m:
        return ""
    if m.lower().startswith("mock"):
        return "Demo model"
    words = m.removeprefix("models/").removesuffix("-latest").split("-")
    return " ".join(w if w[:1].isdigit() else w[:1].upper() + w[1:] for w in words).replace("Flash Lite", "Flash-Lite")


def _pct(x: float) -> str:
    return f"{round(100 * x)}%"


# ---------------------------------------------------------------- 读实验

def is_infra(r: dict) -> bool:
    return r.get("verdict") == "error" and bool(_INFRA.match(str(r.get("reason") or "")))


def _rows(f: Path) -> list[dict]:
    """一组的结果；同一次 attempt 重跑过就以最后一条为准。"""
    try:
        rows = [r for r in storage.read_records(f) if r.get("verdict")]
    except Exception:
        return []
    last: dict[str, dict] = {}
    for i, r in enumerate(rows):
        last[str(r.get("run_id") or f"#{i}")] = r
    return list(last.values())


def _arm_name(name: str) -> tuple[str, str]:
    """组目录名 → (模型, 档位)。TACC 的批次目录名是 "<批次>-B3"，没有模型部分。"""
    if "~" in name:
        m, t = name.rsplit("~", 1)
        return m, t
    hit = re.search(r"(B[0-3])$", name)
    return "", hit.group(1) if hit else name


def _arm_dirs(d: Path) -> list[Path]:
    return sorted(p for p in d.iterdir() if p.is_dir() and (p / "runs.jsonl").exists())


def experiments(runs_root: Path, detail: bool = False) -> list[dict]:
    """盘上所有实验，新的在前。TACC 下只有一两条记录的（网页单题）不算实验。"""
    out = []
    for src, root in (("tacc", Path(runs_root) / "tacc"), ("local", Path(runs_root) / "lab")):
        if not root.is_dir():
            continue
        for d in root.iterdir():
            if not d.is_dir():
                continue
            arms = _arm_dirs(d)
            live = JOBS.get(d.name) if src == "local" else None
            if not arms and not live:
                continue
            exp = _experiment(src, d, arms, detail)
            if exp and (src == "local" or exp["attempts"] >= 3):
                out.append(exp)
    out.sort(key=lambda e: e["ts"], reverse=True)
    return out


def experiment(runs_root: Path, eid: str) -> dict | None:
    d = exp_dir(runs_root, eid)
    if d is None:
        return None
    src = eid.split("/", 1)[0]
    return _experiment(src, d, _arm_dirs(d), True)


def find_run(runs_root: Path, rid: str) -> Path | None:
    """回放 id → 运行目录；索引里没有就把实验重读一遍（服务重启后索引是空的）。"""
    if rid not in RUN_DIRS:
        experiments(runs_root)          # 读一遍就把所有 attempt 的 id 建进索引
    d = RUN_DIRS.get(rid)
    return d if d is not None and (d / "trace.jsonl").exists() else None


def exp_dir(runs_root: Path, eid: str) -> Path | None:
    m = _EXP_RE.match(eid or "")
    if not m:
        return None
    root = (Path(runs_root) / ("tacc" if m.group(1) == "tacc" else "lab")).resolve()
    d = (root / m.group(2)).resolve()
    return d if d.is_relative_to(root) and d.is_dir() else None


def _experiment(src: str, d: Path, arm_dirs: list[Path], detail: bool) -> dict | None:
    meta: dict = {}
    try:
        if (d / "meta.json").exists():
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    arms, tasks, cats = [], {}, {}
    order = meta.get("keys") or []
    for a in sorted(arm_dirs, key=lambda p: (order.index(p.name) if p.name in order else 99, _arm_name(p.name)[1], p.name)):
        rows = _rows(a / "runs.jsonl")
        if not rows:
            continue
        m, tier = _arm_name(a.name)
        model = m or next((str(r.get("model")) for r in rows if r.get("model")), "")
        key = a.name if src == "local" else tier
        infra = [r for r in rows if is_infra(r)]
        for r in rows:
            rd = a / str(r.get("run_id") or "")
            rid = run_id_of(rd) if r.get("run_id") and rd.is_dir() else None
            if rid:
                RUN_DIRS[rid] = rd
            ok = r["verdict"] == "verified"
            bad_infra = is_infra(r)
            t = tasks.setdefault(r.get("task") or "?", {"task": r.get("task") or "?", "category": r.get("category") or "", "cells": {}})
            t["cells"].setdefault(key, []).append({"seed": r.get("seed", 0), "verdict": r["verdict"], "turns": r.get("turns"),
                                                  "wall_s": r.get("wall_s"), "run": rid, **({"infra": True} if bad_infra else {})})
            if not bad_infra:
                c = cats.setdefault(r.get("category") or "Other", {}).setdefault(key, [0, 0])
                c[0] += ok
                c[1] += 1
        rows = [r for r in rows if not is_infra(r)]
        if not rows:
            arms.append({"key": key, "tier": tier, "model": model, "label": tier if not m else f"{pretty_model(m)} · {tier}",
                         "n": 0, "verified": 0, "rate": 0.0, "ci": [0.0, 0.0], "turns": 0, "wall_p50": 0, "tokens": 0,
                         "verdicts": {}, "infra": len(infra)})
            continue
        n, k = len(rows), sum(r["verdict"] == "verified" for r in rows)
        lo, hi = wilson(k, n)
        walls = sorted(float(r.get("wall_s") or 0) for r in rows)
        verdicts: dict[str, int] = {}
        for r in rows:
            verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
        arms.append({"key": key, "tier": tier, "model": model,
                     "label": tier if not m else f"{pretty_model(m)} · {tier}",
                     "n": n, "verified": k, "rate": round(k / n, 4), "ci": [round(lo, 4), round(hi, 4)],
                     "turns": round(sum(int(r.get("turns") or 0) for r in rows) / n, 2),
                     "wall_p50": round(walls[n // 2], 2),
                     "tokens": round(sum(int(r.get("total_tokens") or 0) for r in rows) / n),
                     "verdicts": verdicts, "infra": len(infra)})
    pairs = []
    counted = [a for a in arms if a["n"]]
    for i, A in enumerate(counted):
        for B in counted[i + 1:]:
            ya = {(t, c["seed"]): c["verdict"] == "verified" for t, v in tasks.items() for c in v["cells"].get(A["key"], []) if not c.get("infra")}
            yb = {(t, c["seed"]): c["verdict"] == "verified" for t, v in tasks.items() for c in v["cells"].get(B["key"], []) if not c.get("infra")}
            both = ya.keys() & yb.keys()
            oa = sum(ya[x] and not yb[x] for x in both)
            ob = sum(yb[x] and not ya[x] for x in both)
            pairs.append({"a": A["key"], "b": B["key"], "n": len(both), "only_a": oa, "only_b": ob, "p": round(mcnemar(oa, ob), 4)})
    live = JOBS.get(d.name) if src == "local" else None
    models = sorted({a["model"] for a in arms if a["model"]})
    exp = {"id": f"{src}/{d.name}", "source": src, "name": meta.get("label") or _tacc_name(d.name, arm_dirs), "ts": d.stat().st_mtime,
           "models": models, "n_tasks": len(tasks), "attempts": sum(a["n"] + a["infra"] for a in arms),
           "infra": sum(a["infra"] for a in arms),
           "arms": arms, "pairs": pairs, "conclusion": conclude(counted, pairs, len(tasks), sum(a["infra"] for a in arms)),
           "running": bool(live and not live.get("finished")), "planned": meta.get("keys") or []}
    if live:
        exp["live"] = status(d.name)
    if detail:
        exp["planned_tasks"] = meta.get("tasks") or []
        exp["tasks"] = sorted(tasks.values(), key=lambda t: (t["category"], t["task"]))
        exp["categories"] = cats
    return exp


def _tacc_name(name: str, arm_dirs: list[Path]) -> str:
    """TACC 批次目录 "tacc-dev-v5-B0" → "Dev split · v5"；认不出来就用目录名。"""
    for a in arm_dirs:
        m = re.match(r"^tacc-([a-z]+)-(.+)-B[0-3]$", a.name)
        if m:
            return f"{m.group(1).capitalize()} split · {m.group(2)}"
    m = re.match(r"^web-(\d{2})(\d{2})(\d{2})(\d{2})$", name)      # 网页上单题交给 TACC 的运行，按会话归档
    if m:
        return f"Single runs on TACC · {m.group(1)}-{m.group(2)} {m.group(3)}:{m.group(4)}"
    return name


def conclude(arms: list[dict], pairs: list[dict], n_tasks: int, infra: int = 0) -> str:
    """一句话结论：谁最好、差多少、差距是否显著（配对 McNemar）；基础设施失败另说一句。"""
    tail = (f" {infra} attempt{'s' if infra != 1 else ''} hit rate limits or outages and {'are' if infra != 1 else 'is'} not counted."
            if infra else "")
    return _conclude(arms, pairs, n_tasks) + tail


def _conclude(arms: list[dict], pairs: list[dict], n_tasks: int) -> str:
    if not arms:
        return ""
    def ci(a):
        return f"95% CI {_pct(a['ci'][0])}–{_pct(a['ci'][1])}"
    if len(arms) == 1:
        a = arms[0]
        return f"{a['label']} verified {a['verified']} of {a['n']} attempts ({_pct(a['rate'])}, {ci(a)})."
    best = max(arms, key=lambda a: (a["rate"], -a["turns"]))
    worst = min(arms, key=lambda a: (a["rate"], -a["turns"]))
    s = f"On {n_tasks} challenge{'s' if n_tasks != 1 else ''}, {best['label']} verified {best['verified']}/{best['n']} ({_pct(best['rate'])}, {ci(best)})"
    if best["rate"] == worst["rate"]:
        return s + "; every arm verified the same share, so this experiment cannot separate them."
    s += f"; {worst['label']} verified {worst['verified']}/{worst['n']} ({_pct(worst['rate'])})."
    pr = next((p for p in pairs if {p["a"], p["b"]} == {best["key"], worst["key"]}), None)
    if pr and pr["n"]:
        if pr["p"] < 0.05:
            s += f" The gap is significant (paired McNemar p = {pr['p']:.3f})."
        else:
            s += (f" The gap is not significant (paired McNemar p = {pr['p']:.2f}, {pr['only_a'] + pr['only_b']} discordant pairs);"
                  " more challenges or repeats are needed to tell them apart.")
    return s


def cross(runs_root: Path, a: str, b: str) -> dict:
    """两个实验里各挑一组（"实验 id::组名"），按 (题, seed) 配对比较；只算两边都跑过、都不是基础设施失败的那些。

    用途：同一档位换了模型（Qwen vs Gemini），或同一模型换了版本（v5 vs v6）。两组来自不同实验时
    环境可能不同（集群 vs 笔记本、代码版本），结论里只说数字，判断交给读的人。
    """
    def side(ref: str):
        eid, _, key = (ref or "").partition("::")
        exp = experiment(runs_root, eid)
        if exp is None:
            raise ValueError(f"Unknown experiment {eid!r}")
        arm = next((x for x in exp["arms"] if x["key"] == key), None)
        if arm is None:
            raise ValueError(f"No arm {key!r} in {exp['name']}")
        got = {(t["task"], c["seed"]): c["verdict"] == "verified"
               for t in exp.get("tasks", []) for c in t["cells"].get(key, []) if not c.get("infra")}
        return {"exp": exp["id"], "label": f"{exp['name']} · {arm['label']}", "key": key}, got
    A, ya = side(a)
    B, yb = side(b)
    both = sorted(ya.keys() & yb.keys())
    n = len(both)
    ka, kb = sum(ya[x] for x in both), sum(yb[x] for x in both)
    oa = sum(ya[x] and not yb[x] for x in both)
    ob = sum(yb[x] and not ya[x] for x in both)
    p = round(mcnemar(oa, ob), 4)
    for side_, k in ((A, ka), (B, kb)):
        lo, hi = wilson(k, n)
        side_.update(n=n, verified=k, rate=round(k / n, 4) if n else 0.0, ci=[round(lo, 4), round(hi, 4)])
    if not n:
        concl = "The two arms share no attempt (same challenge and repeat), so they cannot be compared."
    else:
        concl = (f"On the {n} attempt{'s' if n != 1 else ''} both ran, {A['label']} verified {ka} ({_pct(ka / n)}) and "
                 f"{B['label']} {kb} ({_pct(kb / n)}); {oa} only by the first, {ob} only by the second. "
                 + (f"The gap is significant (paired McNemar p = {p:.3f})." if p < 0.05 else
                    f"The gap is not significant (paired McNemar p = {p:.2f})."))
    return {"a": A, "b": B, "n": n, "only_a": oa, "only_b": ob, "p": p, "conclusion": concl,
            "tasks": [{"task": t, "seed": s, "a": ya[(t, s)], "b": yb[(t, s)]} for t, s in both]}


def csv_rows(exp: dict) -> str:
    """导出：一行一次 attempt。"""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["experiment", "arm", "model", "tier", "task", "category", "seed", "verdict", "turns", "wall_s", "counted"])
    arms = {a["key"]: a for a in exp.get("arms", [])}
    for t in exp.get("tasks", []):
        for key, cells in t["cells"].items():
            a = arms.get(key, {})
            for c in cells:
                w.writerow([exp["id"], a.get("label", key), a.get("model", ""), a.get("tier", ""), t["task"], t["category"],
                            c["seed"], c["verdict"], c["turns"], c["wall_s"], "no" if c.get("infra") else "yes"])
    return buf.getvalue()


# ---------------------------------------------------------------- 本地实验

def arm_key(model: str, tier: str) -> str:
    return f"{model}~{tier}" if model else tier


def start(runs_root: Path, tasks: list, arms: list[dict], seeds: int, workers: int,
          make_cfg: Callable[[str, str | None], object], label: str = "",
          run_attempt: Callable | None = None, on_done: Callable[[Path], None] | None = None) -> dict:
    """后台跑一个本地实验。arms: [{"model": 名字或 "", "tier": "B3"}]；make_cfg(档位, 模型) → RunConfig。

    记忆一律关掉：实验要求每次 attempt 互相独立，否则后跑的组会读到先跑的组留下的经验。
    """
    if run_attempt is None:
        from flagpilot.agent import run_attempt as _ra
        run_attempt = _ra
    eid = time.strftime("%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    d = Path(runs_root) / "lab" / eid
    d.mkdir(parents=True, exist_ok=True)
    keys = [arm_key(a.get("model") or "", a["tier"]) for a in arms]
    meta = {"label": label, "tasks": [t.name for t in tasks], "arms": arms, "keys": keys, "seeds": seeds,
            "workers": workers, "created": time.time()}
    storage.atomic_write(d / "meta.json", json.dumps(meta, ensure_ascii=False).encode())
    jobs = [(key, a, t, s) for s in range(seeds) for t in tasks for key, a in zip(keys, arms)]
    _launch(d, jobs, workers, make_cfg, run_attempt, on_done)
    return {"id": f"local/{eid}", "total": len(jobs)}


def retry_infra(runs_root: Path, eid: str, resolve: Callable[[str], object], make_cfg: Callable[[str, str | None], object],
                run_attempt: Callable | None = None) -> dict:
    """把一个本地实验里因限流 / 过载等失败的 attempt 原样重跑一遍（同题、同组、同 seed，结果以新的一条为准）。"""
    d = exp_dir(runs_root, eid)
    if d is None or not eid.startswith("local/"):
        raise ValueError("Only experiments started here can be re-run")
    if (JOBS.get(d.name) or {}).get("finished") is None and d.name in JOBS:
        raise ValueError("The experiment is still running")
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    arm_of = dict(zip(meta.get("keys") or [], meta.get("arms") or []))
    jobs = []
    for a in _arm_dirs(d):
        for r in _rows(a / "runs.jsonl"):
            if is_infra(r) and a.name in arm_of:
                t = resolve(str(r.get("task")))
                if t is not None:
                    jobs.append((a.name, arm_of[a.name], t, int(r.get("seed") or 0)))
    if not jobs:
        raise ValueError("Nothing to re-run: no attempt failed for rate limits or outages")
    if run_attempt is None:
        from flagpilot.agent import run_attempt as _ra
        run_attempt = _ra
    _launch(d, jobs, int(meta.get("workers") or 1), make_cfg, run_attempt, None)
    return {"id": eid, "total": len(jobs)}


def _launch(d: Path, jobs: list, workers: int, make_cfg, run_attempt, on_done) -> None:
    st = {"total": len(jobs), "done": 0, "running": {}, "stop": False, "t0": time.time(), "finished": None, "errors": 0}
    with _LOCK:
        JOBS[d.name] = st

    def one(job) -> None:
        key, a, t, s = job
        if st["stop"]:
            return
        slot = f"{key}|{t.name}|{s}"
        with _LOCK:
            st["running"][slot] = {"arm": key, "task": t.name, "seed": s, "turns": 0}

        def hook(ev: dict) -> None:
            turn = ev.get("turn")
            if isinstance(turn, int):
                cur = st["running"].get(slot)
                if cur is not None and turn > cur["turns"]:
                    cur["turns"] = turn
        arm_dir = d / key
        try:
            cfg = make_cfg(a["tier"], a.get("model") or None)
            cfg = replace(cfg, memory=False)
            summary = run_attempt(t, cfg, arm_dir, s, hook)
        except Exception as e:      # 一次 attempt 崩了不拖垮整个实验
            summary = {"verdict": "error", "reason": f"exception: {type(e).__name__}: {e}", "turns": 0, "wall_s": 0}
            st["errors"] += 1
        rec = {"run_id": f"{t.name}-s{s}", "task": t.name, "task_id": getattr(t, "task_id", None),
               "category": getattr(t, "category", ""), "seed": s, "workers": workers, **summary}
        rec.pop("run_dir", None)
        with _LOCK:
            storage.append_record(arm_dir / "runs.jsonl", rec)
            st["running"].pop(slot, None)
            st["done"] += 1
        if on_done is not None and (arm_dir / rec["run_id"]).is_dir():
            try:
                on_done(arm_dir / rec["run_id"])
            except Exception:
                pass

    def main() -> None:
        try:
            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                list(pool.map(one, jobs))
        finally:
            st["finished"] = time.time()

    threading.Thread(target=main, daemon=True).start()


def status(name: str) -> dict | None:
    st = JOBS.get(name)
    if st is None:
        return None
    with _LOCK:
        running = [dict(v) for v in st["running"].values()]
    end = st["finished"] or time.time()
    return {"total": st["total"], "done": st["done"], "running": running, "stopping": st["stop"] and not st["finished"],
            "finished": bool(st["finished"]), "elapsed": round(end - st["t0"], 1), "errors": st["errors"]}


def stop(name: str) -> bool:
    st = JOBS.get(name)
    if st is None or st["finished"]:
        return False
    st["stop"] = True          # 还没开始的不再开始；正在跑的跑完为止（中途掐断会留下半截 trace）
    return True
