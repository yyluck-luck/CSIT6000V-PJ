# FlagPilot

重放验证型 CTF Agent。**解出答案不算通过**：模型交出的 `solve.py` 要在只有题目附件的新目录里重跑、复现出同一个 flag，才计一次 `verified`。

零第三方依赖（纯标准库，Python ≥ 3.11）。同一套代码可以接 vLLM、Ollama 或任意 OpenAI 兼容端点。

![执行链路](docs/screenshots/architecture.svg)

---

## 运行效果

**解题**：左侧选题，中间逐轮展示「思考 → 动作 → 工具真实输出 → 观察」，右侧是结论与预算指标。

![解题界面](docs/screenshots/01-solve.png)

**答疑**：同一个 agent 换到教学角色，可以挂某道题讲思路；模型要跑命令时用 `<run></run>` 包裹，真正在沙箱里执行。

![答疑界面](docs/screenshots/02-chat.png)

---

## 快速开始

### 前置

| 项 | 要求 |
|---|---|
| Python | ≥ 3.11（不需要装任何包） |
| 模型服务 | 任一 OpenAI 兼容端点：Ollama（本地最省事）、vLLM（集群）、或 mock（无模型） |
| 题库 | **不在本仓库**（体积 + 明文答案 + 第三方授权）。并列放一份，用 `FLAGPILOT_TASKS` 指过去；见 [docs/DATASET.md](docs/DATASET.md) |

### 三步跑起来

```bash
# 1. 指向题库（并列存放，不拷进仓库）
export FLAGPILOT_TASKS=/path/to/ctf-tasks/no-solutions
#   没有现成题库时，可从上游重建：
#   python3 scripts/build_taskset.py --src /path/to/intercode/data/ctf --out ./ctf-tasks

# 2. 起模型 + 前端（默认模型 qwen3:8b，端口 8080）
./scripts/start.sh

# 3. 打开浏览器
open http://127.0.0.1:8080
```

先确认题库与索引一致，否则跑到缺题会静默空跑：

```bash
python3 -m flagpilot doctor        # 看「题库一致性」这一行
python3 scripts/verify_taskset.py --root "$FLAGPILOT_TASKS" --selftest
```

没有模型也想看界面：

```bash
python3 -m flagpilot web --model mock        # 内置策略，不联网
```

自检（不花 GPU、不需要模型，验证判据与装置是否正常）：

```bash
bash scripts/verify.sh          # 11 项检查
python3 -m flagpilot doctor     # 环境与沙箱能力
```

### 常用命令

| 想做的事 | 命令 |
|---|---|
| 换模型 | `./scripts/start.sh qwen3:14b`，或在网页顶栏直接切换 |
| 换端口 | `FP_PORT=8090 ./scripts/start.sh` |
| 只跑一道题（CLI） | `python3 -m flagpilot run --task crypto-005 --model qwen3:8b --base-url http://127.0.0.1:11434/v1` |
| 批量跑一个批次 | `python3 -m flagpilot batch --batch dev --scope core --workers 4 ...` |
| 出指标 | `python3 -m flagpilot report --batch dev` |
| 四档并排对比 | `python3 -m flagpilot compare --batches dev-B0,dev-B1,dev-B2,dev-B3` |
| 配对显著性（精确 McNemar） | `python3 -m flagpilot pair --a dev-B2 --b dev-B3` |
| E3 对比（Δ + 质量护栏） | `python3 -m flagpilot compare --e3 --ref <参照批次> --batches ...` |
| 生成 dev / test 划分 | `python3 scripts/make_split.py --root "$FLAGPILOT_TASKS" --seed 0` |
| 只跑 dev / 定稿后跑 test | `batch ... --split dev`；`batch ... --split test --confirm-test`（同一配置只允许一次） |
| H 轨（DeepSeek）一命令 | `scripts/track_h.sh h-dev --split dev --limit 5`（密钥写在 `.env`） |
| 不给工具问 flag | `python3 -m flagpilot probe --batch probe --mode brief` |
| 停止 | `./scripts/stop.sh` |

---

## 这个系统做了什么

一次 attempt 是固定的五步：

