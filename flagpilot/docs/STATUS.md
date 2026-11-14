# 实现状态

最后核对：2026-11-14。**这张表以代码和实测为准，不以计划为准。**
写报告、做答辩、分工排期都用这一份。

图例：✅ 已实现并验证 ｜ 🟡 部分实现 ｜ ❌ 未实现或未验证

---

## 一、课程要求

| 要求 | 状态 | 证据 / 缺口 |
|---|---|---|
| 选定任务场景 | ✅ | CTF 解题，53 题（Cryptography 16 / Forensics 12 / General 25） |
| 前端：输入、过程可见、结果可复核 | ✅ | 选题与上传、SSE 实时链路、工具输出可折叠、flag + `solve.py` + replay 现场、`?run=` 深链回放 |
| 后端：agent 工作流、模型 API、工具与数据 | ✅ | `agent.py` + `client.py` + `tools.py` + `sandbox.py` + `context.py` + `memory.py` |
| 评测：代表性用例、指标、至少一个失败分析 | 🟡 | 指标、Wilson、批次对比、12 类归因都有；**正式数据与成文失败分析未产出** |
| Demo & Final Presentation | 🟡 | 实时 demo 可跑（`scripts/start.sh`）；**slides / 录屏 / 报告未做** |
| 在 TACC 上执行 | 🟡 | `deploy/*.slurm` 就绪；**尚未在集群实机跑过** |
| 交付物：repo / report / demo / slides | 🟡 | repo ✅、demo ✅、report ❌、slides ❌ |

课程最低交付完成度约 **70–75%**：系统可跑、demo 可演，缺"数据 + 报告 + 展示"。

---

## 二、Proposal 承诺

### 判据与流程

| 承诺 | 状态 | 说明 |
|---|---|---|
| 重放验证器（干净沙箱重跑 + 字面量检查 + 四态） | ✅ | `verify.py`；含 `material_text()` 豁免与 `reproduced` 降级 |
| 六步流程：读 → 想 → 做 → 提交 → 重放 → writeup | 🟡 | 前五步完整；**writeup 未实现** |
| 提交失败带类型反馈重试（最多两次） | 🟡 | 仅 `wrong` 给 1 次重试；`literal` / `timeout` / `not_reproduced` 不重试 |
| 每 attempt 一个容器 / 无 root / 无网 / 只读 / 资源限制 | 🟡 | 只读 + rlimit ✅；**非容器**；macOS 无 netns，裸 socket 实测可出网；TACC 待复验 |

### 工具箱

| 承诺 | 状态 | 说明 |
|---|---|---|
| Python + crypto / math | ✅ | `python` 动作，标准库可用 |
| shell 通用命令 | ✅ | `shell` 动作，白名单环境变量 |
| file / strings / decode | ✅ | `decode` 支持 rot13 / base64 / hex / url |
| SMT（z3） | ❌ | 未适配；集群是否安装未知 |
| binutils / 反汇编 / 调试器 | 🟡 | 有 `shell` 就能调，但不保证集群装了 `objdump` / `gdb` |

### 模型与轨道

| 承诺 | 状态 | 说明 |
|---|---|---|
| L 轨：vLLM + ≤14B | 🟡 | 脚本就绪（`deploy/run.slurm`），**未实跑**；本地是 Ollama 27B（行为替身，不算 L 轨结果） |
| H 轨：DeepSeek V4.1 | 🟡 | `--track H` 只是通用 OpenAI 客户端 + 计价字段，未封装成一命令 |
| Hybrid 自动升级 + stall 检测 + 升级预算 | ❌ | 完全未实现 |

### 日志与指标

| 承诺 | 状态 | 说明 |
|---|---|---|
| track / 阶段耗时 / token / cache / 成本 | 🟡 | 前四项 ✅；成本字段有但单价默认 0 |
| GPU 秒 | ❌ | 未采集（需 `nvidia-smi` 采样或 slurm acct） |
| p50 / p95 延迟分位 | 🟡 | 只有 TTFT p50；端到端未做分位统计 |

### 实验

