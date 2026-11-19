# FlagPilot: a replay-verified CTF agent

FlagPilot is an end-to-end agent system that solves offline CTF challenges and **proves** that it
solved them. A correct flag is not enough. The agent must also submit a standalone `solve.py`, and
the verifier reruns that script in a fresh directory containing only the original challenge files.
Only a flag that the script can reproduce counts as `verified`.

The system runs against a served model (vLLM on TACC, or any OpenAI-compatible endpoint), exposes a
web interface for task submission, live progress, and result review, and records every attempt as a
replayable trace.

Artifact for **CSIT6000V Advanced AI Computing and Systems**, HKUST CSE. The course specification is
in [`course_project.pdf`](course_project.pdf).

---

## Team and contributions

| Member | GitHub | Contribution |
|---|---|---|
| _to be filled_ | _to be filled_ | _to be filled_ |
| _to be filled_ | _to be filled_ | _to be filled_ |
| _to be filled_ | _to be filled_ | _to be filled_ |

---

## What is in this artifact

| Path | Contents |
|---|---|
| `flagpilot/flagpilot/` | Core package: agent loop, tools, sandbox, replay verifier, metrics, storage |
| `flagpilot/app/` | Web interface (zero-dependency HTTP server + single-page frontend) |
| `flagpilot/demo-tasks/` | **Three synthetic tasks bundled for verification** (see note below) |
| `flagpilot/tests/` | 210 unit and integration tests |
| `flagpilot/scripts/` | Start/stop, self-check, split generation, task-set builder |
| `flagpilot/deploy/` | Slurm templates for a general cluster (not used by the course platform) |
| `flagpilot/docs/` | Design, dataset contract, TACC deployment notes, implementation status |

