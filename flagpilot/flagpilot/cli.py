"""命令行入口：python3 -m flagpilot <cmd>

    doctor                     环境自检（上集群第一件事）
    list                       看题库
    run    --task general-004  跑一道题，实时打印每一步
    batch  --scope core ...    批量跑 + 续跑 + 分片
    report --batch dev         出指标（可 --md 导出）
    compare --batches B0,B1    并排对比（E1 出表）
    probe  --mode brief        记忆探针：不给工具，单轮问 flag
    web                        起演示页面
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import __version__, config, metrics
from .config import (ABLATIONS, Budget, Policy, RunConfig, SandboxLimits,
                     get_ablation, make_model_spec)
from .runner import BatchRunner
from .tasks import load_index, select


def _p(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- doctor

def cmd_doctor(args) -> int:
    from .sandbox import probe_capabilities

    caps = probe_capabilities(force=True)
    ok = True

    _p(f"FlagPilot {__version__}")
    _p(f"python           {sys.version.split()[0]}  ({sys.executable})")
    _p(f"题库             {config.TASKS_ROOT}")
    _p(f"运行产物         {config.RUNS_ROOT}")
    _p("")

    # 题库完整性
    tasks = []
    try:
        tasks = load_index()
        cats: dict[str, int] = {}
        for t in tasks:
            cats[t.category] = cats.get(t.category, 0) + 1
        _p(f"题目数           {len(tasks)}  " + ", ".join(f"{k}={v}" for k, v in sorted(cats.items())))
    except Exception as e:
        _p(f"题目数           读取失败：{e}")
        ok = False

    # index 声明 vs 磁盘实际：两个方向都要查。
    # 少目录时 stage_assets 只静默返回 0，沙箱里 challenge/ 是空的，模型对着空气解题，
    # 最后被记成"模型能力不行"——这种错必须在开跑之前拦住，而不是等报告写完才发现。
    if tasks:
        miss_dir, miss_file = [], []
        for t in tasks:
            base = config.TASKS_ROOT / t.assets_dir
            if not base.is_dir():
                miss_dir.append(t.name)
                continue
            for a in (t.assets or []):
                if a == ".placeholder":
                    continue
                if not (base / a).exists():
                    miss_file.append(f"{t.name}/{a}")
        if miss_dir or miss_file:
            _p(f"题库一致性       不通过：{len(miss_dir)} 题缺目录、{len(miss_file)} 个附件缺失")
            for n in miss_dir[:4]:
                _p(f"  - 缺目录  {n}")
            for n in miss_file[:4]:
                _p(f"  - 缺附件  {n}")
            if len(miss_dir) + len(miss_file) > 8:
                _p(f"  … 另有 {len(miss_dir) + len(miss_file) - 8} 项")
            _p("  → 只放部分题目时必须同步裁剪 index/tasks.jsonl，否则跑到缺题会静默空跑")
            ok = False
        else:
            _p(f"题库一致性       通过（{len(tasks)} 题的目录与附件齐全）")

    # 泄漏检查：这两样东西绝不能被上传
    leaks = []
    gold = config.TASKS_ROOT / "_private" / "gold_private.json"
    if gold.exists():
        leaks.append(f"明文答案表存在：{gold}")
    if "with-solutions" in str(config.TASKS_ROOT):
        leaks.append(f"题库指向了带题解的版本：{config.TASKS_ROOT}")
    for sol in config.TASKS_ROOT.glob("tasks/*/solution"):
        leaks.append(f"题解目录残留：{sol}")
        break
    if leaks:
        _p("")
        _p("⚠ 泄漏风险（上集群前必须处理）：")
        for l in leaks:
            _p("  - " + l)
        ok = False
    else:
        _p("泄漏检查         通过（无明文 gold、无题解目录）")

    # 执行能力
    _p("")
    _p(f"网络隔离         {caps.net_isolation}" + ("  ← 不可作为强隔离证据" if caps.net_isolation != "netns" else ""))
    _p(f"资源上限         {'可用' if caps.rlimit else '不可用'}")
    for note in caps.notes:
        _p(f"  · {note}")

    # 题目声明需要的工具：只查已知清单，避免把自由文本拆成垃圾 token
    try:
        known_bin = {"strings", "grep", "binwalk", "exiftool", "zsteg", "tshark",
                     "tcpdump", "sleuthkit", "foremost", "steghide", "objdump",
                     "gdb", "java", "javap", "gcc", "z3"}
        known_mod = {"pycryptodome": "Crypto", "gmpy2": "gmpy2", "scapy": "scapy",
                     "z3": "z3", "capstone": "capstone", "pillow": "PIL"}
        need: dict[str, list[str]] = {}
        for t in load_index():
            for tool in t.suggested_tools or []:
                for tok in str(tool).replace("/", " ").replace("+", " ").split():
                    tok = tok.strip().lower()
                    if tok in known_bin or tok in known_mod:
                        need.setdefault(tok, []).append(t.name)
        missing = {}
        for k, v in need.items():
            if k in known_mod:
                try:
                    __import__(known_mod[k])
                except Exception:
                    missing[k] = v
            elif not shutil.which(k):
                missing[k] = v
        if missing:
            _p("")
            _p("⚠ 题目声明需要、但本机没有的工具：")
            for k, v in sorted(missing.items(), key=lambda kv: -len(kv[1])):
                _p(f"  - {k:<14} 影响 {len(v)} 题（如 {v[0]}）")
            _p("  → 这些题要在报告里单独说明，否则会被读成模型能力问题")
    except Exception:
        pass

    # 工具链：RE 那半边能不能做，取决于这几个
    _p("")
    tools = ["python3", "java", "javap", "objdump", "gcc", "unshare", "nvidia-smi"]
    for t in tools:
        path = shutil.which(t)
        _p(f"{t:<16} {path or '缺失'}")

    _p("")
    if os.environ.get("SLURM_JOB_ID"):
        _p(f"Slurm 作业       {os.environ.get('SLURM_JOB_ID')}  "
           f"分区={os.environ.get('SLURM_JOB_PARTITION', '?')}  "
           f"节点={os.environ.get('SLURMD_NODENAME', '?')}")
    else:
        _p("Slurm 作业       未在作业内（本地模式）")

    base = os.environ.get("SERVICE_BASE_URL") or os.environ.get("FLAGPILOT_BASE_URL")
    _p(f"模型端点         {base or '未设置（用 --base-url 或 FLAGPILOT_BASE_URL）'}")

    _p("")
    _p("结论：" + ("可以开始跑" if ok else "先处理上面的问题"))
    return 0 if ok else 1


# ---------------------------------------------------------------- list

def cmd_list(args) -> int:
    tasks = select(load_index(), category=args.category, scope=args.scope)
    _p(f"{'name':<18}{'cat':<18}{'scope':<8}title")
    for t in tasks:
        _p(f"{t.name:<18}{t.category:<18}{t.scope:<8}{(t.title or '')[:70]}")
    _p(f"\n共 {len(tasks)} 题")
    return 0


# ---------------------------------------------------------------- run

def _upsert_result(batch_dir: Path, rec: dict) -> None:
    """把单题结果写进 runs.jsonl，同 run_id 覆盖。

    单题命令也要落这一行，否则 report / compare 只认批量跑出来的批次，
    调 prompt 时反复单跑的那几次就成了查不到的黑数。
    """
    path = Path(batch_dir) / "runs.jsonl"
    rows: list[dict] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("run_id") != rec.get("run_id"):
                rows.append(r)
    rows.append(rec)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 全量重写而不是追加：同 run_id 要覆盖。但直接 write_text 被 kill 会留下
    # 半个文件（TACC 到时限就 SIGKILL）。先写临时文件再原子替换。
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def cmd_run(args) -> int:
    from .agent import run_attempt

    cfg = _build_cfg(args)
    tasks = select(load_index(), names=args.task and [args.task], ids=args.id)
    if not tasks:
        _p("没有匹配到题目")
        return 1
    task = tasks[0]
    out = config.RUNS_ROOT / cfg.batch
    _p(f"[run] {task.name}  model={cfg.model.name}  ablation={cfg.ablation.name}  "
       f"turns<={cfg.budget.max_turns}")

    def hook(ev):
        t = ev.get("type")
        if t == "model":
            _p(f"  turn {ev['turn']:<3} tokens={ev['prompt_tokens']}+{ev['completion_tokens']} "
               f"ttft={ev['ttft_ms']}ms")
        elif t == "tool":
            _p(f"    ↳ {ev['action']} exit={ev['exit_code']} {ev['duration_ms']}ms "
               f"obs={ev['sent_chars']}c" + (" [截断]" if ev.get("truncated") else ""))
        elif t == "replay":
            _p(f"  replay: {ev['verdict']} / {ev['reason']} — {ev.get('detail', '')}")

    summary = run_attempt(task, cfg, out, seed=0, hook=hook)
    _upsert_result(out, {
        "run_id": f"{task.name}-s0",
        "task": task.name,
        "task_id": task.task_id,
        "category": task.category,
        "seed": 0,
        "workers": 1,
        **summary,
    })
    _p("")
    _p(json.dumps(summary, ensure_ascii=False, indent=2))
    _p(f"\ntrace: {summary['run_dir']}/trace.jsonl")
    return 0


# ---------------------------------------------------------------- batch

def cmd_batch(args) -> int:
    cfg = _build_cfg(args)
    shard = None
    if args.shard:
        i, n = args.shard.split("/")
        shard = (int(i), int(n))
    tasks = select(
        load_index(),
        names=args.tasks.split(",") if args.tasks else None,
        category=args.category,
        scope=args.scope,
        limit=args.limit,
        shard=shard,
    )
    if not tasks:
        _p("没有匹配到题目")
        return 1
    _p(f"[batch] {cfg.batch}: {len(tasks)} 题 × {cfg.seeds} seeds, "
       f"model={cfg.model.name}, ablation={cfg.ablation.name}（{cfg.ablation.desc}）")
    runner = BatchRunner(cfg, config.RUNS_ROOT / cfg.batch, workers=args.workers)
    runner.run(tasks, resume=not args.no_resume)
    results = runner.load_results()
    _p("")
    _p(metrics.render(metrics.summarize(results)))
    return 0


# ---------------------------------------------------------------- report

def cmd_report(args) -> int:
    path = config.RUNS_ROOT / args.batch / "runs.jsonl"
    if not path.exists():
        _p(f"找不到 {path}")
        return 1
    results = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    s = metrics.summarize(results)
    if args.json:
        _p(json.dumps(s, ensure_ascii=False, indent=2))
    else:
        _p(metrics.render(s))
    if args.md:
        out = Path(args.md).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        title = args.title or f"{args.batch}（n={s.get('n', 0)}）"
        out.write_text(metrics.to_markdown(s, title=title), encoding="utf-8")
        _p(f"\nMarkdown 已写入 {out}")
    return 0


def cmd_compare(args) -> int:
    names = [x.strip() for x in args.batches.split(",") if x.strip()]
    if not names:
        _p("用法：compare --batches B0,B1,B2,B3")
        return 1
    batches: dict[str, dict] = {}
    for n in names:
        p = config.RUNS_ROOT / n / "runs.jsonl"
        if not p.exists():
            _p(f"[warn] 跳过 {n}：找不到 {p}")
            continue
        rs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        batches[n] = metrics.summarize(rs, with_tags=False)
    if not batches:
        return 1
    table = metrics.compare(batches)
    _p(table)
    if args.md:
        out = Path(args.md).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(table + "\n", encoding="utf-8")
        _p(f"\nMarkdown 已写入 {out}")
    return 0


# ---------------------------------------------------------------- probe

def cmd_probe(args) -> int:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from . import probe

    cfg = _build_cfg(args)
    tasks = select(
        load_index(),
        names=args.tasks.split(",") if args.tasks else None,
        category=args.category,
        scope=args.scope,
        limit=args.limit,
    )
    if not tasks:
        _p("没有匹配到题目")
        return 1

    out_dir = config.RUNS_ROOT / cfg.batch
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "probe.jsonl"
    _p(f"[probe] mode={args.mode}  {len(tasks)} 题  model={cfg.model.name}")

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = {pool.submit(probe.run_probe, t, cfg, args.mode): t for t in tasks}
        for i, fut in enumerate(as_completed(futs), 1):
            t = futs[fut]
            try:
                r = fut.result().as_dict()
            except Exception as e:
                r = {"task": t.name, "task_id": t.task_id, "category": t.category,
                     "mode": args.mode, "answer": "", "hit": False, "refused": False,
                     "latency_ms": 0, "tokens": 0, "error": f"{type(e).__name__}: {e}"}
            results.append(r)
            mark = "HIT " if r["hit"] else ("—   " if not r["error"] else "ERR ")
            _p(f"  [{i}/{len(tasks)}] {mark}{r['task']:<20} {r['answer'][:60]}")

    with path.open("w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    s = probe.summarize(results)
    _p("")
    _p(probe.render(s, args.mode))
    _p(f"\n结果：{path}")
    return 0


# ---------------------------------------------------------------- web

def cmd_web(args) -> int:
    from app.server import serve

    serve(host=args.host, port=args.port, model=args.model,
          base_url=args.base_url, track=args.track,
          metrics_url=getattr(args, "metrics_url", "") or "")
    return 0


# ---------------------------------------------------------------- 工具

def _build_cfg(args) -> RunConfig:
    base = args.base_url or os.environ.get("FLAGPILOT_BASE_URL") \
        or os.environ.get("SERVICE_BASE_URL") or "http://127.0.0.1:8000/v1"
    model = make_model_spec(
        args.model,
        base,
        thinking=bool(getattr(args, "thinking", False)),
        api_key=args.api_key or os.environ.get("FLAGPILOT_API_KEY", "EMPTY"),
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        metrics_url=args.metrics_url or "",
        price_in_per_mtok=args.price_in,
        price_out_per_mtok=args.price_out,
    )
    abl = get_ablation(getattr(args, "ablate", None))
    # 消融档位优先于命令行开关：B0/B1 的"有没有截断"是实验变量，
    # 不能因为某次忘了传 --no-truncate 就悄悄变成同一档。
    truncate_on = abl.truncate and not args.no_truncate
    policy = Policy(
        truncate_head_chars=args.head if truncate_on else None,
        truncate_tail_chars=args.tail if truncate_on else None,
        compact_at_chars=None if getattr(args, "no_compact", False) else 48_000,
    )
    return RunConfig(
        batch=args.batch,
        track=args.track,
        model=model,
        budget=Budget(max_turns=args.turns, max_wall_s=args.wall),
        policy=policy,
        limits=SandboxLimits(action_timeout_s=args.action_timeout),
        ablation=abl,
        replay_timeout_s=args.replay_timeout,
        seeds=getattr(args, "seeds", 1),
        memory=bool(getattr(args, "memory", False)),
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="flagpilot", description="重放验证型 CTF Agent")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="环境自检")
    d.set_defaults(func=cmd_doctor)

    l = sub.add_parser("list", help="列出题目")
    l.add_argument("--category")
    l.add_argument("--scope", default="core")
    l.set_defaults(func=cmd_list)

    def add_common(p):
        p.add_argument("--batch", default="dev")
        p.add_argument("--track", default="L", choices=["L", "H"])
        p.add_argument("--model", default="mock")
        p.add_argument("--base-url")
        p.add_argument("--api-key")
        p.add_argument("--metrics-url", default="")
        p.add_argument("--temperature", type=float, default=0.2)
        p.add_argument("--max-tokens", type=int, default=2048)
        p.add_argument("--turns", type=int, default=20)
        p.add_argument("--wall", type=int, default=1500)
        p.add_argument("--head", type=int, default=1500)
        p.add_argument("--tail", type=int, default=1500)
        p.add_argument("--no-truncate", action="store_true")
        p.add_argument("--action-timeout", type=float, default=60.0)
        p.add_argument("--replay-timeout", type=float, default=300.0)
        p.add_argument("--price-in", type=float, default=0.0)
        p.add_argument("--price-out", type=float, default=0.0)
        p.add_argument("--thinking", action="store_true",
                       help="打开模型的思维链（Qwen3 默认关，开了会明显吃 token）")
        p.add_argument("--ablate", choices=list(ABLATIONS), default=None,
                       help="E1 消融档位：B0 无截断 → B1 +截断 → B2 +工具箱 → B3 +prompt")
        p.add_argument("--memory", action="store_true",
                       help="把同题历史结论注入 prompt（批跑默认关，避免污染消融对照）")
        p.add_argument("--no-compact", action="store_true",
                       help="关闭上下文压缩（长 run 的 prompt 会一直线性增长）")

    r = sub.add_parser("run", help="跑一道题")
    add_common(r)
    r.add_argument("--task")
    r.add_argument("--id", type=int)
    r.set_defaults(func=cmd_run)

    b = sub.add_parser("batch", help="批量跑")
    add_common(b)
    b.add_argument("--tasks")
    b.add_argument("--category")
    b.add_argument("--scope", default="core")
    b.add_argument("--limit", type=int)
    b.add_argument("--shard", help="i/n，取第 i 片（0 起）")
    b.add_argument("--workers", type=int, default=4)
    b.add_argument("--seeds", type=int, default=1)
    b.add_argument("--no-resume", action="store_true")
    b.set_defaults(func=cmd_batch)

    rp = sub.add_parser("report", help="出指标")
    rp.add_argument("--batch", default="dev")
    rp.add_argument("--json", action="store_true")
    rp.add_argument("--md", help="同时导出 Markdown 表格到该路径")
    rp.add_argument("--title", help="Markdown 标题（默认用 batch 名）")
    rp.set_defaults(func=cmd_report)

    cp = sub.add_parser("compare", help="并排对比多个批次（E1 出表用）")
    cp.add_argument("--batches", required=True, help="逗号分隔，如 B0,B1,B2,B3")
    cp.add_argument("--md", help="导出 Markdown 表格")
    cp.set_defaults(func=cmd_compare)

    pb = sub.add_parser("probe", help="记忆探针：不给工具，单轮问 flag")
    add_common(pb)
    pb.add_argument("--mode", default="brief", choices=["name", "brief", "files"])
    pb.add_argument("--tasks")
    pb.add_argument("--category")
    pb.add_argument("--scope", default="core")
    pb.add_argument("--limit", type=int)
    pb.add_argument("--workers", type=int, default=4)
    pb.set_defaults(func=cmd_probe)

    w = sub.add_parser("web", help="演示页面")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8080)
    w.add_argument("--model", default="mock")
    w.add_argument("--base-url")
    w.add_argument("--metrics-url", default="",
                   help="vLLM 的 /metrics；给了界面才能显示 prefix cache 命中率")
    w.add_argument("--track", default="L")
    w.set_defaults(func=cmd_web)
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