1. **题目输入** — 从题库索引读题面与附件清单（gold 只有 `sha256`，明文不进沙箱）。
2. **沙箱准备** — 附件复制进 `work/challenge/` 并设为只读，工作区可写。
3. **Agent 循环** — 模型每轮输出一个 JSON 动作，工具箱执行，观察回灌；有轮次 / token / 墙钟四道预算。
4. **提交** — `submit` 必须同时给 `flag` 和 `solve.py`。
5. **重放验证** — `solve.py` 被放进只有题目附件的干净目录从零执行，比对输出与提交值。

结论分四态：

| 结论 | 含义 |
|---|---|
| `verified` | flag 对，且脚本在干净目录复现出同一个 flag |
| `unverified` | flag 对，但脚本缺失或跑不出来（只有答案没有过程） |
| `wrong` | flag 不对 |
| `budget` | 轮次 / token / 时间 / 动作格式超限 |

自建题（上传）没有 gold，最高只能到 `reproduced`。

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
│   ├── chat.py             答疑会话
│   ├── custom.py           上传自建题（zip 安全解压）
│   ├── probe.py            记忆探针（不给工具单轮问 flag）
│   ├── runner.py           批跑、分片、断点续跑
│   └── cli.py              子命令入口
├── app/                    Web 界面
│   ├── server.py           零依赖 HTTP + SSE
│   └── index.html          单页前端（无构建步骤）
├── deploy/                 TACC 提交脚本
│   ├── env.sh              共享环境变量
│   ├── run.slurm           一体式：起 vLLM → 跑评测 → 出报告
│   ├── serve.slurm         分离式：只起服务
│   ├── client.slurm        分离式：只跑客户端
│   └── upload.sh           打包上传（强制排除答案表）
├── scripts/                本地启停、自检、划分生成、H 轨一命令
├── tests/                  179 条单元测试
└── docs/                   设计、题库、TACC、实现状态
```

运行产物都在 `runs/`，不进 git：

```
runs/<batch>/runs.jsonl                一次 attempt 一行（汇总）
runs/<batch>/<task>-s<seed>/trace.jsonl 全事件 + summary（唯一事实源）
runs/web/<run_id>/                      网页提交的运行，结构同上
runs/chat/<session>/chat.jsonl          答疑逐轮记录
runs/memory/tasks/<task>.jsonl          跨 run 记忆
runs/<batch>/gpu.jsonl                  GPU 秒账本（检测到 GPU 时才写）
runs/test_runs.jsonl                    test 守卫账本（每个最终配置一行）
```

---

## 已实现 / 未实现

完整对照（含 proposal 的 E1–E4、课程要求）见 **[docs/STATUS.md](docs/STATUS.md)**。摘要：

### 已实现并验证

- ReAct agent 循环 + 四道预算 + 重复动作守卫
- 六种动作：`shell` / `python` / `file` / `strings` / `decode` / `submit`
- 进程级沙箱：只读附件、`rlimit`、超时杀进程组、输出截断、能力探测
- 重放验证：哈希比对 → 脚本字面量扫描 → 干净目录重跑 → 四态结论
- E1 消融阶梯 B0→B3（每档只加一个变量），CLI 与网页都能跑
- 记忆探针（`name` / `brief` / `files` 三档）、失败归因 12 标签、Wilson 置信区间
- Web 界面：SSE 实时链路、工具输出可折叠、指标实时刷新、`?run=` 深链回放
- 手动上传题目（zip 安全解压）、答疑讲解（命令真执行但不参与评分）
- 上下文压缩（中段折成摘要）与跨 run 记忆（只存结论）
- TACC 部署脚本（一体式 / 分离式 / 打包上传 / 分片续跑；E3 网格一个作业跑完）
- 延迟分位：端到端与 verified 解题时间的 p50 / p95
- 配对检验（精确 McNemar）、E3 对比的 Δ 与 5 个点的质量护栏
- dev / test 划分 + "test 每个最终配置只跑一次"的守卫
- H 轨一命令（DeepSeek 预设 + 官方价目逐轮计价，对桩服务器测试）
- 179 条单元测试、11 项自检

### 未实现 / 未验证

| 项 | 状态 | 说明 |
|---|---|---|
| **TACC 实机运行** | ❌ 未跑过 | 脚本已就绪，`vLLM` 与 `slurm` 未在集群上实测；报告里不能写"已验证"。GPU 秒、E3 数据都卡在这一步 |
| E1 正式数据 | ❌ 未跑 | 20 题 × 3 seeds × 四档的表格还没出 |
| E2 人工复核 | ❌ 未做 | 归因 ≥30 例的人工抽检 |
| E3 效率实验 | 🟡 部分 | 网格、p95、8 并发、GPU 秒都已接好（`FP_E3=1`，见 docs/TACC.md 第 7 节）；**数据未跑** |
| E4 升级策略 | ❌ 未实现 | proposal 允许最先砍，当前没有 stall 检测与升级预算 |
| Writeup 生成 | ❌ 未实现 | 提交后只做重放，不产出解题报告 |
| Hybrid 双轨编排 | 🟡 部分 | H 轨已一命令化（`scripts/track_h.sh`），但没有 L→H 自动升级链路 |
| H 轨真实调用 | ❌ 未跑 | 只对桩服务器测过；需要 `DSV41_API_KEY` 先跑一题，核对成本与账单 |
| dev / test 划分 | 🟡 待冻结 | 生成器与守卫已就绪；`splits.json` 仍是 `proposed`，团队确认后改 `frozen` |
| Benchmark 对齐 | ❌ 不一致 | proposal 写 NYU CTF Bench / InterCode / Cybench，当前题库是 picoCTF 53 题（无 RE） |
| 容器级隔离 | ❌ 未做 | 只有进程级；macOS 上网络隔离是 `proxy-only`，实测可被裸 socket 绕过 |
| Flag 加密存储 | ❌ 未做 | `trace.jsonl` 里是明文，共享 `runs/` 等于共享答案 |

> 这些不是"做不完"，而是**当前版本的边界**。写报告、做答辩时按这张表讲，比含糊其辞更安全。

---

## 复现实验

### E1：消融阶梯

```bash
for b in B0 B1 B2 B3; do
  python3 -m flagpilot batch --batch e1-$b --ablate $b \
    --model qwen3-14b --base-url http://127.0.0.1:8000/v1 \
    --metrics-url http://127.0.0.1:8000/metrics \
    --scope core --limit 20 --seeds 3 --workers 1
