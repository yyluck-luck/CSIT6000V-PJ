# Implementation Status

Last updated: **2026-11-19**. This document is the primary reference for project progress and the remaining TODO list. README files summarize it; new proposals should be identified separately from existing project commitments.

[PR #3](https://github.com/yyluck-luck/CSIT6000V-PJ/pull/3) merged the API integration, English writeups, and optional encrypted storage into `main` (`3d496bb`). Usage: [writeups and storage](WRITEUPS_AND_STORAGE.md).

Legend: ✅ implemented and exercised | 🟡 partially complete | ❌ pending

---

## 一、课程要求

| 要求 | 状态 | 证据 / 缺口 |
|---|---|---|
| Task scenario | ✅ | Offline CTF solving. The currently supplied local dataset has 54 tasks: Cryptography 16 / Forensics 12 / General Skills 26. |
| Frontend: input, progress, and result review | ✅ | Task selection/upload, SSE events, tool observations, flag/script/replay review, saved-run recovery, and Writeup download. |
| 后端：agent 工作流、模型 API、工具与数据 | ✅ | `agent.py` + `client.py` + `tools.py` + `sandbox.py` + `context.py` + `memory.py` |
| Evaluation and failure analysis | 🟡 | Metrics, Wilson intervals, comparisons, and failure labels are implemented. The real DeepSeek run supplies a correct-flag/script-error case; formal E1/E2/E3 results and the evaluation report remain pending. |
| Demo & Final Presentation | 🟡 | 实时 demo 可跑（`scripts/start.sh`）；**slides / 录屏 / 报告未做** |
| Execution on TACC | 🟡 | The teammate report dated 2026-11-17 records `crypto-005` as `verified` in job 29430 and through a local Web/SSH-tunnel run. Course-platform launch alignment and persistent result/GPU ledgers remain pending. |
| 交付物：repo / report / demo / slides | 🟡 | repo ✅、demo ✅、report ❌、slides ❌ |

The application and demo workflow are implemented. Formal experiment results, the final report, slides, and the recorded demo remain project deliverables.

---

## 二、Proposal 承诺

### 判据与流程

| 承诺 | 状态 | 说明 |
|---|---|---|
| 重放验证器（干净沙箱重跑 + 字面量检查 + 四态） | ✅ | `verify.py`；含 `material_text()` 豁免与 `reproduced` 降级 |
| Six-stage workflow including writeup | ✅ | Completed attempts generate an English trace-based report with CLI regeneration and Web download. |
| Typed submission feedback and bounded retries | 🟡 | One retry is implemented for a wrong flag. Replay script errors, timeouts, and literal-flag rejection do not yet trigger script repair. |
| Attempt isolation and resource limits | 🟡 | Process-level isolation and resource limits are implemented. WSL detects network namespaces, but the existing attachment read-only test still fails. Container-level isolation remains pending. |

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
| Track L: served model | 🟡 | The teammate report records a successful course-platform run with Qwen3.8-27B-AWQ-INT4. Align the proposal model scope and deployment guide with the course service, its one-hour limit, and its fixed serving parameters. |
| Track H: DeepSeek Flash | 🟡 | CLI/Web API configuration is integrated. A real `crypto-005` run completed model/tool/submission/replay execution: the flag matched, but the script failed (`unverified / script_error`). A verified H-track solve and invoice reconciliation remain pending. |
| Hybrid 自动升级 + stall 检测 + 升级预算 | ❌ | 完全未实现 |

### 日志与指标

| 承诺 | 状态 | 说明 |
|---|---|---|
| Track, timings, tokens, cache, and estimated cost | ✅ | Per-turn usage and application cost estimates are recorded. The real H-track run used 983 input / 181 output tokens, with an estimated cost of $0.000200. Provider invoice reconciliation remains pending. |
| GPU seconds | 🟡 | Collection and report support are implemented. The teammate TACC report records lost temporary run outputs and no persistent GPU ledger; course-platform persistence and ledger collection remain pending. |
| p50 / p95 延迟分位 | ✅ | 端到端墙钟 p50/p95（全部 attempt）、解题时间 p50/p95（仅 verified）、TTFT p50 |
| Encrypted storage | ✅ | Optional Fernet encryption covers records, writeups, and completed attempt files. Local history migration, compatible readers, and encrypted replay/downloads were exercised. Keys stay outside Git; [setup](WRITEUPS_AND_STORAGE.md). |

### 实验

| 实验 | 状态 | 说明 |
|---|---|---|
| E1 任务质量（B0–B3 等） | 🟡 | 档位、CLI、`compare` 全部打通；**20 题 × 3 seeds 数据未跑** |
| E2: verification, probes, and failure classification | 🟡 | Probes and labels are implemented. TACC and DeepSeek reports contain correct-flag/replay-failure examples; systematic evaluation and manual review of at least 30 cases remain pending. |
| E3: efficiency experiments | 🟡 | Client grids, latency summaries, comparisons, and GPU accounting are implemented. The teammate report says the course service fixes concurrency at 4 and enables prefix caching; adapt those experiment dimensions before collecting formal results. |
| E4 升级策略 | ❌ | 未实现（proposal 允许最先砍） |

### 数据集

| 承诺 | 状态 | 说明 |
|---|---|---|
| NYU CTF Bench dev21 / test103 | ❌ | 未接入 |
| InterCode-CTF scope | 🟡 | The supplied dataset derives from the InterCode/picoCTF tasks. The current local index contains 54 tasks; freeze its exact scope and reconcile it with the proposal and earlier 53-task documentation. |
| Cybench 21 | ❌ | 未接入 |
| RE 子集 | ❌ | 当前题库**没有 RE 题** |
| Current local dataset | ✅ | 54 tasks: General Skills 26 / Cryptography 16 / Forensics 12. All declared attachments were found. No-solutions inputs are stored outside the code repository. |

> 口径差异必须写进报告：要么补 loader，要么明确改为 picoCTF 并说明原因。
> 不能一边用 picoCTF 跑、一边写"在 NYU CTF Bench 上评测"。

题库的接入方式、为什么不随仓库分发、来源与授权边界：
见 **[DATASET.md](DATASET.md)**。上游是 InterCode（MIT），题目原始出处是 picoCTF（CMU），
两边的授权不同，仓库里用 `NOTICE` 分别声明。

### 工程与交付

| 承诺 | 状态 | 说明 |
|---|---|---|
| Repeated attempts / seeds | 🟡 | `--seeds` creates repeated attempts and records their identifiers. The current model request does not send a sampling seed; distinguish repetitions from provider seed control in the evaluation. |
| Wilson 置信区间 | ✅ | `metrics.py` |
| 配对比较 | ✅ | `pair` 子命令：按 (题, seed) 配对，给出修好 / 弄坏 / 都对 / 都错与精确 McNemar p 值 |
| Dev/test mechanism and test-run guard | 🟡 | Split generation and the configuration ledger are implemented. The current local dataset has no `splits.json`; agree on the index hash, generate the split, and freeze it before formal experiments. |
| Track launch paths | 🟡 | H-track API execution is exercised locally; L-track execution is recorded in the teammate report. Portable API startup documentation and course-platform deployment alignment remain to be finalized. |
| 录屏 demo / report / slides | ❌ | 未做 |

---

## 三、Remaining Project TODOs

Use this list for future task selection. Entries come from the project commitments and remaining gaps in the recorded runs; implementation suggestions outside this scope should be labeled separately.

| ID | Remaining task | Deliverable |
|---|---|---|
| 1 | Freeze the dataset and dev/test split | Agreed index hash, generated `splits.json`, and evaluation scope |
| 2 | Run E1 B0–B3 comparisons | Per-setting results, repeated-attempt configuration, confidence intervals, and paired comparisons |
| 3 | Finalize the TACC execution path and persistent outputs | Course-compatible launch instructions, persisted traces, and GPU-seconds ledger; include the reported interpreter/PATH fix |
| 4 | Complete H-track verification and billing reconciliation | A real `verified` solve plus recorded usage checked against the provider bill; the initial API workflow has already run |
| 5 | Run E2 probes and review at least 30 classifications | Raw/verified comparison, probe statistics, reviewed labels, and failure analysis |
| 6 | Adapt and run E3 under the available serving constraints | Agreed experiment grid, quality guard, timings, and available cost/cache/GPU measurements |
| 7 | Prepare the report, slides, and recorded demo | Final course deliverables |
| 8 | Align benchmark and model descriptions with the actual evaluation | Consistent proposal/report/dataset/model scope |
| 10 | Decide whether to implement E4 / Hybrid | Optional escalation work, as allowed by the proposal |
| 11 | Complete typed replay feedback and bounded submission repair | Feedback for script errors/timeouts/literal rejection within attempt and retry budgets |

Completed from the previous gap list: **item 9, Writeup generation**. English reports, Web downloads, CLI regeneration, and optional encrypted storage were merged in PR #3.

---

## 四、Recorded Verification

| Scope | Recorded result |
|---|---|
| Focused feature checks | 71 storage/writeup/API/metrics checks passed, using synthetic data, mock models, and local HTTP servers. |
| Complete offline suite | 209 of 210 passed. The remaining `test_challenge_is_readonly` failure is also present on the baseline in this WSL namespace; no new failing cases were found. |
| Mock CLI and Web | `general-004` reached `verified` in 3 turns. |
| Real DeepSeek Flash | `crypto-005`: 2 turns, about 2.84 s, correct flag, `unverified / script_error` because the submitted reproduction script failed. |
| Writeup and storage | Local history migration covered 15 record files. The previous real run was recovered after restart and its encrypted Writeup downloaded with the original verdict preserved. |
| Team-reported TACC result | The 2026-11-17 report records job 29430 as `verified`, prefix-cache samples of 20–27%, and a successful local Web/tunnel run. Persistent traces/GPU accounting and formal experiments remain separate follow-up work. |

Reproduce the local checks from the `flagpilot/` directory:

```bash
# Unit and integration tests (configure the external dataset first).
python3 -m unittest discover -s tests -t . -q

# Focused writeup/encryption checks; install the optional encryption dependency.
python3 -m unittest tests.test_storage_writeup tests.test_writeup_web -q

# Environment and dataset checks.
python3 -m flagpilot doctor

# H-track example; loads credentials from the ignored local .env.
scripts/track_h.sh h-smoke --tasks crypto-005 --workers 1 --turns 6
```

Evidence sources: merged PR #3, local trace/test results, and the team-supplied report
`TACC-实机验证与v2推进状况(1).md` dated 2026-11-17. TACC entries above reflect that
reported snapshot; its uncommitted platform changes still need to be reconciled with this repository.
