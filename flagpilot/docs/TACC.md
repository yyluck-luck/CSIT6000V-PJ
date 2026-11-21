# 提交到 TACC

> 面向第一次上手的人，按顺序做。`<>` 里的值由课程教程 / TA 回复填，不要照抄。
> 所有命令都在仓库根目录执行（下称 `flagpilot/`）。

---

## 课程平台（官方 tcloud SDK）——先看这里

课程 TACC 只允许用 SDK 提交、查看、下载（学生不登录服务器、不建 SSH 转发）。本文后面的 `run.slurm` /
`serve.slurm` 流程适用于通用 Slurm 集群；**课程平台请走 `deploy/tcloud/`**，2026-11-17 已实跑（作业 29323：
5 道 dev 题 4 道 verified）。课程平台的硬约束：分区上限 **1 小时**；模型固定为 `qwen3.8-27b`
（Qwen3.8-27B-AWQ-INT4，混合注意力，prefix cache 按 784 token 一块对齐）；`--max-num-seqs 4`、prefix cache 常开，
所以 E3 的"关缓存"与"8 并发"两格在课程服务上做不了。

```bash
# 全程只用课程 SDK（tcloud v0.4.1），在本机执行：不登录服务器、不 scp、不建 SSH 转发
deploy/tcloud/submit.sh doctor                         # CPU 自检：SDK 引导、课程 Python、泄漏检查
deploy/tcloud/submit.sh serve                          # qwen3.8-27b，2×3090，分区上限 1 小时
tcloud cat slurm_log/qwen-public-serve-<job>.log       # 等 "Application startup complete"，抄 SERVICE_BASE_URL
FP_LIMIT=5 deploy/tcloud/submit.sh batch http://<node>:8000/v1
tcloud download flagpilot-runs/tacc-smoke.md ./        # 报告
tcloud download -r flagpilot-runs/tacc-smoke ./        # 轨迹 → python3 -m flagpilot web --replay ./tacc-smoke
tcloud cancel -j <serve job>                            # 释放 GPU
# 或一条命令：deploy/tcloud/pipeline.sh <本地输出目录>（显存 / 8000 端口被占的节点自动排除重交，结束必取消服务）
```

**2026-11-19 起的增强**（作业 30030/30031 实跑验证）：

- **一个服务作业跑多档**：`FP_ABLATIONS=B3,B0,B1,B2 FP_LIMIT=0 FP_BATCH=tacc-dev-v5 deploy/tcloud/pipeline.sh runs/tacc/v5 "10-0-1-10,10-0-1-11,10-0-4-10"`
  在同一个 qwen 服务上依次跑四档（开服只付一次 5 分钟），每档一个批次 `<FP_BATCH>-Bx`；客户端按服务作业的
  截止时刻估算下一档还来不来得及，不够就整档跳过并在日志里写明怎么续跑。dev 20 题 × 4 档实测约 23 分钟。
- **补齐运行时缺的工具**：`bin/xxd`、`bin/exiftool` 是纯 Python 替身（xxd 与系统 xxd 逐字节一致），
  pycryptodome / gmpy2 的 manylinux wheel 在本机下好、随包解进作业目录，只经 `FLAGPILOT_SANDBOX_PYTHONPATH`
  进沙箱（动作与重放一致）。doctor 不再报这四样缺失。
- **调度**：批跑默认"预计最慢的先跑"（LPT），先验来自同目录下历史批次的 wall_s。
- **划分**：仓库里有团队冻结的 `docs/splits-picoctf-53.json` 就原样带上（以前每次现场重新生成；对同一份题库两者逐字节相同，
  但题库索引一旦变了，重新生成会悄悄换成另一种划分）。作业里的 CLI 会核对索引哈希：不符时 dev 警告、test 拒跑。
  v5 与 max_tokens 复验用的 20 道 dev 题正是冻结划分里 core 范围的 dev，与 test 无交集。
- **本机暂存交完即删**：网页会话里每条答疑、每次单题都要交一次作业，暂存目录以前从不删（单题约 15M、整批约 88M）。
  `FP_KEEP_STAGE=1` 保留，`FP_DRY_RUN=1` 只暂存不提交。
- **从网页发起**：`python3 -m flagpilot web --replay runs/tacc` → Lab → *TACC batch*：选档位与题量，点 *Run on TACC*；
  页面显示阶段（Queue → Server → Batch → Download → Done）、作业号、逐题结论，跑完把每档挂成回放（Runs 页可逐步复盘）。