| 实验 | 状态 | 说明 |
|---|---|---|
| E1 任务质量（B0–B3 等） | 🟡 | 档位、CLI、`compare` 全部打通；**20 题 × 3 seeds 数据未跑** |
| E2 验证（raw vs verified、探针、失败分类） | 🟡 | `probe` 三档 + `tagger` 12 类都在；**数据未跑，≥30 例人工复核未做** |
| E3 效率（截断 vs 摘要、cache、轮次、并发、p50/p95、GPU 秒） | 🟡 | 截断/压缩、cache 开关、轮次与并发参数齐备；**p95、8 并发、GPU 秒缺** |
| E4 升级策略 | ❌ | 未实现（proposal 允许最先砍） |

### 数据集

| 承诺 | 状态 | 说明 |
|---|---|---|
| NYU CTF Bench dev21 / test103 | ❌ | 未接入 |
| InterCode-CTF 46 | 🟡 | **数据源就是它**，但按"取证 + 入门密码"筛过：InterCode 全量 → 保留 53 题（不是承诺的 46 题口径） |
| Cybench 21 | ❌ | 未接入 |
| RE 子集 | ❌ | 当前题库**没有 RE 题** |
| 实际使用 | — | picoCTF 53 题（General 25 / Crypto 16 / Forensics 12），逐题 `source` 可追溯 |

> 口径差异必须写进报告：要么补 loader，要么明确改为 picoCTF 并说明原因。
> 不能一边用 picoCTF 跑、一边写"在 NYU CTF Bench 上评测"。

题库的接入方式、为什么不随仓库分发、来源与授权边界：
见 **[DATASET.md](DATASET.md)**。上游是 InterCode（MIT），题目原始出处是 picoCTF（CMU），
两边的授权不同，仓库里用 `NOTICE` 分别声明。

### 工程与交付

| 承诺 | 状态 | 说明 |
|---|---|---|
| 3 seeds | ✅ | `--seeds` |
| Wilson 置信区间 | ✅ | `metrics.py` |
| 配对比较 | ❌ | 有批次并排（`compare`），无配对显著性检验 |
| test split 只跑一次 | ❌ | 无守卫，靠人守 |
| 一命令双轨服务 | 🟡 | L 有一体式 slurm；H 没有 |
| 录屏 demo / report / slides | ❌ | 未做 |

---

## 三、缺口清单（按性价比排序）

| # | 任务 | 为什么优先 | 产出 |
|---|---|---|---|
| 1 | 跑 E1：20 题 × 3 seeds × 四档 | 报告的地基；代码已够用，只差算力 | `runs/e1-*.md` |
| 2 | TACC 上跑通一次 vLLM | 拿到真实 cache 命中率与吞吐，替换文档里的估值 | `cache_hit_rate`、作业日志 |
| 3 | 跑 E2：探针 + 归因抽检 30 例 | 支撑"raw vs verified"的核心论点 | 归因分布表 |
| 4 | 写 report / slides | 课程交付硬要求 | 报告、幻灯片 |
| 5 | 对齐 benchmark 或改口径 | 避免"名不符实"的致命问题 | 报告中的数据集章节 |
| 6 | writeup 生成 | 补上 proposal 第 6 步 | `writeup.md` |
| 7 | E4 / Hybrid | proposal 允许最先砍 | 可放弃 |

---

## 四、当前可复现的验证

```bash
# 单元测试：121 条
python3 -m unittest discover -s tests -t . -q

# 自检：11 项（不花 GPU、不需要模型）
bash scripts/verify.sh

# 环境与沙箱能力（含泄漏检查）
python3 -m flagpilot doctor

# 一道题的完整链路（需要模型端点）
python3 -m flagpilot run --task crypto-005 \
  --model qwen3:8b --base-url http://127.0.0.1:11434/v1
```

最近一次实测（Ollama + qwen3.8:27b，`crypto-005`）：

```
verdict=verified  turns=2  e2e=10.31s  tokens=1276
链路：decode → submit → 重放验证
```

注意这是**本地行为验证**，不是 L 轨结果，也不是 TACC 结果。
