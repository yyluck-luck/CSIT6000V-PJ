"""命令行入口：python3 -m flagpilot <cmd>

    doctor                     环境自检（上集群第一件事）
    list                       看题库
    run    --task general-004  跑一道题，实时打印每一步
    batch  --scope core ...    批量跑 + 续跑 + 分片
    report --batch dev         出指标（可 --md 导出）
    compare --batches B0,B1    并排对比（E1 出表；--e3 --ref 出 E3 表）
    pair   --a X --b Y         配对比较 + 精确 McNemar（同题同 seed 逐对看修好/弄坏）
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

from . import __version__, config, gpu, metrics, splits, storage, writeup
from .config import (ABLATIONS, GEMINI, TRACK_H, Budget, Hybrid, ModelSpec, Policy, RunConfig, SandboxLimits,
                     get_ablation, make_model_spec, track_h_api_key)
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
    # proposal 要的是 "one container per attempt"。这里如实报，别让报告
    # 写成"有容器隔离"而实际是进程级。
    _p(f"隔离等级         {caps.isolation}"
       + ("" if caps.isolation == "container" else "  ← 非容器，报告里必须如实说明"))
    _p(f"容器             {caps.container}"
       + (f"  ({caps.container_image})" if caps.container_image else ""))
    # toolbox 承诺的四样：Python（上面已报）、SMT、binutils/反汇编、调试器
    from .tools import disasm_binary, z3_available
    z3_ok = z3_available()
    disasm = disasm_binary()
    gdb = shutil.which("gdb")
    _p(f"SMT (z3)         {'可用' if z3_ok else '不可用  ← smt 动作会明确报错并建议改用 python'}")
    _p(f"反汇编           {disasm or '不可用  ← disasm 动作会明确报错并建议改用 shell'}")
    _p(f"调试器           {gdb or '未找到  ← 只能靠 shell 调其它调试器'}")
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
    rows = [r for r in storage.read_records(path) if r.get("run_id") != rec.get("run_id")]
    rows.append(rec)
    storage.write_records(path, rows)


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


# ---------------------------------------------------------------- show

def cmd_show(args) -> int:
    """把一次已完成的 run 复盘成时间线。

    为什么需要它：交付给助教的是一堆 `runs/`，如果只能靠 web 看，就得先起
    服务、再猜 run_id。而 `trace.jsonl` 本身是自解释的——这个命令不依赖服务、
    不依赖模型、不依赖网络，`--run-dir` 指过去就能把全过程读出来。

    `--full` 才打印 observation 全文与脚本全文；默认只给摘要，
    因为一次 run 的原文动辄上万字，全打出来没人看。
    """
    from .trace import read_jsonl

    run_dir = Path(args.run_dir).expanduser()
    trace = run_dir / "trace.jsonl"
    if not trace.exists():
        _p(f"没有找到 {trace}")
        return 1
    rows = read_jsonl(trace)
    if not rows:
        _p(f"{trace} 为空或无法解密")
        return 1

    meta = next((r for r in rows if r.get("type") == "meta"), {})
    model = meta.get("model") or {}
    budget = meta.get("budget") or {}
    caps = meta.get("caps") or {}
    out: list[str] = [
        f"# {meta.get('task', '?')} · {meta.get('run_id', '?')}",
        f"model={model.get('name')}  ablation={(meta.get('ablation') or {}).get('name')}  "
        f"max_turns={budget.get('max_turns')}  max_tokens={model.get('max_tokens')}",
        f"isolation={caps.get('isolation')}  network={caps.get('net_isolation')}",
        "",
    ]

    for ev in rows:
        t = ev.get("type")
        if t == "meta":
            continue
        if t == "summary":
            continue
        if t == "model":
            fr = ev.get("finish_reason") or "?"
            out.append("")
            out.append(f"[{ev.get('turn'):>2}] model  "
                       f"{ev.get('prompt_tokens')}+{ev.get('completion_tokens')} tok  "
                       f"ttft={ev.get('ttft_ms')}ms  finish={fr}")
            if args.full and ev.get("text"):
                out.extend("       " + ln for ln in str(ev["text"]).splitlines())
        elif t == "tool":
            mark = "!" if ev.get("timed_out") else " "
            out.append(f"     {mark} {ev.get('action')}  exit={ev.get('exit_code')}  "
                       f"{ev.get('duration_ms')}ms  obs={ev.get('sent_chars')}c"
                       + ("  [截断]" if ev.get("truncated") else ""))
            if args.full and ev.get("obs_head"):
                out.extend("       " + ln for ln in str(ev["obs_head"]).splitlines()[:40])
        elif t == "submit":
            out.append(f"     → submit  flag={ev.get('flag')}")
            if args.full and ev.get("script"):
                out.append("       script:")
                out.extend("       | " + ln for ln in str(ev["script"]).splitlines())
        elif t == "replay":
            out.append(f"     ⇄ replay  {ev.get('verdict')} / {ev.get('reason')}"
                       f"  — {ev.get('detail', '')}")
            if args.full and ev.get("stdout"):
                out.append(f"       stdout: {str(ev['stdout']).strip()[:400]}")
        elif t == "invalid_action":
            out.append(f"     ✗ invalid  kind={ev.get('kind')}  {ev.get('reason')}")
        elif t == "submit_repair":
            out.append(f"     ↻ repair  kind={ev.get('kind')}  reason={ev.get('reason')}"
                       f"  (flag×{ev.get('flag_retries')}, script×{ev.get('script_repairs')})")
        elif t == "escalate":
            out.append(f"     ↑ escalate  {ev.get('from_model')} → {ev.get('to_model')}")
        elif t == "memory" and ev.get("injected"):
            out.append(f"     · memory  hits={ev.get('hits')}  {ev.get('chars')}c")
        elif args.full:
            # 其余事件（start / server_metrics / probe …）只在 --full 下露出来
            keys = {k: v for k, v in ev.items()
                    if k not in ("type", "ts", "t_ms")}
            out.append(f"     · {t}  {json.dumps(keys, ensure_ascii=False)[:300]}")

    summary = next((r for r in reversed(rows) if r.get("type") == "summary"), {})
    if summary:
        out += [
            "",
            "## 结论",
            f"verdict={summary.get('verdict')}  reason={summary.get('reason')}  "
            f"turns={summary.get('turns')}  wall={summary.get('wall_s', 0):.1f}s",
            f"首次提交={summary.get('first_submit_verdict')}/"
            f"{summary.get('first_submit_reason')}  "
            f"flag重试={summary.get('submit_retries') or 0}  "
            f"脚本修复={summary.get('script_repairs') or 0}  "
            f"借轮次={summary.get('bonus_turns') or 0}",
        ]
        rep = summary.get("replay") or {}
        if rep:
            out.append(f"重放={rep.get('verdict')}/{rep.get('reason')}  {rep.get('detail', '')}")

    text = "\n".join(out)
    if args.out:
        Path(args.out).expanduser().write_text(text + "\n", encoding="utf-8")
        _p(f"已写入 {args.out}")
    else:
        _p(text)
    return 0


# ---------------------------------------------------------------- batch

def _split_pool(args, cfg):
    """按 --split 取题池；跑 test 时过守卫。返回 (题池, 错误码或 None)。"""
    pool = load_index()
    which = getattr(args, "split", "all") or "all"
    if which == "all":
        return pool, None
    path = Path(args.split_file).expanduser() if args.split_file else splits.default_split_path()
    try:
        sp = splits.load_split(path)
    except (FileNotFoundError, ValueError) as e:
        _p(str(e))
        return None, 2
    current = splits.index_sha256()
    if sp.get("index_sha256") and sp["index_sha256"] != current:
        msg = f"划分文件对应的题库索引已变（{sp['index_sha256'][:12]} ≠ {current[:12]}）"
        if which == "test":
            _p(msg + "，test 不能沿用旧划分。请和团队确认后重新冻结划分。")
            return None, 2
        _p("[warn] " + msg + "；dev 继续，但别据此下结论。")
    pool = splits.filter_tasks(pool, which, sp)
    if which == "test":
        fp = {**cfg.fingerprint(), "split": which,
              "split_test": sorted(sp["test"]), "split_index": sp.get("index_sha256")}
        try:
            _p("[guard] " + splits.guard_test_run(config.RUNS_ROOT, cfg.batch,
                                                  splits.config_hash(fp), args.confirm_test))
        except PermissionError as e:
            _p(str(e))
            return None, 2
    _p(f"[split] {which}：{len(pool)} 题（{path}）")
    return pool, None


def cmd_batch(args) -> int:
    cfg = _build_cfg(args)
    shard = None
    if args.shard:
        i, n = args.shard.split("/")
        shard = (int(i), int(n))
    pool, err = _split_pool(args, cfg)
    if err is not None:
        return err
    tasks = select(
        pool,
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
    hook = None
    if getattr(args, "events_stdout", False):        # 网页经 TACC 跑题时，按行把事件打进 Slurm 日志
        from .remote import events_printer
        hook = events_printer
    runner.run(tasks, resume=not args.no_resume, order=args.order, hook=hook)
    results = runner.load_results()
    _p("")
    _p(metrics.render(metrics.summarize(results)))
    return 0


# ---------------------------------------------------------------- report

def _load_batch(path: Path) -> list[dict]:
    """读 runs.jsonl。run_dir 指向别处的绝对路径（例如从 TACC 下载回来）时，改指批次目录下的
    同名子目录——否则本机出报告时失败归因与整批缓存命中率都读不到 trace。"""
    rows = storage.read_records(path)
    for r in rows:
        d = r.get("run_dir")
        if d and not Path(d).exists() and (path.parent / Path(d).name).exists():
            r["run_dir"] = str(path.parent / Path(d).name)
    return rows


def cmd_report(args) -> int:
    path = config.RUNS_ROOT / args.batch / "runs.jsonl"
    if not path.exists():
        _p(f"找不到 {path}")
        return 1
    results = _load_batch(path)
    s = metrics.summarize(results, gpu=gpu.load_ledger(path.parent))
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
        rs = _load_batch(p)
        batches[n] = metrics.summarize(rs, with_tags=False, gpu=gpu.load_ledger(p.parent))
    if not batches:
        return 1
    if args.ref and args.ref not in batches:
        _p(f"--ref {args.ref} 不在已加载的批次里")
        return 1
    table = metrics.compare(batches, keys=metrics.E3_KEYS if args.e3 else None,
                            ref=args.ref, guard_pts=args.guard_pts)
    _p(table)
    if args.md:
        out = Path(args.md).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(table + "\n", encoding="utf-8")
        _p(f"\nMarkdown 已写入 {out}")
    return 0


def cmd_pair(args) -> int:
    loaded = {}
    for name in (args.a, args.b):
        p = config.RUNS_ROOT / name / "runs.jsonl"
        if not p.exists():
            _p(f"找不到 {p}")
            return 1
        loaded[name] = _load_batch(p)
    res = metrics.paired(loaded[args.a], loaded[args.b], key=args.key)
    text = metrics.render_paired(res, args.a, args.b)
    _p(text)
    if args.md:
        out = Path(args.md).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("```\n" + text + "\n```\n", encoding="utf-8")
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

    storage.write_records(path, results)

    s = probe.summarize(results)
    _p("")
    _p(probe.render(s, args.mode))
    _p(f"\n结果：{path}")
    return 0


# ---------------------------------------------------------------- web

def cmd_chat_once(args) -> int:
    from .remote import chat_once
    cfg = _build_cfg(args)
    message = Path(args.message_file).read_text(encoding="utf-8")
    history = json.loads(Path(args.history_file).read_text(encoding="utf-8")) if args.history_file else []
    task = None
    if args.task:
        task = next((t for t in load_index() if t.name == args.task), None)
        if task is None:      # 以前静默当成"没挂题"来答，上传题交不上去时就是这样被掩盖的
            print(f"[fp] warn: challenge {args.task} is not in the staged task set; answering without it",
                  file=sys.stderr, flush=True)
    workdir = Path(args.workdir) if args.workdir else Path.cwd() / "chat-work"
    reply = chat_once(cfg, message, history, task=task, use_tools=not args.no_tools, workdir=workdir)
    return 0 if not reply.get("error") else 1


def cmd_web(args) -> int:
    from app.server import serve

    serve(host=args.host, port=args.port, model=args.model,
          base_url=args.base_url, track=args.track,
          metrics_url=getattr(args, "metrics_url", "") or "",
          replay_dir=getattr(args, "replay", "") or "")
    return 0


# ---------------------------------------------------------------- 工具

def _resolve_endpoint(args) -> tuple[str, str, str]:
    """(model, base_url, api_key)。Track H 一个 --track H 就够，其余自动补全。

    Track H 刻意不读 FLAGPILOT_BASE_URL / FLAGPILOT_API_KEY：deploy/env.sh 把它们
    设成了 vLLM 节点地址和 "EMPTY"，在集群上沿用会把托管模型的请求打到本地服务上。
    """
    if getattr(args, "track", "L") == "H":
        model = args.model or TRACK_H["model"]
        base = args.base_url or os.environ.get("FLAGPILOT_H_BASE_URL") or TRACK_H["base_url"]
        key = args.api_key or track_h_api_key()
        if not key and not str(model).lower().startswith("mock"):
            raise SystemExit(
                "Track H 需要 DeepSeek 密钥：设置环境变量 "
                + " 或 ".join(TRACK_H["key_env"]) + "，或用 --api-key 传入")
        return model, base, key or "EMPTY"
    model = args.model or os.environ.get("FLAGPILOT_MODEL") or "mock"
    # gemini-* 没指定端点时走 Google 的 OpenAI 兼容端点，密钥在 make_model_spec 里从 GEMINI_API_KEY / .env 补上
    default = GEMINI["base_url"] if str(model).lower().startswith("gemini") else "http://127.0.0.1:8000/v1"
    base = args.base_url or os.environ.get("FLAGPILOT_BASE_URL") \
        or os.environ.get("SERVICE_BASE_URL") or default
    return model, base, args.api_key or os.environ.get("FLAGPILOT_API_KEY", "EMPTY")


def _build_cfg(args) -> RunConfig:
    name, base, key = _resolve_endpoint(args)
    # H 轨就是 DeepSeek：不靠 host 猜（FLAGPILOT_H_BASE_URL 可能指向代理）。
    extra = {"provider": "deepseek"} if getattr(args, "track", "L") == "H" else {}
    model = make_model_spec(
        name,
        base,
        thinking=bool(getattr(args, "thinking", False)),
        api_key=key,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        metrics_url=args.metrics_url or "",
        price_in_per_mtok=args.price_in,
        price_out_per_mtok=args.price_out,
        **extra,
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
        hybrid=Hybrid(
            enabled=bool(getattr(args, "hybrid", False)),
            stall_turns=int(getattr(args, "stall_turns", 3)),
            max_escalations=int(getattr(args, "max_escalations", 1)),
            max_hosted_tokens=int(getattr(args, "max_hosted_tokens", 20_000)),
        ),
        replay_timeout_s=args.replay_timeout,
        seeds=getattr(args, "seeds", 1),
        memory=bool(getattr(args, "memory", False)),
        self_check=not getattr(args, "no_self_check", False),
        material_free=not getattr(args, "no_material_check", False),
        repair_extends_budget=not getattr(args, "repair_within_turns", False),
    )


def cmd_dataset(args) -> int:
    """拉取上游数据集并转换成本项目题库格式。

    产物不含明文 flag（只留 sha256），也不随仓库分发——对应 proposal 的
    "Data is fetched at run time, not redistributed"。
    """
    from . import datasets

    cache = Path(args.cache).expanduser() if args.cache else datasets.default_cache()
    _p(f"[dataset] 上游：{datasets.REPO_URL}")
    _p(f"[dataset] 缓存：{cache}")
    try:
        repo = datasets.fetch(cache, force=args.refresh)
    except Exception as e:
        _p(f"[dataset] 拉取失败：{type(e).__name__}: {e}")
        _p("[dataset] 需要本机能访问 GitHub；也可以先手动 clone，再用 --cache 指过去")
        return 1
    out = (Path(args.out).expanduser() if args.out
           else config.PROJECT_ROOT / "datasets" / f"nyu-{args.split}")
    stats = datasets.convert(repo, args.split, out, include_compose=args.include_compose)
    _p(f"[dataset] 已转换 {stats['tasks']} 题，排除 {stats['excluded']} 条（见 EXCLUDED.md）")
    _p(f"[dataset] 题库：{stats['root']}")
    _p(f"[dataset] 下一步：FLAGPILOT_TASKS={stats['root']} python3 -m flagpilot list")
    return 0


def cmd_writeup(args) -> int:
    output = writeup.generate(Path(args.run_dir), Path(args.out) if args.out else None)
    _p(f"Writeup saved: {output}")
    return 0


def cmd_encrypt_history(args) -> int:
    count = storage.protect_history(Path(args.root))
    _p(f"Encrypted {count} history files.")
    return 0


def cmd_storage_keygen(args) -> int:
    storage.generate_key(Path(args.out))
    _p(f"Encryption key saved: {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="flagpilot", description="重放验证型 CTF Agent")
    sub = ap.add_subparsers(dest="cmd", required=True)

    wr = sub.add_parser("writeup", help="Generate a solution writeup from a completed run")
    wr.add_argument("--run-dir", required=True)
    wr.add_argument("--out", help="Output path; encrypted mode adds .enc")
    wr.set_defaults(func=cmd_writeup)

    eh = sub.add_parser("encrypt-history", help="Encrypt existing saved records and completed workspaces")
    eh.add_argument("--root", default=str(config.RUNS_ROOT))
    eh.set_defaults(func=cmd_encrypt_history)

    kg = sub.add_parser("storage-keygen", help="Create a local storage encryption key")
    kg.add_argument("--out", required=True)

    ds = sub.add_parser("dataset", help="下载并转换公开数据集（NYU CTF Bench，运行时拉取不入库）")
    ds.add_argument("--name", default="nyu", choices=["nyu"])
    ds.add_argument("--split", default="dev", choices=["dev", "test"])
    ds.add_argument("--out", default="", help="输出题库目录（默认 <项目>/datasets/nyu-<split>）")
    ds.add_argument("--cache", default="", help="上游仓库缓存目录（默认 ~/.cache/flagpilot/nyu-ctf-bench）")
    ds.add_argument("--refresh", action="store_true", help="已有缓存时先 git pull")
    ds.add_argument("--include-compose", action="store_true",
                    help="连需要 docker compose 的题也收（离线 replay 跑不了，默认排除）")
    ds.set_defaults(func=cmd_dataset)
    kg.set_defaults(func=cmd_storage_keygen)

    d = sub.add_parser("doctor", help="环境自检")
    d.set_defaults(func=cmd_doctor)

    l = sub.add_parser("list", help="列出题目")
    l.add_argument("--category")
    l.add_argument("--scope", default="core")
    l.set_defaults(func=cmd_list)

    def add_common(p):
        p.add_argument("--batch", default="dev")
        p.add_argument("--track", default="L", choices=["L", "H"],
                       help="L=自部署 vLLM；H=DeepSeek V4.1（自动补端点/密钥/价目表）")
        p.add_argument("--model", default=None,
                       help="默认：Track L 用 mock，Track H 用 deepseek-flash")
        p.add_argument("--base-url")
        p.add_argument("--api-key")
        p.add_argument("--metrics-url", default="")
        p.add_argument("--temperature", type=float, default=0.2)
        p.add_argument("--max-tokens", type=int, default=ModelSpec.max_tokens)   # 4096，依据见 config.ModelSpec
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
        # ---- hybrid（proposal 的 optional escalation） ----
        p.add_argument("--hybrid", action="store_true",
                       help="启用 L→H 自动升级：连续 k 轮没有新证据就切到 H 轨")
        p.add_argument("--stall-turns", type=int, default=3,
                       help="连续多少轮没有新证据算 stall（默认 3）")
        p.add_argument("--max-escalations", type=int, default=1,
                       help="一次 attempt 内最多升级几次（默认 1）")
        p.add_argument("--max-hosted-tokens", type=int, default=20_000,
                       help="H 轨累计 token 上限，超了就不再升级/继续用 H")
        # ---- 判据与预算口径 ----
        p.add_argument("--no-self-check", action="store_true",
                       help="关掉提交前自检（run_script 动作）。复现旧行为时才需要")
        p.add_argument("--no-material-check", action="store_true",
                       help="关掉材料缺失重放。它会为每次成功重放多跑一遍脚本")
        p.add_argument("--repair-within-turns", action="store_true",
                       help="修复轮也占轮次预算（默认不占，以便兑现 proposal 的"
                            "'被拒给一次反馈'）")

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
    b.add_argument("--events-stdout", action="store_true",
                   help="把 trace 事件按行（@@ev）打印到标准输出：网页经 TACC 跑题时用来跟进度")
    b.add_argument("--order", default="lpt", choices=["lpt", "fifo"],
                   help="开跑顺序：lpt=预计最慢的先跑（默认，缩短整批墙钟）；fifo=题号顺序")
    b.add_argument("--split", default="all", choices=["all", "dev", "test"],
                   help="题池划分（默认 all 不变）；test 需要 --confirm-test")
    b.add_argument("--split-file", help="划分文件（默认 FLAGPILOT_SPLITS 或 <题库>/index/splits.json）")
    b.add_argument("--confirm-test", action="store_true",
                   help="确认这是定稿配置在 test 上的唯一一次运行")
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
    cp.add_argument("--e3", action="store_true",
                    help="E3 列：solve p50/p95、缓存、GPU-s/verified、$/verified")
    cp.add_argument("--ref", help="参照批次：多一列 verified 差值与护栏判定")
    cp.add_argument("--guard-pts", type=float, default=5.0, help="护栏阈值（百分点）")
    cp.set_defaults(func=cmd_compare)

    pr = sub.add_parser("pair", help="配对比较两个批次（精确 McNemar）")
    pr.add_argument("--a", required=True, help="基线批次")
    pr.add_argument("--b", required=True, help="对照批次")
    pr.add_argument("--key", default="verified", choices=["verified", "raw"])
    pr.add_argument("--md", help="导出到文件")
    pr.set_defaults(func=cmd_pair)

    pb = sub.add_parser("probe", help="记忆探针：不给工具，单轮问 flag")
    add_common(pb)
    pb.add_argument("--mode", default="brief", choices=["name", "brief", "files"])
    pb.add_argument("--tasks")
    pb.add_argument("--category")
    pb.add_argument("--scope", default="core")
    pb.add_argument("--limit", type=int)
    pb.add_argument("--workers", type=int, default=4)
    pb.set_defaults(func=cmd_probe)

    co = sub.add_parser("chat-once", help="答疑一轮（网页经 TACC 提问时由作业调用）：打印 @@reply")
    add_common(co)
    co.add_argument("--message-file", required=True)
    co.add_argument("--history-file", default="")
    co.add_argument("--task", default="")
    co.add_argument("--no-tools", action="store_true")
    co.add_argument("--workdir", default="")
    co.set_defaults(func=cmd_chat_once)

    w = sub.add_parser("web", help="演示页面")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8080)
    w.add_argument("--model", default=os.environ.get("FLAGPILOT_MODEL") or "mock")
    w.add_argument("--base-url")
    w.add_argument("--metrics-url", default="",
                   help="vLLM 的 /metrics；给了界面才能显示 prefix cache 命中率")
    w.add_argument("--track", default="L")
    w.add_argument("--replay", default="",
                   help="挂载一个目录下已完成的 run（只读回放），不需要模型与集群")
    w.set_defaults(func=cmd_web)

    sh = sub.add_parser("show", help="把一次 run 的 trace 复盘成时间线（离线，不需要模型）")
    sh.add_argument("--run-dir", required=True, help="包含 trace.jsonl 的目录")
    sh.add_argument("--full", action="store_true", help="连 observation 与脚本全文一起打印")
    sh.add_argument("--out", help="写到文件而不是 stdout")
    sh.set_defaults(func=cmd_show)
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