- **网页实时会话（Qwen 27B on TACC）**：点 Solve 页的 *Qwen on TACC · connect* 或 Ask 页的 *connect Qwen*（或直接点 *Run on TACC*）会提交一个服务作业并常驻，
  时长按分区上限 1 小时、从作业真正开跑算起（排队不计），到点前 3 分钟自动取消，再点一次即断开。启动的几分钟里在 Ask 发的消息会排队，就绪后交给 Qwen，不会落到演示模型上。就绪后：
  - Solve 的 *Run on TACC*：只带这一道题交一个客户端小作业；作业把 trace 事件按行打进 Slurm 日志（`@@ev`），
    网页每 4 秒 `tcloud cat` 一次逐步显示，结束后下载运行目录并挂成回放。
  - Ask：每条消息一个小作业（`fp chat-once`），回复以 `@@delta` / `@@reply` 回传；历史问答随作业带上去，线程仍存本机 `runs/chat/`。
  - 平台上客户端作业共用一个工作目录，所以求解和答疑同一时间只跑一件，忙时网页会提示。
  - 2026-11-20 实测（服务作业 30111，节点 10-0-3-11）：开服到就绪 300 s；一条答疑往返 31 s；
    general-004（B3）从点击到 verified 64 s。
  - **一条回复为什么要半分钟**：课程平台只给 SDK，消息到不了常驻服务，每条都要交一个 Slurm 作业。
    实测拆分（作业 30163）：提交 35 s（SDK 按文件逐个上传，当时 37 个文件）→ 排队与启动 9 s → 生成 < 2 s。
    现在除 tuxiv.conf / job.sh / run.env 外打成一个 payload.tgz、代码打成 flagpilot.zip：
    不挂题 43.7 s → 26.6 s（作业 30165）；挂题（带 pycryptodome / gmpy2，以前 200 多个文件）约 79 s → 56 s（作业 30166）。
  - **排队中（on hold）**：另一个回复或单题正在跑时，新请求排队而不是报 "busy"；作业排着队时页面显示 Slurm 的原因
    （QOSMaxJobsPerUserLimit = 个人两个作业的名额已满，Resources = 没有空闲 GPU）。会话结束时取消还在排队的作业。
  - **重启网页服务不丢会话**：会话写在 runs/tacc/session.json，重启后核实服务作业还在跑就接回来。

| 档位（dev，n=20，qwen3.8-27b） | verified | 95% CI | raw | 平均轮次 | 整批 prefix cache |
|---|---|---|---|---|---|
| B0 | 70.0% | 48.1–85.5% | 80% | 6.5 | 76.2% |
| B1 | 65.0% | 43.3–81.9% | 80% | 6.75 | 75.9% |
| B2 | 60.0% | 38.7–78.1% | 70% | 7.1 | 74.4% |
| B3 | 60.0% | 38.7–78.1% | 65% | 8.25 | 84.4% |

四档区间完全重叠：n=20 时档位之间的差异不显著，这是要如实写进报告的结论，不是要调掉的噪声。

上表四档都是 `max_tokens=2048`。**max_tokens 复验**（2026-11-20，作业 30114，B3，同一批 20 道 dev 题，`FP_MAX_TOKENS=4096`）：

| B3 | verified | 95% CI | raw | 被截断的回复 | 每题输出 token | 平均墙钟 |
|---|---|---|---|---|---|---|
| 2048（v5） | 60.0%（12/20） | 38.7–78.1% | 65% | 5 条（3 题） | 2293 | 78.7 s |
| 4096 | 65.0%（13/20） | 43.3–81.9% | 70% | 0 | 2623 | 86.0 s |

逐题配对：原来解出的 12 题全部保持；唯一变化的是 crypto-079（2048 时第 2、3 轮被截断、最后耗尽轮次；
4096 时有 3 条回复超过 2048 token，最后 verified）。n=20 下不显著（McNemar 精确 p=1.0），所以把默认值改成 4096
的理由是去掉"截断 → 无效动作 → 整题出局"这一失败机制，而不是涨了 5 个点。上下文余量：实测单次最大 prompt
24.3k token，加 4096 仍在 32k 之内；万一放不下，客户端按 vLLM 的报错把这一轮的上限调小重试，不再误当成
"不认 chat_template_kwargs"而关掉思考开关。之后的批次默认 4096，四档要对比就整组重跑，不和上表混比。

