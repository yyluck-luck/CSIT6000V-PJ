# 题库

FlagPilot 不绑定具体题目集，只要能产出一个 `index/tasks.jsonl` 和对应的附件目录就能跑。
仓库里**不附带题库**（体积、明文答案、第三方授权三条理由，见 [DATASET.md](DATASET.md)），按下面任一方式提供。

> 为什么题库独立分发、能不能进公开仓库、来源和版权边界 —— 见 **[DATASET.md](DATASET.md)**。
> 本文只讲数据格式与用法。

---

## 目录结构

```
tasks/                          ← 题库根目录（默认位置，可用 FLAGPILOT_TASKS 改）
├── index/
│   └── tasks.jsonl             ← 每行一道题
├── tasks/
│   ├── crypto-005/
│   │   ├── task.json           ← 单题元数据（可选，staging 时会被跳过）
│   │   └── rot13.py            ← 附件，会复制进沙箱
│   └── forensics-002/
│       └── trace_task_2.pcap
└── _private/
    └── gold_private.json       ← 明文答案表，**绝不能上传到集群**
```

提供题库的两种方式：

```bash
# A. 放到默认位置
cp -r /path/to/no-solutions ./tasks

# B. 放在任意位置，用环境变量指过去
export FLAGPILOT_TASKS=/data/ctf-tasks/no-solutions
```

用 `python3 -m flagpilot doctor` 确认路径被正确识别，并检查 **index 与磁盘是否对得上**：

```
题目数           53  Cryptography=16, Forensics=12, General Skills=25
题库一致性       通过（53 题的目录与附件齐全）
```

`题库一致性` 不通过意味着 index 声明了某题、但 `tasks/<name>/` 不存在或附件缺失。
这种情况下 `stage_assets()` 只会静默挂 0 个文件，模型对着空目录解题，最后被误记成模型能力问题。
**只放部分题目时必须同步裁剪 `index/tasks.jsonl`。**

---

## `index/tasks.jsonl` 字段

每行一个 JSON 对象，必填项只有五个（`task_id`、`name`、`category`、`gold_sha256`、`assets_dir`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `task_id` | int | 唯一整数，用于筛选与排序 |
| `name` | str | 题目短名，如 `crypto-005`，也是运行目录名的一部分 |
| `category` | str | 分类，如 `Cryptography` / `Forensics` / `General Skills` |
| `title` | str | 题面（给模型看的就是这一段） |
| `query` | str | 原始题面，`title` 为空时的兜底 |
| `source` | str | 来源链接，仅用于追溯 |
| `gold_sha256` | str | **标准答案的 sha256 十六进制**；留空表示没有标准答案 |
| `assets` | list | 附件文件名列表（相对 `assets_dir`）；没有附件就给 `[]` |
| `assets_dir` | str | 附件目录，相对题库根，如 `tasks/crypto-005` |
| `suggested_tools` | list | 建议工具，仅作提示，不影响执行 |
| `requires_network` | bool | 是否需要外网（沙箱默认无网，需要时人工处理） |
| `scope` | str | `core` / `extra`，批跑用 `--scope core` 过滤 |

示例（单行）：

```json
{"task_id": 5, "name": "crypto-005", "category": "Cryptography",
 "title": "The flag is encoded with ROT13. Decode it.",
 "gold_sha256": "<64 位十六进制>",
 "assets": ["rot13.py"], "assets_dir": "tasks/crypto-005",
 "suggested_tools": ["python"], "requires_network": false, "scope": "core"}
```

---

## `gold_sha256` 怎么来

```bash
printf '%s' 'picoCTF{your_flag_here}' | shasum -a 256
```

注意是 `printf '%s'`（不加换行），与判据里的计算方式保持一致。
`flag` 的明文只在 `_private/gold_private.json` 里，**不要写进 `tasks.jsonl`**——
索引是会跟着代码一起上传的，明文跟着走等于免费送答案。

---

## 加一道自己的题

### 方式 A：网页上传（最快）

界面上点「＋ 上传题目」，填写题名与题面、选中附件（可多选或直接拖一个 zip）。
没有 gold，所以这类题最高只能判 `reproduced`（重放成功即算复现，不比对标准答案）。

上传的题落到 `runs/custom/<slug>/`，不会污染 `tasks/`。

### 方式 B：手工加入题库

```bash
mkdir -p tasks/tasks/my-challenge
cp ~/Downloads/secret.zip tasks/tasks/my-challenge/
printf '%s' 'flag{...}' | shasum -a 256     # 拿到哈希
```

然后在 `tasks/index/tasks.jsonl` 追加一行，`assets_dir` 写 `tasks/my-challenge`。

---

## 沙箱怎么看待附件

`stage_assets()` 会把 `assets_dir` 下的内容复制到沙箱的 `work/challenge/`，并且：

- 跳过任何名为 `solution` 的目录（第二道保险，题解在构建阶段就该剥离）
- 跳过 `task.json`（元数据不进沙箱）
- 复制后设成只读（文件 444、目录 555），工作区其他位置可写

模型看到的是相对路径 `./challenge/...`，它不知道题库在磁盘上的真实位置。

---

## 一条硬规则

**`_private/` 和任何 `with-solutions/` 目录都不能出现在运行评测的机器上。**

不是"防住 agent 去读"，而是"文件根本不存在"：
进程级沙箱不隔离路径，`find / -name 'gold_private.json'` 是能命中的。
`deploy/upload.sh` 会在打包时强制检查并在命中时中止，`doctor` 也会在远端复查。
