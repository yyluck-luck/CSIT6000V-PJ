# FlagPilot — 开发者说明

> **给助教/评审的安装与复现说明在仓库根 [`../README.md`](../README.md)**（英文，按 NSDI artifact 规范组织）。
> 本文件是给团队成员的上手说明，讲代码怎么组织、改完怎么验。

重放验证型 CTF Agent。**解出答案不算通过**：模型交出的 `solve.py` 要在只有题目附件的新目录里重跑、复现出同一个 flag，才计一次 `verified`。

---

## 30 秒上手

```bash
cd flagpilot
export FLAGPILOT_TASKS="$PWD/demo-tasks"     # 内置 3 道合成题，仓库自带
python3 -m flagpilot batch --batch demo --model mock --limit 3   # 期望 3/3 verified
python3 -m flagpilot web --model mock        # 浏览器打开 127.0.0.1:8080
```

不需要装任何第三方包，不需要 GPU，不需要 API key。

**只想看一次真实运行长什么样**（不接模型、不接集群）：

```bash
python3 -m flagpilot web --replay /path/to/runs/<batch>   # 挂载已完成的 run，打印每条地址
python3 -m flagpilot show --run-dir /path/to/runs/<batch>/<task>-s0   # 终端里看时间线
```

---

## 改完代码先跑这两条

```bash
bash scripts/verify.sh                                       # 12 项自检，不花 GPU
python3 -m unittest discover -s tests -t . -q                # 348 条单元/集成测试
```

`verify.sh` 会**自己造一套合成题库**（临时目录，跑完删掉），所以它不依赖外部题库，
也不假设"题库里某道题 mock 恰好能解"。助教刚 clone 下来直接跑也能 12/12。

真实实验才需要 `FLAGPILOT_TASKS` 指向 picoCTF 题库（见 [docs/DATASET.md](docs/DATASET.md)）。

**可选依赖**（`requirements.txt`）：`z3-solver` 解锁 `smt` 动作、`cryptography` 解锁
加密存储、`pycryptodome` / `gmpy2` 供部分 crypto 题使用。全都不装也能跑主流程。

---

## 目录结构

```
flagpilot/
├── flagpilot/              核心包（CLI + agent）
│   ├── agent.py            ReAct 循环、预算、循环守卫
│   ├── tools.py            动作解析 + 工具箱（shell/python/file/strings/decode/submit）
│   ├── sandbox.py          进程级沙箱：只读附件、rlimit、超时杀进程组
│   ├── verify.py           四态判据 + 重放 + 字面量扫描
│   ├── config.py           预算、消融档位、模型端点（唯一构造入口）
│   ├── context.py          上下文压缩（中段折叠成摘要）
│   ├── memory.py           跨 run 记忆（只存结论，不存 flag）
│   ├── tagger.py           失败归因（12 类）
│   ├── metrics.py          Wilson 区间、p50/p95、配对检验、Markdown 报告、批次 / E3 对比
│   ├── pricing.py          H 轨价目表与逐轮计价（缓存命中 / 未命中 / 高峰价）
│   ├── gpu.py              GPU 秒账本（nvidia-smi 采样按批次切片）
│   ├── splits.py           dev / test 划分与"test 只跑一次"守卫
│   ├── storage.py          可选 Fernet 加密存储（所有 JSONL 读写都收敛在这里）
│   ├── writeup.py          从 trace 渲染英文 writeup（不调模型，零 token）
│   ├── chat.py             答疑会话
│   ├── custom.py           上传自建题（zip 安全解压）
│   ├── probe.py            记忆探针（不给工具单轮问 flag）
│   ├── runner.py           批跑、分片、断点续跑
│   └── cli.py              子命令入口
├── app/                    Web 界面
│   ├── server.py           零依赖 HTTP + SSE
│   └── index.html          单页前端（无构建步骤）
├── demo-tasks/             ★ 内置合成题（3 道），供快速验证与自检
├── deploy/                 TACC / Slurm 提交脚本（通用集群模板）
├── scripts/                本地启停、自检、划分生成、H 轨一命令
├── tests/                  210 条测试
└── docs/                   设计、题库、TACC、实现状态
```

运行产物都在 `runs/`，不进 git：

```
runs/<batch>/runs.jsonl                  一次 attempt 一行（汇总，唯一可分析的账本）
runs/<batch>/<task>-s<seed>/trace.jsonl  全事件 + summary（唯一事实源）
runs/<batch>/<task>-s<seed>/writeup.md   自动生成的英文 writeup
runs/<batch>/<task>-s<seed>/replay/      重放现场：solve.py + 干净 challenge/
runs/<batch>/gpu.jsonl                   GPU 秒账本（检测到 GPU 时才写）
```

---

## 模块分工

| 模块 | 文件 | 上手成本 |
|---|---|---|
| Agent / 判据 | `flagpilot/agent.py`、`verify.py` | 需要理解四态与重放逻辑 |
| 前端 | `app/index.html` | 单文件，无构建，改完刷新即可 |
| 评测 / 指标 | `flagpilot/metrics.py`、`runner.py`、`tagger.py` | 主要看 Wilson 与归因表 |
| 存储 / 加密 | `flagpilot/storage.py`、`writeup.py` | 见 [docs/WRITEUPS_AND_STORAGE.md](docs/WRITEUPS_AND_STORAGE.md) |
| 部署 | `deploy/*.slurm` | 只需要改 `env.sh` 里的四个变量 |

---

## 设计文档

| 文档 | 内容 |
|---|---|
| [../README.md](../README.md) | **安装与复现（英文，给评审）** |
| [docs/DESIGN.md](docs/DESIGN.md) | 架构、模块职责、关键取舍（为什么重放、为什么零依赖、消融怎么切） |
| [docs/DATASET.md](docs/DATASET.md) | 题库怎么接：数据契约、为什么独立分发、来源与版权 |
| [docs/TASKS.md](docs/TASKS.md) | 题库目录结构、字段说明、怎么加自己的题 |
| [docs/TACC.md](docs/TACC.md) | 集群提交流程与常见错误 |
| [docs/STATUS.md](docs/STATUS.md) | 逐条对照 proposal 与课程要求的实现状态 |
| [docs/WRITEUPS_AND_STORAGE.md](docs/WRITEUPS_AND_STORAGE.md) | writeup 生成与加密存储 |

---

## 已知边界

这些是**设计层面的限制**，不是 bug，写报告和答辩时必须说明（根 README 第 6 节有英文版）：

1. **沙箱不是安全边界。** 与主进程同 uid，无 `chroot` / `seccomp` / `cgroup`；`challenge/` 的只读权限可以用 `chmod` 改回来。真正的防线是「flag 不进沙箱」+「重放用干净副本」。
2. **网络隔离是 `proxy-only`。** 只是把 `http_proxy` 指向死端口；实测裸 socket 仍可出网。
3. **`<run>` 等于任意命令执行。** 这是答疑功能的设计；只应在可信环境使用。
4. **字面量扫描是启发式。** 挡不住刻意混淆（把 flag 拆成整数数组之类）。
5. **Web 服务没有账号体系。** 默认只绑 `127.0.0.1`；不要用 `--host 0.0.0.0` 暴露到公网。
6. **无 RE 题。** 当前题库是 picoCTF 53 题（General 25 / Crypto 16 / Forensics 12），纯推理题没有自然的 `solve.py`，会被系统性判为 `unverified`。