**按收紧后的验证器重验**（main 的 #9：第 4 道关在"清空材料"之外，再用同名、等长的假材料跑一次）：把上面两张表里
TACC 判 verified 的 64 条提交，在本机分别用旧、新验证器重放（同一份题目附件；本机补齐 pycryptodome / gmpy2 /
Pillow 与 `python`、`zcat` 后 64 条在旧验证器下全部复现）。新验证器只翻转 2 条，都是 crypto-005（v5 的 B2、B3）：
脚本先读 `rot13.py`，正则落空时退回写死的密文，清空材料时 `open` 报错蒙混过关，换成假材料就露馅。
按新验证器：B0 70.0%、B1 65.0% 不变，B2、B3 各降到 55.0%（11/20，95% CI 34.2–74.2%），4096 的 B3 仍是 65.0%。
max_tokens 的配对变成 11 → 13（crypto-079 与 crypto-005 两对不一致，都偏向 4096；McNemar 精确 p=0.5），
其中 crypto-005 的差别来自脚本写法，与截断无关，所以上面"为去掉截断机制而改"的结论不变。

踩过的坑都已在脚本里处理：课程运行时（PRoot）没有 `python3` → 客户端垫一层；PRoot 解析不了节点名 →
宿主机上换成 IP；`tcloud cat` 输出带 `\r` → `submit.sh` 校验地址；部分节点有 Slurm 管不到的进程占着显存或
8000 端口 → 服务作业开服前体检，用 `FP_EXCLUDE=节点` 换节点。GPU 秒记在服务作业上，客户端批次不计。

## 0. 提交前必须问清的四件事

任何一条没确认，后面都可能白跑：

| # | 问题 | 卡在哪里 |
|---|---|---|
| 1 | 外部 API（如 DeepSeek）能否用于评测 | 决定 Track H 是否存在；不能用就只做单轨（H 轨不占 GPU、在本机跑，见第 14 节） |
| 2 | 课程提供的 `qwen-public-serve/` 能否改参数（`--max-num-seqs`、`--max-model-len`、prefix cache） | E3 的缓存消融要动服务端参数，8 并发格要 `--max-num-seqs ≥ 8`；改不了就只能做观察策略这一维 |
| 3 | `SERVICE_BASE_URL` 从集群外怎么访问（隧道 / 端口转发） | 决定能否在本地调前端，还是必须一体式作业内跑 |
| 4 | GPU 配额、是否固定 `gres=gpu:2`、2h 作业能否重复提交 | 决定总 attempts 能否跑完（见第 10 节算力账） |

同时确认：计算节点能否 `pip install --user`、能否起子进程、有没有 Apptainer。
没有 root 不等于装不了东西，但 `gdb` 这类 apt 包确实装不上——这会直接影响 RE 题能否做，
`doctor` 会报告 `java` / `javap` / `gdb` 是否可用。

---

## 1. 上传

```bash
./deploy/upload.sh <user>@<tacc-host> '~/flagpilot'
```

脚本做五件事，第 2 步是硬性的：

1. 打包仓库（含 `tasks/`，如果本地有）
2. **检查包里没有 `_private/`、`with-solutions/`、`solution/`**，有就中止
3. `scp` 到 `/tmp`
4. 远端解包到 `~/flagpilot`
5. 远端执行 `python3 -m flagpilot doctor`

为什么第 2 步必须是脚本而不是"记得别传"：

```
tasks/_private/gold_private.json      ← 全部题目的明文答案
```

这份文件一旦进集群，agent 只要 `find / -name 'gold_private.json'` 就能捞出所有答案，
而且**它在文件系统里，进程级沙箱挡不住**（没有 namespace 就不隔离路径）。
正确做法不是"防住 agent 去读"，而是"这台机器上根本没有这个文件"。`doctor` 会专门检查这一条。

不上传 `with-solutions/` 同理：题解目录就是答案。

---

## 2. 远端自检

```bash
ssh <user>@<tacc-host>
cd ~/flagpilot
python3 -m flagpilot doctor
```

重点看四行：

