# 题库：依赖关系、分发方式与来源

这份文档回答三个问题：代码到底依赖题库的什么、题库该不该进仓库、题库的来源和版权边界在哪。

---

## 一、依赖关系：只有一份数据契约，没有代码耦合

代码和题库之间**不通过 import、不通过包、不通过任何构建步骤**连接，只通过两条路径约定：

| 代码要读的东西 | 位置（相对题库根） | 必需性 | 谁在读 |
|---|---|---|---|
| 题目索引 | `index/tasks.jsonl` | **必需** | `tasks.load_index()` |
| 题目附件 | `<assets_dir>/<assets 里的每个文件>` | **必需** | `tasks.stage_assets()`、`verify.material_text()`、`probe._body()` |
| 明文答案表 | `_private/gold_private.json` | 可选 | 只有 `doctor` 的泄漏检查会看它在不在 |

题库根本身由 `config.TASKS_ROOT` 决定，取值顺序：

```
FLAGPILOT_TASKS 环境变量  >  仓库根的 tasks/ 目录
```

`index/tasks.jsonl` 每行一题，字段依赖如下（`tasks.load_index()` 的实际行为）：

| 字段 | 必需 | 用途 | 缺了会怎样 |
|---|---|---|---|
| `task_id` | 是 | 排序、`--id` 选择 | `KeyError`，加载失败 |
| `name` | 是 | 题目标识、目录名、记忆键 | `KeyError` |
| `category` | 是 | `--category` 过滤、页面分组 | `KeyError` |
| `gold_sha256` | 是 | 判分（只比哈希，不存明文） | `KeyError` |
| `assets_dir` | 是 | 拼附件路径 | `KeyError` |
| `title` | 否 | 页面展示 | 空串 |
| `query` | 否 | 给模型的题面 | 空串 |
| `assets` | 否 | 要挂进沙箱的文件清单 | 空列表（**会挂 0 个文件**） |
| `suggested_tools` | 否 | `doctor` 提示缺哪个工具 | 空列表 |
| `scope` | 否 | `core` / `extra-gs` 分流 | 默认 `core` |

**没有任何题目数、类别名、题号的硬编码。** 全项目 grep `53` / `43` / `Forensics` 在业务代码里零命中——换一套题库、只放 5 题、加 20 道自己的题，代码一行不用改。

### 这个契约的脆弱点

附件路径是"声明式"的：`index` 说这题有 `trace_task_2.pcap`，代码就去 `assets_dir/trace_task_2.pcap` 拿。**拿不到不会报错**——`stage_assets()` 只把存在的文件拷进沙箱，返回拷贝数量。

实测：index 保留 53 条、磁盘只放 2 题，跑第 3 题时沙箱 `challenge/` 是空的，模型对着空气解题，最后被记成"模型能力不足"。

所以 `doctor` 现在会做双向一致性检查（`flagpilot/cli.py`）：

```
题库一致性       通过（53 题的目录与附件齐全）
题库一致性       不通过：51 题缺目录、0 个附件缺失
  - 缺目录  forensics-003
  → 只放部分题目时必须同步裁剪 index/tasks.jsonl，否则跑到缺题会静默空跑
```

**结论：题库要么整包给，要么同步裁剪 index。不能只拷几个题目录了事。**

---

## 二、题库现在不在包里，也不该进公开仓库

`flagpilot-release/` 里**没有**题库，`.gitignore` 明确排除了 `tasks/`。原因不是懒得放，是三条独立的理由都指向同一个结论。

### 体积账

| 项 | 数值 |
|---|---|
| `no-solutions/` 总计 | 72.0 MB / 128 个文件 |
| 最大的两个文件 | `forensics-070/suspicious.dd.sda1` 31.3 MB<br>`forensics-065/dds1-alpine.flag.img.gz` 28.4 MB |
| 这两个占总量 | 59.7 MB ≈ **83%** |
| 其余 126 个文件合计 | 约 12 MB |

72 MB 没有触碰 GitHub 单文件 100 MB 的硬限制，但会让 `git clone` 变成几十秒起步，且每次改题都会在历史里再存一份二进制。代码仓库只有约 600 KB，被 120 倍的数据拖着走不划算。

### 安全账

`_private/gold_private.json` 是**明文答案**（53 条）。它和 `index/tasks.jsonl` 里的哈希必须放在一起才能自检，所以两者同属题库。一旦题库进公开仓库，答案就跟着进了公开仓库——"重放验证"这套判据的意义直接归零。

`no-solutions/` 的设计意图是"可以挂给 agent 的版本"，但它仍然**不等于可以公开**：`_private/` 只在沙箱挂载时被排除，不在分发时被排除。

### 合规账

见第四节。简单说：题目内容是第三方作品，公开再分发的授权不明确。

---

## 三、三种分发方式，推荐"并列"

