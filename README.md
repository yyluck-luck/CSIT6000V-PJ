# FlagPilot: a replay-verified CTF agent

FlagPilot is an end-to-end agent system that solves offline CTF challenges and **proves** that it
solved them. A correct flag is not enough. The agent must also submit a standalone `solve.py`, and
the verifier reruns that script in a fresh directory containing only the original challenge files.
Only a flag the script can reproduce counts as `verified`.

The system runs against a served model (vLLM on TACC, or any OpenAI-compatible endpoint), exposes a
web interface for task submission, live progress, and result review, and records every attempt as a
replayable trace.

> **Assessment in one command:** `cd flagpilot && bash scripts/verify.sh` — 13 checks, no GPU, no network,
> no model weights. The last check drives the web UI in headless Chrome (needs Node ≥ 22 and Chrome; skipped otherwise).

Artifact for **CSIT6000V Advanced AI Computing and Systems**, HKUST CSE. The course specification is
in [`course_project.pdf`](course_project.pdf), and the proposal this artifact implements is in
[`proposal.pdf`](proposal.pdf). Group contributions are listed in the submitted PDF, not here.

---

## Contents of this artifact

| Path | Contents |
|---|---|
| `proposal.pdf` | The project proposal this artifact implements: goals, experiments E1–E4, and the cut order |
| `course_project.pdf` | The course specification |
| `flagpilot/flagpilot/` | Core package: agent loop, tools, sandbox, replay verifier, metrics, storage |
| `flagpilot/app/` | Web interface (zero-dependency HTTP server + single-page frontend) |
| `flagpilot/demo-tasks/` | **Three synthetic tasks bundled so the quickstart runs offline** |
| `flagpilot/tests/` | 307 unit and integration tests |
| `flagpilot/scripts/` | Start/stop, self-check, split generation, task-set builder, cluster tunnel |
| `flagpilot/deploy/` | Slurm templates and an upload helper for a general cluster |
| `flagpilot/docs/` | Design, dataset contract, TACC deployment, implementation status, screenshots |