```
题目数           53  Cryptography=16, Forensics=12, General Skills=25
泄漏检查         通过（无明文 gold、无题解目录）
网络隔离         netns            ← 期望值；proxy-only 说明没有 namespace
java / javap     /usr/bin/java     ← 缺失则依赖 Java 的题做不了
```

- **`proxy-only` 不是隔离**，只是把 `http_proxy` 指向死端口。报告里必须如实写。
- **`java` / `javap` 缺失**：`conda install -c conda-forge openjdk` 试一下；装不上就把相关题目砍掉。

---

## 3. 冻结 dev / test 划分（一次性，团队决定）

proposal 的承诺是：所有设计决策只看 dev，test 只在配置定稿后跑一次。
划分在本地生成，放在题库的 `index/splits.json`，随题库一起上传（放在别处就用 `FLAGPILOT_SPLITS` 指过去）：

```bash
python3 scripts/make_split.py --root "$FLAGPILOT_TASKS" --seed 0 --test-frac 0.5
# 按类别分层：当前 53 题 → dev 27 / test 26
```

生成后 `status` 是 `proposed`。团队确认后手动改成 `frozen`，并把 seed、test_frac、文件 sha256 写进报告；
冻结后 `make_split.py` 拒绝覆盖。题库索引变了，`--split test` 直接拒跑（dev 只警告）。

之后的作业都带 `FP_SPLIT`：

| 用途 | 变量 | 效果 |
|---|---|---|
| 冒烟、E1 / E3 调参、选策略 | `FP_SPLIT=dev` | 只跑 dev 题；`FP_LIMIT` 在 dev 里取前 N 题 |
| 定稿后的唯一一次 test | `FP_SPLIT=test FP_CONFIRM_TEST=1` | 守卫按配置指纹记账到 `runs/test_runs.jsonl` |
| 不设 | — | 全部题（旧行为），只适合看系统能不能跑 |

守卫规则：没有 `FP_CONFIRM_TEST=1` 不跑；同一个配置换个批次名重跑会被拒；
同名批次到点重交是续跑，允许，但配置变了会被拒。两个容易踩的坑：

- **指纹里有服务地址。** 一体式 `run.slurm` 固定是 `127.0.0.1`；分离式每次节点不同，续跑会被当成换了配置——test 用一体式。
- **指纹认的是服务名 `FP_MODEL_NAME`，不是权重路径。** 换权重时同时换 `FP_MODEL_NAME`，否则会被当成同一个配置。

`run.slurm` 在起 vLLM 之前就检查划分文件，缺了直接退出，不白占 GPU。

---

## 4. 先跑冒烟批

```bash
cd ~/flagpilot && mkdir -p logs
sbatch --account=<alloc> --partition=<part> \
       --export=ALL,FP_BATCH=smoke,FP_LIMIT=20,FP_SPLIT=dev \
       deploy/run.slurm
```

`run.slurm` 在一个作业里做四件事：起 vLLM → 等 `/v1/models` 就绪 → `doctor` → 跑 20 题并出指标。
用 localhost 通信，不需要隧道，也不需要两个作业对齐排队。

看进度：

```bash
squeue -u $USER
tail -f logs/run-<jobid>.out
tail -f logs/vllm-<jobid>.log      # 模型加载、显存、报错都在这
```

**这 20 题决定后面所有事**。判据要提前定死，不要在拿到数字后改：

| 结果 | 含义 | 动作 |
|---|---|---|
| `verified > 0` | 选题成立 | 扩到全量，进 E1/E2 |
| `verified = 0` 但 `raw > 0` | 模型能解、脚本写不出来 | 这正是选题的卖点，改主张为"可复现性"，不要急着换题 |
| `raw = 0` | 模型在这个集合上无信号 | 换模型规模或换集合，别硬跑几千次 |

---

## 5. 冒烟通过后：一个作业跑完 E1 四档

```bash
sbatch --account=<alloc> --partition=<part> \
  --export=ALL,FP_BATCH=e1,FP_LIMIT=20,FP_WORKERS=1,FP_ABLATIONS="B0 B1 B2 B3" \
  deploy/run.slurm
```

四档在同一作业里顺序跑，复用同一个 vLLM 实例——省掉三次排队，这是把 E1 放进单作业的唯一理由。
结束时自动生成 `runs/e1-<jobid>.md` 对比表。

`FP_WORKERS=1` 不是保守：E1/E3 要读服务端 prefix cache 计数器，并发会让多个 attempt 共用一个计数器，
差值被摊薄，命中率算出来是错的。