| 方案 | 做法 | 优点 | 缺点 | 适用 |
|---|---|---|---|---|
| **A. 打进仓库** | 题库放 `tasks/`，一起 commit | 队友 clone 完就能跑 | 72 MB 二进制进历史；答案明文进公开仓库；合规风险 | ❌ 不推荐 |
| **B. 并列两个仓库** | 代码仓库 + 题库仓库并排；用 `FLAGPILOT_TASKS` 或 symlink 连 | 职责清晰；代码轻；题库可私有/可换 | 队友要拉两次 | ✅ **推荐** |
| **C. Release 附件** | 题库打 zip 挂 GitHub Release | 代码仓库干净 | 每次更新要重发；下载要手动解压；仍属公开分发 | 演示用可以，长期不好 |
| **D. 从上游重建** | 仓库只放 `scripts/build_taskset.py`，队友本地跑一遍 | 合规最干净；不含任何第三方内容 | 需要能访问上游；版本可能漂移 | ✅ **推荐作为兜底** |

### 推荐组合：B 为主，D 兜底

**B 的落地形态（本地开发）**

```
team-work/
├── flagpilot-release/          ← 代码仓库，可公开
│   ├── flagpilot/ app/ deploy/ tests/ scripts/
│   ├── README.md docs/
│   └── tasks -> ../ctf-tasks/no-solutions     ← 软链，仅本地
└── ctf-tasks/                  ← 题库，不进代码仓库
    └── no-solutions/
```

不建软链也行，直接用环境变量：

```bash
export FLAGPILOT_TASKS=/path/to/ctf-tasks/no-solutions
```

`scripts/start.sh` 支持 `FP_TASKS`，`deploy/env.sh` 也认这个变量。

**B 的落地形态（GitHub，如果题库也要托管）**

题库单独建一个**私有**仓库，代码仓库里用 submodule 或纯文档指向：

```bash
# 代码仓库
git clone git@github.com:<org>/flagpilot.git
git clone git@github.com:<org>/flagpilot-tasks.git ../ctf-tasks
export FLAGPILOT_TASKS=$(pwd)/../ctf-tasks/no-solutions
```

不建议用 submodule：会把"题库不可用"变成"克隆代码失败"，队友体验很差，而这两件事应该能独立失败。

**D 的落地形态（重建题库）**

仓库里已经放了完整构建脚本，这是"我们如何得到这个题库"的可复现证据：

```bash
# 1. 取上游数据（InterCode 的 CTF 子集）
git clone https://github.com/princeton-nlp/intercode.git
# src 需要包含 ic_ctf.json 与 task_assets/<task_id>/，即 intercode/data/ctf

# 2. 重建（筛类别、剥题解、gold 转哈希）
python3 scripts/build_taskset.py --src /path/to/intercode/data/ctf --out ./ctf-tasks

# 3. 校验（53 题 × 4 条断言 = 212 检查）
python3 scripts/verify_taskset.py --root ./ctf-tasks/no-solutions --selftest
```

`build_taskset.py` 做的事：只保留 Forensics / Cryptography / General Skills → 剔除依赖在线服务的题 → 删掉每题 `solution/` → 附件落到 `tasks/<category>-<id>/` → 写 `index/tasks.jsonl`（gold 换 sha256）与 `_private/gold_private.json`（明文，只给自检）。

`verify_taskset.py` 支持 `--root` 指向外部题库，所以它既能留在题库里用，也能在代码仓库里当外部题库的自检工具。

### 无论选哪个，这三件事必须做

```bash
# 题库完好？
python3 scripts/verify_taskset.py --root "$FLAGPILOT_TASKS" --selftest

# index 和磁盘对得上？有没有明文答案/题解混进来？
python3 -m flagpilot doctor

# 确认题库没被误提交
git status --short | grep -i tasks && echo "⚠ tasks 被暂存了" || echo "OK"
```

---

## 四、来源与版权

### 事实链

| 层 | 内容 | 授权 |
|---|---|---|
| 本项目的代码 | `flagpilot/`、`app/`、`deploy/`、`tests/` | 由我们决定（建议 MIT 或课程要求） |
| InterCode 的数据组织 | `data/ctf/ic_ctf.json`、`task_assets/` | **MIT**，需保留版权与许可声明 |
| 题目内容本身 | 题面文字、pcap、磁盘镜像、图片、加密文件 | **picoCTF / CMU，授权不明确** |

`index/tasks.jsonl` 的 `source` 字段逐题记录了原始出处，全部指向 `https://play.picoctf.org/practice/challenge/<id>`：

```
forensics-002 → https://play.picoctf.org/practice/challenge/362
crypto-005    → https://play.picoctf.org/practice/challenge/51
```

少数几条（如 crypto 的 ROT13 那题）上游 InterCode 的元数据给的是第三方 writeup 链接而非 picoCTF 原页，`source_note` 字段已标注这一点——这是上游的元数据瑕疵，不是我们改的。