**Bundled demo tasks.** `demo-tasks/` contains three synthetic challenges written for this artifact.
They are deliberately trivial: they exercise the *pipeline* (sandbox → agent loop → replay →
verdict → writeup), not the model's reasoning. Their flags are synthetic and do not come from
picoCTF. The real evaluation uses an external picoCTF-derived task set that is not redistributed
here for size and licensing reasons; see [Task set](#1-task-set).

### How an attempt is judged

```
task ──► sandbox prep ──► agent loop ──► submit(flag, solve.py) ──► three gates ──► verdict
              │              │                    │                      │
      assets copied    model picks an      both required       1. sha256(flag) == gold
      read-only        action each turn                         2. no flag literal in solve.py
                                                                3. solve.py reruns in a clean
                                                                   directory and prints the flag
```

| Verdict | Meaning |
|---|---|
| `verified` | Flag matches gold **and** the script reproduces it from scratch |
| `unverified` | Flag matches gold, but the script is missing or fails to run |
| `wrong` | Flag does not match gold |
| `budget` | Turn / token / wall-clock / format budget exhausted |

Tasks without a gold hash can reach `reproduced` at best, never `verified`.

---

## Getting Started Instructions

### Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11 or newer | **No third-party packages are required** for the core runtime |
| A model endpoint | Optional for the quickstart: `--model mock` runs the full pipeline with no model |
| Optional: `cryptography` | Only for encrypted storage: `pip install -r flagpilot/requirements-encryption.txt` |

Verified on Python 3.11–3.13 on macOS 14 (arm64) and on Linux (TACC compute node, Python 3.11).

### Setup

```bash
git clone <repository-url> flagpilot-artifact
cd flagpilot-artifact/flagpilot
export FLAGPILOT_TASKS="$PWD/demo-tasks"
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
python           3.13.12
题库             .../flagpilot/demo-tasks      # task-set root
运行产物         .../flagpilot/runs            # output root

题目数           3  Demo=3                     # 3 tasks loaded
题库一致性       通过（3 题的目录与附件齐全）  # index and files agree
泄漏检查         通过（无明文 gold、无题解目录） # no plaintext gold leaked
```

If the task count line reports a missing `tasks/index/tasks.jsonl`, then `FLAGPILOT_TASKS` is not
pointing at a task-set root.

### Step 2 — run the bundled demo end to end

```bash
python3 -m flagpilot batch --batch demo --model mock --limit 3 --workers 1
```

Three attempts, each with a fresh sandbox, a scripted policy, a replay, and a generated writeup.
Expected output:

```text
[1/3] demo-001-s0            VERIFIED   turns=3 0.17s
[2/3] demo-002-s0            VERIFIED   turns=3 0.21s
[3/3] demo-003-s0            VERIFIED   turns=3 0.17s
[runner] 完成 3 个，用时 0.8s

attempts        3   ablation=custom
verified        3  (100.0%, 95% CI 43.9%–100.0%)
raw (flag ok)   3  (100.0%, 95% CI 43.9%–100.0%)
gap             0.0%
wrong / budget / error  0 / 0 / 0
reasons         match=3
```

Per-attempt artifacts land in `runs/demo/<task>-s0/`: `trace.jsonl` (every event),
`replay/solve.py` and `replay/challenge/` (the clean room used for the rerun), and `writeup.md`.

### Step 3 — verify that the verdict is not a rubber stamp

A verifier that always says `verified` proves nothing. Break the gold hash and confirm that the same
run is now rejected:

```bash
cp -r demo-tasks /tmp/neg-tasks
sed -i.bak 's/"gold_sha256": "[a-f0-9]*"/"gold_sha256": "0000000000000000000000000000000000000000000000000000000000000000"/' \
  /tmp/neg-tasks/index/tasks.jsonl
FLAGPILOT_TASKS=/tmp/neg-tasks python3 -m flagpilot run --task demo-001 --model mock --batch neg
```

Expected output:

```json
"verdict": "wrong",
"reason": "wrong_flag",
"detail": "flag 与 gold 哈希不一致"
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

Screenshots: `flagpilot/docs/screenshots/01-solve.png`, `02-chat.png`.

### Expected result of the quickstart

| Step | Command | Expected |
|---|---|---|
| 1 | `doctor` | 3 tasks loaded, index and leak checks pass |
| 2 | `batch --model mock --limit 3` | 3/3 `verified`, about 0.8 s wall clock |
| 3 | `run` with a corrupted gold | `wrong` / `wrong_flag` |
| 4 | `web --model mock` | UI loads on port 8080; a run reaches `verified` |

Total elapsed time on a laptop: under one minute. No GPU, no API key, no network access.

---

## Detailed Instructions

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

**The evaluation task set is not redistributed.** It is derived from picoCTF (Forensics,
Cryptography, and General Skills; network-dependent tasks removed), which we cannot ship. To rebuild
it from the upstream InterCode-CTF release:

```bash
python3 scripts/build_taskset.py --src /path/to/intercode/data/ctf --out ./ctf-tasks
export FLAGPILOT_TASKS="$PWD/ctf-tasks"
```

The builder drops reference solutions, removes network-dependent tasks, and replaces plaintext flags
with `gold_sha256`. Our evaluated set contains 53 tasks (General 25 / Crypto 16 / Forensics 12) and
no reverse-engineering tasks, which matters for one known bias discussed in
[Limitations](#6-known-limitations-and-expected-variation).

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

For the TACC deployment used in the report (vLLM serving Qwen3.8-27B-AWQ-INT4, tensor parallel 2,
32K context, `max-num-seqs=4`), see `flagpilot/docs/TACC.md`. The course platform accepts jobs
through its own SDK rather than `sbatch`; `deploy/*.slurm` are portable templates for a general
cluster.

### 3. Reproducing the evaluation

Each experiment writes a Markdown summary and a `runs.jsonl` ledger under `runs/<batch>/`.

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
Wilson intervals; `pair` runs an exact McNemar test on the same tasks.
Report reference: §5.2, Table 2 (ablation) and Table 3 (paired tests).

**E2 — verification and attribution.** The memory probe asks for the flag with no tools, no files,
and no sandbox, at three disclosure levels (`name`, `brief`, `files`).

```bash
python3 -m flagpilot probe --batch e2-probe --mode brief --limit 20
python3 -m flagpilot report --batch e2-probe
```

A hit means the model can name the flag without solving the task, which bounds how much of the
headline accuracy could be memorization. `report` prints raw accuracy, verified rate, the gap, and
the failure-attribution distribution (12 labels). Report reference: §5.3, Table 4 and Figure 3.

**E3 — efficiency grid.** Observation policy × turn budget × concurrency, with prefix caching on and
off (one restart per cache setting). Requires a vLLM endpoint that exposes `/metrics`.

```bash
sbatch --export=ALL,FP_E3=1,FP_PREFIX_CACHE=1,FP_MAX_SEQS=4,FP_LIMIT=20,FP_SPLIT=dev deploy/run.slurm
python3 -m flagpilot compare --e3 --ref e3-pc1-trunc-t20-w1 --batches e3-pc1-trunc-t20-w1,e3-pc1-summ-t20-w1
```

Two planned E3 dimensions cannot be varied on the course platform: `max_num_seqs` and
`enable_prefix_caching` are fixed in the provided serving script, so the grid collapses from six
cells to four (2 observation policies × 2 turn budgets). Report reference: §5.4, Table 5.

**Cost accounting.** `--track L` records GPU seconds in two ways: `allocated` (cards × wall clock)
and `busy` (Σ utilization × Δt, sampled from `nvidia-smi`). When the sampler is unavailable the
field is `None` and printed as `—`, never as zero. `--track H` prices each request from the DeepSeek
price table, separating cache-hit, cache-miss, and output tokens, with peak-hour multipliers applied
at request time.

### 4. Metrics

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

### 5. Tests

```bash
python3 -m unittest discover -s tests -t . -q     # 210 tests
bash scripts/verify.sh                            # 11 feature checks
```

The suite is hermetic: it builds synthetic tasks in temporary directories and needs neither a model
nor the evaluation task set. 17 storage tests skip unless `cryptography` is installed; with it
installed, all 210 pass.

### 6. Known limitations and expected variation

1. **The sandbox is not a security boundary.** It runs as the same uid, without `chroot`, `seccomp`,
   or cgroups. `challenge/` is read-only by permission bits, which the agent can undo. The real
   defences are that the flag never enters the sandbox and that the replay uses a fresh copy of the
   task files, not the process isolation.
2. **Network isolation is `proxy-only`.** It points `http_proxy` at a dead port; a raw socket can
   still reach the network. Where user namespaces are unavailable this degrades silently, and
   `doctor` reports it as unsuitable evidence of isolation.
3. **Literal scanning is heuristic.** `scan_script` catches exact, hex, base64, reversed, and ROT13
   forms of the flag. It cannot catch deliberate obfuscation such as splitting a flag into an integer
   array. ROT13 is exempted because its ciphertext *is* the challenge input; without the exemption a
   correct solution would be flagged as cheating.
4. **No reverse-engineering tasks.** The task set covers crypto, forensics, and general skills. A
   pure-reasoning RE task has no natural `solve.py`, so it would be scored `unverified` by
   construction. The reported verified rate is therefore not directly comparable to benchmarks that
   include RE.
5. **Small samples, wide intervals.** With 20 tasks, a 100% verified rate has a 95% Wilson interval of
   roughly 84–100%. Differences below about 15 percentage points on 20 tasks are not resolvable,
   which is why the paired test is reported alongside.
6. **Mock is not a model.** Quickstart numbers (`3/3 verified`) measure the harness. They must not be
   compared with model results anywhere in the report.
7. **Model scale.** The course platform serves a 27B AWQ model; the reproducible configuration in §3
   uses an 8B model on a single consumer GPU. Absolute rates differ between the two. What we claim
   transfers is the direction of the ablation effect, not its magnitude.
8. **Job length.** The course partition caps a job at 1 hour, and serving a 27B model takes about 17
   minutes to become ready, leaving roughly 43 minutes per job. Long evaluations must be sharded
   with `--shard`.
9. **Storage is plaintext by default.** Encryption is opt-in and needs a separate key file; see
   `flagpilot/docs/WRITEUPS_AND_STORAGE.md`. Live results and downloads are always decrypted.

### 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `找不到题库索引` | `FLAGPILOT_TASKS` unset or wrong | Point it at a directory containing `index/tasks.jsonl` |
| Task-set consistency check fails | Index lists assets that are missing | Rebuild the index, or restore the files |
| Replay reports `/bin/sh: python3: not found` | Host started as an absolute path, so the sandbox `PATH` lacked the interpreter directory | Fixed in `flagpilot/sandbox.py`; the sandbox now prepends the resolved interpreter directory |
| `--split test` refuses to run | The test split runs once per configuration | Use `--split dev`; the guard is intentional |
| Web run returns 403 | Missing same-origin `Origin` header | Use the UI, or send `Origin: http://127.0.0.1:8080` |
| Encrypted run fails on `staging.rmdir()` | Host shim intercepts `rmdir` | Set `FLAGPILOT_RUNS` to a path outside the intercepted tree |

---

## Data and licensing

The bundled `demo-tasks/` is original to this artifact. The evaluation task set is derived from
picoCTF via InterCode-CTF and is not redistributed; `scripts/build_taskset.py` rebuilds it from the
upstream release. See `NOTICE` for third-party attribution.