done
python3 -m flagpilot compare --batches e1-B0,e1-B1,e1-B2,e1-B3 --md e1.md
python3 -m flagpilot pair --a e1-B2 --b e1-B3 --md e1-pair.md   # 相邻两档的配对检验（精确 McNemar）
```

`--workers 1`：并发会摊薄 prefix cache 的差值，E1/E3 必须单并发。

### E2：验证与归因

```bash
python3 -m flagpilot probe --batch e2-probe --mode brief --limit 20
python3 -m flagpilot report --batch e2-probe
```

`probe` 不给工具、单轮问 flag：命中说明模型可能是"背过"而不是"解出"。`report` 会同时给出 raw 准确率与 verified 率，以及失败归因分布。

### E3：效率网格

在 TACC 上跑：prefix cache 开 / 关各一个作业，每个作业跑 6 格（2 种观察策略；最佳策略上轮次 40 / 60、并发 4 / 8），结束时自动出对比表。

```bash
for pc in 1 0; do
  sbatch --export=ALL,FP_E3=1,FP_PREFIX_CACHE=$pc,FP_MAX_SEQS=8,FP_LIMIT=20,FP_SPLIT=dev deploy/run.slurm
done
python3 -m flagpilot compare --e3 --ref e3-pc1-trunc-t20-w1 \
  --batches e3-pc1-trunc-t20-w1,e3-pc1-summ-t20-w1,e3-pc0-trunc-t20-w1