### 风险判断

- picoCTF 的**平台代码**是 MIT（CMU 与 ForAllSecure 明确声明）。
- picoCTF 的**题目内容**不是：官方竞赛规则写明参赛材料"仅授权用于注册和参加当届比赛，不得用于任何未授权目的"。历史上公开的旧题、picoGym 练习区的题，官方长期允许练习使用，但**没有给"允许第三方再分发"的书面许可**。
- InterCode 把这些题以 MIT 仓库分发，这是上游的选择；它不构成对我们再分发的转授权。

也就是说：**教育、研究、课程作业这类使用几乎没有实际风险**（这些题已公开多年、大量 writeup 在引用），但**把题目附件放到公开 GitHub 仓库**是在做一次没有明确许可的再分发。区别不在"用"，在"分发"。

### 建议做法

| 场景 | 做法 |
|---|---|
| 课程内部使用 | 可以，正常引用出处 |
| 交作业给老师 | 走课程 LMS / 私有渠道；把题库和代码一起打包提交，不要只交公开仓库链接 |
| 代码放公开 GitHub | ✅ 可以，但**不含题库**。仓库里放 `build_taskset.py` + `source` 字段 + `NOTICE` |
| 题库放 GitHub | 只放**私有**仓库，且确认组员都能访问 |
| 报告/答辩 | 明确写"题库来自 InterCode 的 CTF 子集，原始题目出自 picoCTF"，并给出 `source` 字段 |

### 已随仓库提供的说明

- `NOTICE` — 第三方内容归属声明（InterCode MIT、picoCTF 出处）
- `scripts/build_taskset.py` — 可复现的构建过程，替代"直接分发数据"
- `index/tasks.jsonl` 的 `source` / `pico_challenge_id` — 逐题可追溯

如果课程或期刊要求更严格，最保守的做法是：仓库里**只保留 3–5 道自建题**（`runs/custom/` 那套上传机制生成的），第三方题目改为"运行 `build_taskset.py` 自行重建"。这样公开仓库里没有任何第三方内容。

---

## 五、一页速查

```
代码依赖题库的什么？
  <root>/index/tasks.jsonl        必需，10 个字段（5 必需 5 可选）
  <root>/<assets_dir>/*           必需，按 assets 清单
  <root>/_private/gold_private.json  可选，只有 doctor 看

题库在包里吗？
  不在。72.0 MB / 128 文件 / 53 题，.gitignore 已排除 tasks/

怎么分发？
  代码仓库 + 题库并列（FLAGPILOT_TASKS 连接）；题库走私有仓库或本地
  仓库内提供 build_taskset.py 作为"从上游重建"的兜底

来源？
  InterCode (MIT) 的 data/ctf → 原始题目出自 picoCTF (CMU)
  逐题 source 字段可追溯

版权风险？
  代码：自有；InterCode 数据组织：MIT 需署名；题目内容：picoCTF 授权不明确
  → 内部使用没问题，公开再分发附件有风险，所以不进公开仓库
```

---

## 六、第二种来源：NYU CTF Bench（运行时拉取，不入库）

picoCTF 那套题库没有 RE 题，而 proposal 的研究对象是 crypto + RE。NYU CTF Bench
补上了这一块，代价是数据必须**运行时拉取**（proposal 原文：*Data is fetched at run
time, not redistributed*），因此 `datasets/` 已进 `.gitignore`。

```bash
# 拉取 + 转换（首次会 clone 约 3.7 GB 到 ~/.cache/flagpilot/nyu-ctf-bench）
python3 -m flagpilot dataset --split dev
python3 -m flagpilot dataset --split test

# 用转换后的题库
FLAGPILOT_TASKS=$PWD/datasets/nyu-dev python3 -m flagpilot list
```

筛选口径对齐 proposal：只取 `crypto` 与 `rev`，且只保留离线可跑的题
（`challenge.json` 里 `compose: false`）。需要 docker 起服务的题会被排除并逐条
记进 `EXCLUDED.md`，对应 proposal 的 *report every exclusion*。

| split | 上游 crypto+RE | 离线可跑（实际转换） | 排除 |
|---|---|---|---|
| dev | 21 | **16**（Crypto 7 / RE 9） | 5 |
| test | 103 | **46**（Crypto 15 / RE 31） | 57 |

转换产物的三条硬约束：

1. **明文 flag 绝不落盘**：只写 `gold_sha256`，`challenge.json` 不复制——
   `stage_assets` 只跳过 `task.json` 和 `solution*`，挡不住它。
2. **只拷 `files` 声明的附件**：题目目录里的 `Dockerfile`、`node_modules`、
   `test_solver` 一律不带，避免把解法带进沙箱。
3. **可追溯**：`SOURCE.json` 记录上游 commit、拉取时间与授权（GPL-2.0），
   `EXCLUDED.md` 记录每一条排除理由。