调参阶段加 `FP_SPLIT=dev`；四档定稿后在 test 上各跑一次（`FP_SPLIT=test FP_CONFIRM_TEST=1`，
每档配置不同，守卫各记一次账）。相邻两档是否真有差别，用配对检验而不是只看两个百分比：

```bash
python3 -m flagpilot pair --a e1-B2 --b e1-B3      # 按 (题, seed) 配对，精确 McNemar
```

时间预算：20 题 × 4 档，单档约 20–40 分钟（14B，平均 3–5 轮）。四档接近 2 小时上限，
**先用 10 题试一次**；不够就降 `FP_LIMIT`，或按 `FP_SHARD=0/2`、`1/2` 分两个作业。

---

## 6. 记忆探针（E2）

```bash
sbatch --account=<alloc> --partition=<part> \
  --export=ALL,FP_BATCH=probe,FP_PROBE=1,FP_PROBE_MODE=brief \
  deploy/run.slurm
```

探针**必须在作业内跑**：计算节点没有外网，外部机器连不进 8000 端口。
它排在正式批跑之前，单轮调用，几十题在 14B 上大约 3–5 分钟。

三档严格度 `name / brief / files`，建议至少跑 `name` 和 `brief`：
两者的差就是"题面里本来就有的线索"贡献了多少命中。

---

## 7. E3 效率网格（prefix cache 开 / 关各一个作业）

prefix cache 开关要重启 vLLM，所以一个作业只测一种 cache 设置，交两个：

```bash
for pc in 1 0; do
  sbatch --account=<alloc> --partition=<part> \
    --export=ALL,FP_E3=1,FP_PREFIX_CACHE=$pc,FP_MAX_SEQS=8,FP_LIMIT=20,FP_SPLIT=dev \
    deploy/run.slurm
done
```

每个作业按顺序跑 6 格，一次只变一个维度，批次名 `e3-pc<1|0>-<策略>-t<轮次>-w<并发>`：

| 维度 | 格子 | 其余参数 |
|---|---|---|
| 观察策略 | `trunc`（只做追加时截断）、`summ`（只做回溯摘要） | 20 轮、单并发 |
| 轮次预算 | 40、60 | 策略 = `FP_E3_BEST`（默认 `trunc`）、单并发 |
| 并发 | 4、8 | 策略 = `FP_E3_BEST`、20 轮 |

结束时自动生成 `runs/e3-pc<1|0>-<jobid>.md`：verified 率、verified 解题时间 p50 / p95、轮次、
cache 命中率（服务端 / API）、prefill 占比、每个 verified 的 GPU 秒与成本；
每格对参照格 `e3-pc<1|0>-<BEST>-t20-w1` 给出 Δ，verified 率掉超过 5 个点标 ✗（质量护栏）。

- **命中率只在单并发格报**：并发时多个 attempt 共用服务端计数器，差值被摊薄；`report` 会自动置空并注明原因。
- **8 并发格要 `FP_MAX_SEQS=8`**：否则多出来的请求在 vLLM 里排队，测到的是排队不是并发——脚本会跳过该格并打印原因。
  显存不够就只报 1 / 4 并发。
- **GPU 秒**：脚本后台每 `FP_GPU_SAMPLE_S` 秒采一次 `nvidia-smi`（`runs/gpu_util-<jobid>.csv`），
  runner 按每个批次的时间窗记进 `runs/<batch>/gpu.jsonl`。申请口径 = 卡数 × 墙钟；busy 口径 = Σ 利用率 × Δt。
  vLLM 加载和批次之间的空档不算进任何批次。
- **时长**：6 格 × 20 题，单并发格按 E1 的经验 20–40 分钟一格，可能超过 2h。先用 `FP_LIMIT=10` 试；
  被杀就原样重交，已完成的题会跳过。

cache 开 / 关之间的对比、任意两格的配对检验：

```bash
python3 -m flagpilot compare --e3 --ref e3-pc1-trunc-t20-w1 \
  --batches e3-pc1-trunc-t20-w1,e3-pc0-trunc-t20-w1,e3-pc1-summ-t20-w1
python3 -m flagpilot pair --a e3-pc1-trunc-t20-w1 --b e3-pc1-summ-t20-w1
```

---

## 8. 分离式（需要本地调试前端时用）