```

对比表每格对参照格给 Δ，verified 率掉超过 5 个点标 ✗（质量护栏）。格子定义、GPU 秒口径、为什么并发格不报命中率：见 [docs/TACC.md](docs/TACC.md) 第 7 节。

### H 轨：DeepSeek（托管 API，不占 GPU）

```bash
# 密钥写进本目录的 .env（已 gitignore）：DSV41_API_KEY=...
scripts/track_h.sh h-smoke --limit 1             # 先冒烟一题，核对成本与 DeepSeek 后台账单
scripts/track_h.sh h-dev --split dev --workers 4
```

`--track H` 自动补齐模型 `deepseek-flash`、端点和价目表。成本按官方价目逐轮计：缓存命中 / 未命中 / 输出分开计价，工作日高峰时段价格翻倍（见 `flagpilot/pricing.py`）。

---

## 提交到 TACC

完整流程见 **[docs/TACC.md](docs/TACC.md)**，这里是五步概览：

```bash
# 0) 改 deploy/env.sh：allocation / 分区 / 模型路径（问 TA 要）
# 1) 本地打包上传（脚本强制排除 _private/ 与 with-solutions/）
./deploy/upload.sh <user>@<tacc-host> '~/flagpilot'

# 2) 登录节点下载模型到共享盘（计算节点没有外网）
ssh <user>@<tacc-host>
cd ~/flagpilot && hf download Qwen/Qwen3-14B --local-dir $WORK/models/Qwen3-14B

# 3) 提交一体式作业：起 vLLM → 等就绪 → 跑评测 → 出报告
sbatch --account=<alloc> --partition=<part> \
  --export=ALL,FP_BATCH=smoke,FP_LIMIT=20 deploy/run.slurm

# 4) 看进度
squeue -u $USER
tail -f logs/run-<jobid>.out

# 5) 取回结果
rsync -avz <user>@<tacc-host>:~/flagpilot/runs/ ./runs_remote/
```

E1 四档一次跑完（同一作业复用同一个服务，省三次排队）：

```bash
sbatch --export=ALL,FP_BATCH=e1,FP_LIMIT=20,FP_WORKERS=1,FP_ABLATIONS="B0 B1 B2 B3" deploy/run.slurm
```

跑正式实验前先冻结 dev / test 划分（[docs/TACC.md](docs/TACC.md) 第 3 节）；E3 网格见第 7 节。

---

## 设计文档

| 文档 | 内容 |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | 架构、模块职责、关键取舍（为什么重放、为什么零依赖、消融怎么切） |
| [docs/DATASET.md](docs/DATASET.md) | **题库怎么接**：数据契约、为什么独立分发、来源与版权 |
| [docs/TASKS.md](docs/TASKS.md) | 题库目录结构、字段说明、怎么加自己的题 |
| [docs/TACC.md](docs/TACC.md) | 完整提交流程与常见错误 |
| [docs/STATUS.md](docs/STATUS.md) | 逐条对照 proposal 与课程要求的实现状态 |

---

## 已知边界

这些是**设计层面的限制**，不是 bug，写报告时必须说明：

1. **沙箱不是安全边界。** 与主进程同 uid，无 `chroot` / `seccomp` / `cgroup`；`challenge/` 的只读权限可以用 `chmod` 改回来；`shell` 能读进程权限内的任意绝对路径。
2. **网络隔离是 `proxy-only`。** 只是把 `http_proxy` 指向死端口；实测裸 socket 仍可出网。要真正隔离需要容器或 `--network none`。
3. **`<run>` 等于任意命令执行。** 这是答疑功能的设计；只应在可信环境使用。
4. **flag 明文落盘。** `trace.jsonl` 的 `submit` 事件保留明文便于复核，共享 `runs/` 等于共享答案。
5. **Web 服务没有账号体系。** 默认只绑 `127.0.0.1`；不要用 `--host 0.0.0.0` 暴露到公网。

---

## 团队协作

| 模块 | 文件 | 上手成本 |
|---|---|---|
| Agent / 判据 | `flagpilot/agent.py`、`verify.py` | 需要理解四态与重放逻辑 |
| 前端 | `app/index.html` | 单文件，无构建，改完刷新即可 |
| 评测 / 指标 | `flagpilot/metrics.py`、`runner.py`、`tagger.py` | 主要看 Wilson 与归因表 |
| 部署 | `deploy/*.slurm` | 只需要改 `env.sh` 里的四个变量 |

改完代码先跑：

```bash
bash scripts/verify.sh
python3 -m unittest discover -s tests -t . -q
```