**Bundled demo tasks.** `demo-tasks/` contains three synthetic challenges written for this artifact.
They are deliberately trivial: they exercise the *pipeline* (sandbox → agent loop → replay →
verdict → writeup), not the model's reasoning. Their flags are synthetic and do not come from
picoCTF. The real evaluation uses an external picoCTF-derived task set that is not redistributed
here for size and licensing reasons; see [Task set](#1-task-set).

### How an attempt is judged

```
task ──► sandbox prep ──► agent loop ──► submit(flag, solve.py) ──► four gates ──► verdict
              │              │                    │                     │
      assets copied    model picks an      both required      1. sha256(flag) == gold
      read-only        action each turn                        2. no flag literal in solve.py
                                                              3. solve.py reruns in a clean
                                                                 directory and prints the flag
                                                              4. it still prints it with the
                                                                 challenge files removed
```

| Verdict | Meaning |
|---|---|
| `verified` | Flag matches gold **and** the script reproduces it from scratch |
| `reproduced` | The script reproduces the flag, but the task has no gold hash to compare against |
| `unverified` | Flag matches gold, but the script is missing, fails to run, or only works with the files present |
| `wrong` | Flag does not match gold |
| `budget` | Turn / token / wall-clock / format budget exhausted |

Gate 4 is the **material-free replay**: the verifier empties `challenge/` and runs the same script
again. If it still prints the submitted flag, the flag was hardcoded and the verdict becomes
`unverified / hardcoded_no_material`. This catches encoded fallbacks that the literal scan in gate 2
cannot see. Tasks with no attached files skip gate 4 automatically.

**Pre-submit self-check.** Before submitting, the agent can spend one turn on a `run_script` action
that runs its candidate script in a clean directory under exactly the replay conditions. A rejected
submission costs a submission attempt and may arrive on the last turn; the self-check costs a turn.
Both the self-check and the material-free gate can be disabled (`--no-self-check`,
`--no-material-check`) when a comparison needs the old behaviour.

**Submission budget.** An attempt may submit twice, on two independent budgets: one correction if the
flag is wrong, and one repair if the flag is right but the script fails to replay. The two do not
consume each other. A repair is granted even when the model submits on its last turn; the borrowed
turns are recorded separately as `bonus_turns`. The summary records both `first_submit_verdict` and
the final verdict, so "solved on the first try" can be separated from "solved after a repair" —
otherwise the repair budget would be counted as model capability.

---

## Getting Started Instructions

### Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11 or newer | **No third-party packages are required** for the core runtime |
| A model endpoint | Optional for the quickstart: `--model mock` runs the full pipeline with no model |
| Optional: `flagpilot/requirements.txt` | Only unlocks the `smt` action, encrypted storage, and two crypto libraries |

```bash
python3 -m pip install -r flagpilot/requirements.txt   # optional
```

Verified on Python 3.11–3.13 on macOS 14 (arm64) and on Linux (TACC compute node, Python 3.11).
Every optional dependency degrades to an explicit "not available" message rather than a crash;
`doctor` reports which ones are active.

### Setup

```bash
git clone <repository-url> flagpilot-artifact
cd flagpilot-artifact/flagpilot
export FLAGPILOT_TASKS="$PWD/demo-tasks"
export FLAGPILOT_RUNS="$PWD/runs"        # optional; defaults to ./runs
```

`FLAGPILOT_TASKS` points at a task-set root. The bundled `demo-tasks/` is a complete, valid task
set, so the quickstart needs no download.

### Step 1 — check the environment

```bash
python3 -m flagpilot doctor
```

Expected output (the CLI is localized in Chinese; field meanings are annotated):

```text
FlagPilot 0.1.0
python           3.13.12  (.../bin/python3)
题库             .../flagpilot/demo-tasks      # task-set root
运行产物         .../flagpilot/runs            # output root

题目数           3  Demo=3                     # 3 tasks loaded
题库一致性       通过（3 题的目录与附件齐全）  # index and files agree
泄漏检查         通过（无明文 gold、无题解目录） # no plaintext gold leaked

网络隔离         proxy-only  ← 不可作为强隔离证据
资源上限         可用
隔离等级         process  ← 非容器，报告里必须如实说明
容器             none
SMT (z3)         可用
反汇编           /usr/bin/objdump
调试器           未找到  ← 只能靠 shell 调其它调试器
```

The `隔离等级` (isolation level) and `容器` (container) lines state what isolation was actually
achieved. They are meant to be quoted in a report, not ignored: on a host without a container
runtime this says `process`, and the artifact claims nothing stronger.

If the task count line reports a missing `tasks/index/tasks.jsonl`, then `FLAGPILOT_TASKS` is not
pointing at a task-set root.

### Step 2 — run the bundled demo end to end

```bash
python3 -m flagpilot batch --batch demo --model mock --limit 3 --workers 1
```

Three attempts, each with a fresh sandbox, a scripted policy, a replay, and a generated writeup.
Expected output:

```text
[batch] demo: 3 题 × 1 seeds, model=mock, ablation=custom（截断 + 结构化工具 + 完整 prompt）
[runner] 待跑 3 个 attempt，并发 1
[1/3] demo-001-s0            VERIFIED   turns=3 0.18s
[2/3] demo-002-s0            VERIFIED   turns=3 0.2s
[3/3] demo-003-s0            VERIFIED   turns=3 0.19s
[runner] 完成 3 个，用时 0.7s

attempts        3   ablation=custom
verified        3  (100.0%, 95% CI 43.9%–100.0%)
raw (flag ok)   3  (100.0%, 95% CI 43.9%–100.0%)
gap             0.0%
wrong / budget / error  0 / 0 / 0
mean turns      3.0
mean wall       0.19 s
mean tokens     459
TTFT p50        1 ms（各题中位数之均值）
wall p50/p95    0.2 / 0.2 s（n=3）
solve p50/p95   0.2 / 0.2 s（verified 端到端，n=3）
reasons         match=3
by category:
  Demo               n=3   verified=3   raw=3   v_rate=100% r_rate=100%
```

Per-attempt artifacts land in `runs/demo/<task>-s0/`: `trace.jsonl` (every event),
`replay/solve.py` and `replay/challenge/` (the clean room used for the rerun), `no-material/`
(the emptied copy used by gate 4), and `writeup.md`.

### Step 3 — verify that the verdict is not a rubber stamp

A verifier that always says `verified` proves nothing. Corrupt every gold hash and confirm that the
same run is now rejected:

```bash
cp -r demo-tasks /tmp/neg-tasks
sed -i.bak 's/"gold_sha256": "[a-f0-9]*"/"gold_sha256": "0000000000000000000000000000000000000000000000000000000000000000"/' \
  /tmp/neg-tasks/index/tasks.jsonl
FLAGPILOT_TASKS=/tmp/neg-tasks python3 -m flagpilot run --task demo-001 --model mock --batch neg
```

Expected output (excerpt of the summary JSON):

```json
"verdict": "wrong",
"reason": "wrong_flag",
"first_submit_verdict": "wrong",
"first_submit_reason": "wrong_flag"
```

The model produces the same flag and the same script; only the gold hash changed, and the verdict
flips. That is the behaviour the evaluation depends on.

### Step 4 — open the web interface

```bash
python3 -m flagpilot web --model mock --host 127.0.0.1 --port 8080
```

Open <http://127.0.0.1:8080>, pick a task on the left, and press run. The middle column streams the
agent's turns (thought → action → real tool output → observation); the right column shows the
verdict, the replay result, and the budget counters. A `writeup.md` download appears when the run
finishes.

Screenshots: `flagpilot/docs/screenshots/01-solve.png`, `02-chat.png`; architecture:
`flagpilot/docs/screenshots/architecture.svg`.

### Step 5 — replay a finished run without a model

This is the path to use when reviewing results, and the one that needs no cluster and no GPU.
Point the server at any directory of completed runs:

```bash
python3 -m flagpilot web --replay ./runs/demo --port 8080
```

```text
FlagPilot demo → http://127.0.0.1:8080   model=mock
回放模式：挂载 3 个已完成的 run（只读，不需要模型）
  demo-001-s0   http://127.0.0.1:8080/?run=c6a6f48eaf3e   verified/match
  demo-002-s0   http://127.0.0.1:8080/?run=597af7758ad6   verified/match
  demo-003-s0   http://127.0.0.1:8080/?run=b4b8b8900546   verified/match
```

The interface is identical to a live run, because a replay entry and a live run are the same data
structure on the server. Only runs that finished (they have a summary) are mounted.

The same trace can be read as a timeline without a browser:

```bash
python3 -m flagpilot show --run-dir ./runs/demo/demo-001-s0
python3 -m flagpilot show --run-dir ./runs/demo/demo-001-s0 --full   # include observations and scripts
python3 -m flagpilot show --run-dir ./runs/demo/demo-001-s0 --out run.md
```

### Expected result of the quickstart

| Step | Command | Expected |
|---|---|---|
| 1 | `doctor` | 3 tasks loaded, index and leak checks pass |
| 2 | `batch --model mock --limit 3` | 3/3 `verified`, about 0.7 s wall clock |
| 3 | `run` with a corrupted gold | `wrong` / `wrong_flag` |
| 4 | `web --model mock` | UI loads on port 8080; a run reaches `verified` |
| 5 | `web --replay ./runs/demo` | 3 runs mounted, each opening at `verified/match` |

Total elapsed time on a laptop: under one minute. No GPU, no API key, no network access.

---

## Detailed Instructions

> All commands in this section run from the `flagpilot/` directory created in
> [Setup](#setup), unless a step says otherwise. `python3 -m flagpilot` resolves the package
> from there.

### 1. Task set

A task-set root contains `index/tasks.jsonl` plus one directory per task:

```text
<task-set>/
├── index/tasks.jsonl          one JSON object per line
└── <task-name>/
    └── <asset files>          copied into the sandbox as read-only ./challenge/
```

`tasks.jsonl` fields: `task_id`, `name`, `category`, `title`, `query`, `gold_sha256`, `assets`,
`assets_dir`, `suggested_tools`, `scope`. Only `gold_sha256` is stored; plaintext flags never enter
the repository or the sandbox. The contract is specified in `flagpilot/docs/TASKS.md`.

The bundled `demo-tasks/` is a minimal but complete example and is what the quickstart uses.

**The evaluation task set is not redistributed.** It is derived from picoCTF (General Skills,
Cryptography, and Forensics; network-dependent tasks removed), which we cannot ship. To rebuild it
from the upstream InterCode-CTF release:

```bash
python3 scripts/build_taskset.py --src /path/to/intercode/data/ctf --out ./ctf-tasks
export FLAGPILOT_TASKS="$PWD/ctf-tasks"
python3 -m flagpilot list                     # 43 core tasks
python3 -m flagpilot list --scope extra-gs    # 10 supplementary General Skills tasks
```

The builder drops reference solutions, removes network-dependent tasks, and replaces plaintext flags
with `gold_sha256`. Our evaluated set contains **53 tasks** (General Skills 25 / Cryptography 16 /
Forensics 12), of which 43 are `scope=core` and 10 are `scope=extra-gs`; `list` shows the core set by
default. There are no reverse-engineering tasks, which matters for one known bias discussed in
[Limitations](#7-known-limitations-and-expected-variation).

**NYU CTF Bench (cryptography and reverse engineering).** A second loader pulls the public benchmark
at run time, keeps only offline-solvable tasks, and writes the exclusion list next to the result:

```bash
python3 -m flagpilot dataset --name nyu --split dev     # 16 tasks
python3 -m flagpilot dataset --name nyu --split test    # 46 tasks
export FLAGPILOT_TASKS="$PWD/datasets/nyu-dev"
```

Upstream is cloned into `~/.cache/flagpilot/nyu-ctf-bench` and is never committed; `datasets/` is
git-ignored. `--include-compose` also takes the tasks that need `docker compose`, which the offline
replay cannot run. See `flagpilot/docs/DATASET.md`.

You can also add a task through the web UI (upload a zip, optionally with a gold flag).

### 2. Model backends

| Backend | Flag | Needs | Use |
|---|---|---|---|
| Scripted policy | `--model mock` | Nothing | Pipeline verification, offline demo, CI |
| OpenAI-compatible endpoint | `--model <name> --base-url <url>` | A served model | Real evaluation |
| DeepSeek hosted API | `--track H` | `DSV41_API_KEY` in `.env` | Evaluation without a GPU |

The scripted `mock` policy is **not a model**: it lists the challenge directory, prints every file,
and submits the first flag-shaped string it sees, with a `grep`-style replay script. It exists so the
harness can be tested and demonstrated without a GPU. It cannot solve anything that requires
reasoning, and its accuracy must never be reported as a model result.

For a local model:

```bash
ollama serve &
ollama pull qwen3:8b
python3 -m flagpilot batch --batch local --model qwen3:8b \
  --base-url http://127.0.0.1:11434/v1 --limit 20 --workers 1
```

For the hosted track, credentials come from an ignored `.env`:

```bash
printf 'DSV41_API_KEY=%s\n' "$YOUR_KEY" > .env
python3 -m flagpilot batch --batch h-smoke --track H --tasks crypto-005 --workers 1 --turns 6
```

`--track H` fills in the endpoint and the price table automatically, and prices every request from
cache-hit, cache-miss, and output tokens separately.

### 3. Reproducing the evaluation

Each experiment writes a Markdown summary and a `runs.jsonl` ledger under `runs/<batch>/`.
Measured rates and their current status are tabulated in `flagpilot/docs/STATUS.md`
(§二 commitments, §四 recorded verification); design rationale is in `flagpilot/docs/DESIGN.md`.
The commands below regenerate every number those tables cite.

**E1 — ablation ladder (B0 → B3).** Each rung adds exactly one capability: `B0` bare loop,
`B1` + context truncation, `B2` + structured tool schema, `B3` + full system prompt.

```bash
for b in B0 B1 B2 B3; do
  python3 -m flagpilot batch --batch e1-$b --ablate $b --model qwen3:8b \
    --base-url http://127.0.0.1:11434/v1 --scope core --limit 20 --seeds 1 --workers 1
done
python3 -m flagpilot compare --batches e1-B0,e1-B1,e1-B2,e1-B3 --md e1.md
python3 -m flagpilot pair --a e1-B2 --b e1-B3 --md e1-pair.md
```

`--workers 1` is required for E1 and E3: concurrency dilutes the server-side prefix-cache difference
those experiments measure. `compare` reports verified rate, raw flag accuracy, and their gap with
Wilson intervals; `pair` runs an exact McNemar test on the same tasks. Corresponding table:
`docs/STATUS.md` §二 "实验", row E1.

**E2 — verification and attribution.** The memory probe asks for the flag with no tools, no files,
and no sandbox, at three disclosure levels (`name`, `brief`, `files`).

```bash
python3 -m flagpilot probe --batch e2-probe --mode brief --limit 20
python3 -m flagpilot report --batch e2-probe
```

A hit means the model can name the flag without solving the task, which bounds how much of the
headline accuracy could be memorization. `report` prints raw accuracy, verified rate, the gap, and
the failure-attribution distribution. The attribution separates **output truncation**
(`finish_reason=length`, or reported tokens hitting `max_tokens`) from genuine protocol violations,
and can re-read old traces so past runs are re-labelled without re-execution. Corresponding table:
`docs/STATUS.md` §二 "实验", row E2.

**E3 — efficiency grid.** Observation policy × turn budget × concurrency, with prefix caching on and
off (one restart per cache setting). Requires a vLLM endpoint that exposes `/metrics`.

```bash
sbatch --export=ALL,FP_E3=1,FP_PREFIX_CACHE=1,FP_MAX_SEQS=4,FP_LIMIT=20,FP_SPLIT=dev deploy/run.slurm
python3 -m flagpilot compare --e3 --ref e3-pc1-trunc-t20-w1 --batches e3-pc1-trunc-t20-w1,e3-pc1-summ-t20-w1
```

Two planned E3 dimensions cannot be varied on the course platform: `max_num_seqs` and
`enable_prefix_caching` are fixed in the provided serving script, so the grid collapses from six
cells to four (2 observation policies × 2 turn budgets). `--e3` adds solve p50/p95, cache hit rate,
GPU-seconds per verified solve, and dollars per verified solve; `--ref` adds the verified-rate delta
and a guardrail verdict against `--guard-pts`. Corresponding table: `docs/STATUS.md` §二 "实验",
row E3.

**E4 — escalation strategy.** Track L alone, Track H alone, and a hybrid that escalates on stalls.

```bash
python3 -m flagpilot batch --batch e4-l --track L --base-url http://127.0.0.1:8000/v1 --limit 20
python3 -m flagpilot batch --batch e4-h --track H --limit 20
python3 -m flagpilot batch --batch e4-hy --track L --base-url http://127.0.0.1:8000/v1 \
  --hybrid --stall-turns 3 --max-escalations 4 --max-hosted-tokens 60000 --limit 20
python3 -m flagpilot compare --e3 --batches e4-l,e4-h,e4-hy --md e4.md
```

A stall is k consecutive turns that add no new evidence (tool-output fingerprints are deduplicated).
The escalation budget is capped by `--max-escalations` and `--max-hosted-tokens`, and the two tracks
are accounted separately in `summary["hybrid"]` and `cost_usd_total`.

**Cost accounting.** `--track L` records GPU seconds in two ways: `allocated` (cards × wall clock)
and `busy` (Σ utilization × Δt, sampled from `nvidia-smi`). When the sampler is unavailable the
field is `None` and printed as `—`, never as zero. `--track H` prices each request from the DeepSeek
price table, separating cache-hit, cache-miss, and output tokens, with peak-hour multipliers applied
at request time.

**Repetitions.** `--seeds N` creates N attempts per task and records their identifiers. The model
request does not carry a sampling seed, so these are repeated attempts, not seeded determinism; the
distinction is stated wherever repetition counts are reported.

### 4. Deployment on TACC

Four paths. **Path A needs no cluster at all** and is what grading should use; the others exist
because the evaluation itself needs a GPU.

| Path | Needs | Use |
|---|---|---|
| A. Offline replay | A `runs/` directory | Reviewing results, demos, screenshots |
| B. Backend on the cluster, frontend on your laptop | TACC account + SSH forwarding | Interactive debugging of the UI |
| C. Batch job on the cluster, pull results back | TACC account + the course SDK | Producing real evaluation data |
| D. Live agent from the web page | TACC account + the course SDK | Demos with the real model, no forwarding |

#### 4A. Nothing on the cluster

Covered by Step 5 above. A finished `runs/` tree is self-contained: `trace.jsonl` holds every event,
so `web --replay` and `show` reproduce the run without a model, a GPU, or network access. This is
how a reader can inspect a real 27B run without owning a cluster account.

#### 4B. Backend on the cluster, frontend on your laptop

The agent loop and the web server are one process, so "backend on TACC, frontend local" means:
serve on a compute node, forward the port, and point a local browser at it.

```bash
# 1) start the course serving job through the SDK and read its endpoint from the job log
deploy/tcloud/submit.sh serve                    # prints the job id
tcloud cat slurm_log/qwen-public-serve-<job-id>.log | grep SERVICE_BASE_URL   # → http://<node>:8000/v1

# 2) on your laptop: forward the port (no shell is opened)
export TACC_HOST=gw.example.edu TACC_PORT=30041 TACC_KEY=~/.ssh/id_ed25519
export TACC_NODE=10.0.1.11                        # the node IP from step 1
scripts/tunnel.sh up                              # starts ssh -f -N -L and probes /v1/models
scripts/tunnel.sh status

# 3) on your laptop: the UI now talks to the cluster model
export FLAGPILOT_TASKS="$PWD/demo-tasks"
python3 -m flagpilot web --host 127.0.0.1 --port 8080 \
    --model qwen3.8-27b --base-url http://127.0.0.1:8000/v1

scripts/tunnel.sh down
```

`scripts/tunnel.sh` has four subcommands: `up` (start and verify), `down`, `status`, and `endpoint`
(find the serving job and print its node). It reads `TACC_HOST`, `TACC_PORT`, `TACC_KEY`,
`TACC_USER`, `TACC_NODE`, and `LOCAL_PORT`.

> The course platform's own instructions ask students to use its SDK rather than logging in to the
> platform servers or opening SSH forwards themselves. Nothing here changes what the artifact does,
> but for grading prefer **4A** or **4C**, which need no forwarding. Use 4B for local development.

#### 4C. Batch job on the cluster, pull results back

This is the path that produces the evaluation data, and it never leaves the cluster.

```bash
# everything runs from your laptop through the course SDK: no login, no scp, no port forwarding
deploy/tcloud/submit.sh doctor                         # CPU self-check: SDK bootstrap, course Python, leak guard
deploy/tcloud/submit.sh serve                          # qwen3.8-27b on 2x3090 (partition limit: 1 hour)
tcloud cat slurm_log/qwen-public-serve-<job>.log       # wait for "Application startup complete"
FP_LIMIT=5 deploy/tcloud/submit.sh batch http://<node>:8000/v1
tcloud download flagpilot-runs/tacc-smoke.md ./        # report
tcloud download -r flagpilot-runs/tacc-smoke ./        # traces, then: python3 -m flagpilot web --replay ./tacc-smoke
tcloud cancel -j <serve job>                            # free the GPUs
# or all of it in one command: deploy/tcloud/pipeline.sh <local-output-dir>
```

The client job reads the model endpoint from `server-url.txt`. Persisting `runs/` is not optional:
the job's temporary directory is deleted when the job ends, and an earlier run lost its traces that
way.

On a general cluster (not the course platform) the same thing runs through `deploy/serve.slurm` and
`deploy/run.slurm`:

```bash
sbatch --export=ALL,FP_LIMIT=20,FP_SPLIT=dev deploy/serve.slurm
sbatch --export=ALL,FP_LIMIT=20,FP_SPLIT=dev deploy/run.slurm
```

#### 4D. Live agent from the web page (SDK only)

The page can drive Qwen on the course platform directly. The local server submits SDK jobs and reads
their Slurm logs, so nothing is forwarded.

```bash
cd flagpilot && python3 -m flagpilot web --replay runs/tacc   # then click "Qwen on TACC · connect" (Solve) or "connect Qwen" (Ask)
```

- Connecting submits one serving job and keeps it for the partition's hour, counted from when the job
  starts running. It is cancelled three minutes before the limit, or when you click the chip again.
- **Run on TACC** on Solve submits a small client job with just that challenge. The job prints each
  trace event into its log; the page reads it with `tcloud cat` every 4 s, then downloads the finished
  run and mounts it as a replay.
- Each Ask message is one small job (`fp chat-once`) that sends its reply back the same way. Threads
  stay on the laptop in `runs/chat/`.
- Client jobs share one working directory on the platform, so one solve or reply runs at a time.
- Measured on 2026-11-20 (serving job 30111): ready in 5 minutes, one Ask reply in 31 s, and
  general-004 (B3) from click to `verified` in 64 s.

**Known platform constraints.** The course partition caps a job at 1 hour, and serving a 27B model
takes 5–17 minutes to become ready (5 on 2026-11-20, 17 in an earlier run), leaving 43–55 minutes of
work per job; long evaluations
must be sharded with `--shard i/n`. `max_num_seqs` and `enable_prefix_caching` are fixed in the
provided serving script, which is why E3's grid is smaller than planned. Full notes:
`flagpilot/docs/DEPLOY-TACC.md` and `flagpilot/docs/TACC.md`.

### 5. Metrics

| Metric | Definition |
|---|---|
| raw accuracy | `verdict != wrong` — the flag matched gold, with or without a working script |
| verified rate | `verdict == verified` — flag matched **and** the replay reproduced it |
| gap | raw accuracy − verified rate: attempts that were answered but not reproducible |
| Wilson interval | 95% confidence interval for a proportion, used instead of the normal approximation |
| p50 / p95 | End-to-end latency and verified solve time |
| McNemar | Exact paired test on per-task outcomes between two batches |

Because `wrong` is returned only when the flag itself is wrong, raw accuracy can be recomputed from
any `runs.jsonl` ledger, including batches recorded before a code change.

### 6. Tests

```bash
python3 -m unittest discover -s tests -t . -q     # 348 tests
bash scripts/verify.sh                            # 13 feature checks (the last one drives the web UI)
```

The suite is hermetic: it builds synthetic tasks in temporary directories and needs neither a model
nor the evaluation task set. Storage tests skip unless `cryptography` is installed; with it
installed, all 348 pass. `scripts/verify.sh` is the end-to-end gate: it checks the CLI surface, the
verdict logic, the replay paths, the web API, the offline replay, and the packaging rules, and
prints one line per check.

### 7. Known limitations and expected variation

1. **The sandbox is not a security boundary.** It runs as the same uid, without `chroot`, `seccomp`,
   or cgroups. `challenge/` is read-only by permission bits, which the agent can undo. The real
   defences are that the flag never enters the sandbox and that the replay uses a fresh copy of the
   task files, not the process isolation. `doctor` prints the achieved level (`process` or
   `container`) so a report never overstates it.
2. **Container isolation is implemented but unexercised.** The backend probes
   apptainer/singularity/docker/podman and uses one when a local image exists, falling back to
   process isolation otherwise. No machine we have access to provides a runtime, so
   `caps.isolation == "container"` has not been observed in a real run.
3. **Network isolation is `proxy-only`.** It points `http_proxy` at a dead port; a raw socket can
   still reach the network. Where user namespaces are unavailable this degrades silently, and
   `doctor` reports it as unsuitable evidence of isolation.
4. **Literal scanning is heuristic.** `scan_script` catches exact, hex, base64, reversed, and ROT13
   forms of the flag. It cannot catch deliberate obfuscation such as splitting a flag into an integer
   array — that is what the material-free replay is for. ROT13 is exempted from the literal scan
   because its ciphertext *is* the challenge input; without the exemption a correct solution would
   be flagged as cheating.
5. **No reverse-engineering tasks in the picoCTF-derived set.** It covers crypto, forensics, and
   general skills. A pure-reasoning RE task has no natural `solve.py`, so it would be scored
   `unverified` by construction. The reported verified rate is therefore not directly comparable to
   benchmarks that include RE. The NYU loader is the way to add RE coverage.
6. **Small samples, wide intervals.** With 20 tasks, a 100% verified rate has a 95% Wilson interval
   of roughly 84–100%. Differences below about 15 percentage points on 20 tasks are not resolvable,
   which is why the paired test is reported alongside.
7. **Mock is not a model.** Quickstart numbers (`3/3 verified`) measure the harness. They must not be
   compared with model results anywhere.
8. **Model scale.** The course platform serves a 27B AWQ model; the reproducible configuration in §3
   uses an 8B model on a single consumer GPU. Absolute rates differ between the two. What we claim
   transfers is the direction of the ablation effect, not its magnitude.
9. **Job length.** The course partition caps a job at 1 hour, and serving a 27B model takes about 17
   minutes to become ready, leaving roughly 43 minutes per job. Long evaluations must be sharded
   with `--shard`.
10. **Storage is plaintext by default.** Encryption is opt-in and needs a separate key file; see
    `flagpilot/docs/WRITEUPS_AND_STORAGE.md`. Live results and downloads are always decrypted.
11. **System tools are not bundled.** `binwalk` (6 tasks), `exiftool` (5), `zsteg` (5), `tshark` (2),
    `sleuthkit` (1), and `gdb` are OS packages, not pip wheels. Missing ones make the affected tasks
    fail for environmental reasons, not model reasons; `doctor` lists them with their task counts.
    `flagpilot/requirements.txt` covers everything that *can* ship with the repository.
12. **No sampling seed is sent.** `--seeds` repeats attempts; it does not fix the provider's RNG.

### 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `找不到题库索引` | `FLAGPILOT_TASKS` unset or wrong | Point it at a directory containing `index/tasks.jsonl` |
| Task-set consistency check fails | Index lists assets that are missing | Rebuild the index, or restore the files |
| Replay reports `/bin/sh: python3: not found` | Host started as an absolute path, so the sandbox `PATH` lacked the interpreter directory | Fixed in `flagpilot/sandbox.py`; the sandbox now prepends the resolved interpreter directory |
| `--split test` refuses to run | The test split runs once per configuration | Use `--split dev`, or pass `--confirm-test` deliberately; the guard is intentional |
| Web run returns 403 | Missing same-origin `Origin` header | Use the UI, or send `Origin: http://127.0.0.1:8080` |
| Replay page returns 404 | The directory has no `trace.jsonl`, or the run never finished (no summary) | `--replay` mounts only runs that finished |
| `?run=` opens nothing | The id must be 12 hex characters | Use the URL printed at startup; do not construct one |
| Tunnel is up but `/v1/models` is empty | The serving job expired (1-hour partition limit) | Resubmit the serving job; the tunnel recovers if the node IP is unchanged |
| Encrypted run fails on `staging.rmdir()` | Host shim intercepts `rmdir` | Set `FLAGPILOT_RUNS` to a path outside the intercepted tree |
| Model output looks cut off | `--max-tokens` too small | Attribution labels these as truncation rather than protocol failures; raise `--max-tokens` and rerun |

---

## Data and licensing

The bundled `demo-tasks/` is original to this artifact. The picoCTF-derived evaluation task set is
not redistributed; `scripts/build_taskset.py` rebuilds it from the upstream InterCode-CTF release
(MIT). NYU CTF Bench (GPL-2.0) is cloned at run time by `flagpilot dataset` and never committed.
Cybench (Apache-2.0) is not integrated: the proposal lists it as a stretch goal and the cut order
places it second, and a 40-task set on which the 32B baseline solves 5% cannot resolve an effect.
See `NOTICE` for third-party attribution and `flagpilot/docs/DATASET.md` for the inclusion rules.