**作业 1：服务**

```bash
sbatch --account=<alloc> --partition=<part> deploy/serve.slurm
cat runs/service_url.txt          # 形如 http://c123-456:8000/v1
```

**本地隧道**（前提是第 3 问的答复支持）：

```bash
ssh -N -L 8000:<节点名>:8000 <user>@<tacc-host>
# 另一个终端
FLAGPILOT_BASE_URL=http://127.0.0.1:8000/v1 \
  python3 -m flagpilot web --model local-model
```

**作业 2：评测**

```bash
SERVICE=http://<节点名>:8000/v1
sbatch --account=<alloc> --partition=<part> \
       --export=ALL,FLAGPILOT_BASE_URL=$SERVICE,FP_BATCH=dev,FP_SHARD=0/3 \
       deploy/client.slurm
```

代价：两个作业要自己对齐，服务 2h 到点会断，客户端还在跑就全废。所以除调试外不推荐。

---

## 9. 参数速查

| 变量 | 默认 | 说明 |
|---|---|---|
| `FP_MODEL_PATH` | `<待填>` | 权重路径或 HF id |
| `FP_MODEL_NAME` | `local-model` | 客户端用的名字，必须与 `--served-model-name` 一致；换权重就换名字（第 3 节） |
| `FP_TP` | 2 | tensor parallel，通常等于申请的 GPU 数 |
| `FP_MAX_LEN` | 32768 | 上下文上限 |
| `FP_MAX_SEQS` | 4 | **并发闸门**，别超过集群允许值；E3 的 8 并发格要 ≥ 8 |
| `FP_PREFIX_CACHE` | 1 | 设 0 关闭，用于 E3 缓存消融 |
| `FP_WORKERS` | 4 | 客户端并发，**不得大于 `FP_MAX_SEQS`** |
| `FP_TURNS` | 20 | 轮次上限 |
| `FP_SEEDS` | 1 | 每题重复次数 |
| `FP_LIMIT` | 空 | 只跑前 N 题（冒烟用） |
| `FP_SHARD` | 空 | `i/n`，多作业并行切片 |
| `FP_SPLIT` | 空 | `dev` / `test`；空 = 全部题（第 3 节） |
| `FP_CONFIRM_TEST` | 0 | 跑 test 必须设 1 |
| `FP_ABLATIONS` | 空 | 空格分隔，如 `"B0 B1 B2 B3"`，四档顺序跑 |
| `FP_PROBE` | 0 | 设 1 额外跑记忆探针 |
| `FP_E3` | 0 | 设 1 跑 E3 网格（第 7 节） |
| `FP_E3_BEST` | `trunc` | 轮次、并发两维挂在哪个观察策略上 |
| `FP_E3_POLICIES` / `FP_E3_TURNS` / `FP_E3_WORKERS` | `trunc summ` / `40 60` / `4 8` | 网格三维的取值 |
| `FP_GPU_SAMPLE_S` | 2 | `nvidia-smi` 采样间隔（秒） |

`#SBATCH` 里的 `REPLACE_ALLOCATION` / `REPLACE_PARTITION` 是占位符，
必须用命令行的 `--account` / `--partition` 覆盖，或者自己改文件。

---

## 10. 算力账（提交前先算一遍）

按 1800 attempts 估算：

```
单 attempt  ≈ 15 轮 × 350 输出 token ≈ 5.3k token
4 并发聚合吞吐 ≈ 120 tok/s（TP=2、14B、32k）
1800 × 5.3k / 120 ≈ 22 h 纯解码
加 prefill + 工具等待 ≈ 15–20 个 2h 作业
```

E3 按第 7 节的网格（不做全因子，一次只变一个维度）：2 个作业 × 6 格 × 20 题 = 240 attempts / seed。
按 E1 的时间估算，单 seed 约 2–4 个 2h 作业（轮次 40 / 60 的格子更慢，按实测再调）。
先跑 20 题，用真实数字替换上面每个假设，再决定要不要缩网格。

---

## 11. 中途断了怎么办

`runs/<batch>/runs.jsonl` 逐行追加，一条一个 attempt。**同一个 batch 再交一次会自动跳过已完成的**：

```bash
sbatch --export=ALL,FP_BATCH=dev deploy/run.slurm      # 接着跑
```

分片并行（不同作业跑不同题目，互不重叠）：

```bash
for i in 0 1 2 3; do
  sbatch --export=ALL,FP_BATCH=dev,FP_SHARD=$i/4 deploy/run.slurm
done
```

这是 2h 作业上限下唯一可行的做法：**不要指望一个作业跑完全量**。

---

## 12. 拉回结果

```bash
rsync -avz <user>@<tacc-host>:~/flagpilot/runs/ ./runs_remote/
python3 -m flagpilot report --batch dev        # 本地也能出同一份指标
```

`runs/<batch>/<run_id>/` 下有三样值得带回：`trace.jsonl`（逐步证据）、
`replay/`（重放目录）、`work/`（agent 操作现场）。失败分析全靠这些。

另外两份账本也要带回：`runs/<batch>/gpu.jsonl`（GPU 秒，`report` / `compare` 自动读取）
和 `runs/test_runs.jsonl`（test 守卫记录，进报告附录）。

---

## 13. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 作业秒退，`logs/*.err` 说找不到 vllm | 没激活环境 | 在 slurm 里加 `source .../conda.sh && conda activate <env>` |
| `CUDA out of memory` | 上下文 × 并发太大 | 先降 `FP_MAX_SEQS` 到 2，再降 `FP_MAX_LEN` |
| 服务起来但客户端连不上 | 分离式作业不在同一节点 | 改用一体式 `run.slurm`，或按第 8 节做隧道 |
| 4 并发之外全部超时 | `FP_WORKERS` > `FP_MAX_SEQS` | 把 workers 压到 ≤ max-num-seqs |
| 大量 `unverified / literal_flag` | 模型在背答案 | 这是**结果**不是故障，正是 E2 要量的东西 |
| 大量 `unverified / timeout` | 重放超时 | 提高 `FP_REPLAY_TIMEOUT`，或查题目是否需要暴力枚举 |
| 作业到 2h 被 kill | 正常 | 重新提交，续跑会跳过已完成的 |
| `doctor` 报 `proxy-only` | 无 user namespace | 正常现象；报告里写"proxy-only，非强隔离" |
| 起 vLLM 前就退出，`[run] 划分文件不可用` | 集群上没有 `splits.json` | 本地生成后随题库上传，或用 `FLAGPILOT_SPLITS` 指定（第 3 节） |
| `同一个最终配置已经在 test 上跑过` | 守卫拦下了换名重跑 | 这是设计；要换配置先回 dev 定稿 |
| `test 批次 … 已用另一套配置跑过` | 同名批次续跑时配置变了（含服务地址、`FP_MODEL_NAME`） | test 用一体式 `run.slurm`；真换了配置就换批次名 |
| `[e3] 跳过 …：workers=8 > max-num-seqs=4` | 没设 `FP_MAX_SEQS=8` | 提交时加上；显存不够就只报 1 / 4 并发 |
| 报告里 cache 命中率是"—"并附说明 | 并发格的服务端计数器被摊薄，已自动置空 | 正常；命中率只看单并发格 |

---

## 14. Track H（DeepSeek）不进集群

H 轨调用托管 API：不占 GPU，而且计算节点没有外网，所以在本机跑（前提是第 0 节第 1 问得到允许）：

```bash
# 密钥写进仓库根目录的 .env（已 gitignore）：DSV41_API_KEY=...（也认 DEEPSEEK_API_KEY）
scripts/track_h.sh h-smoke --limit 1                 # 先冒烟一题
scripts/track_h.sh h-dev --split dev --workers 4     # 结束时导出 runs/h-dev.md
```

- `--track H` 自动补齐模型 `deepseek-flash`、端点 `https://api.deepseek.com`（`FLAGPILOT_H_BASE_URL` 可改）和价目表；
  请求里用 DeepSeek 自己的 `thinking.type` 显式关掉思考模式（它不认 vLLM 的 `chat_template_kwargs`）。
- 成本按官方价目逐轮计：缓存命中 / 未命中 / 输出分开计价；工作日 UTC 01–04、06–10（北京时间 09–12、14–18）是高峰，价格翻倍。
  价目在 `flagpilot/pricing.py`（2026-11-16 核对），调价时只改那一处。
- L 轨的成本口径是 GPU 秒，H 轨是美元，报告里分开列，不直接相加。
- **还没用真实 key 跑过**：第一次只跑一题，把 `report` 的成本和 DeepSeek 后台账单对一下。
