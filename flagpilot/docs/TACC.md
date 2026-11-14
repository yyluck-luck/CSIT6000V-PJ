# 提交到 TACC

> 面向第一次上手的人，按顺序做。`<>` 里的值由课程教程 / TA 回复填，不要照抄。
> 所有命令都在仓库根目录执行（下称 `flagpilot/`）。

---

## 0. 提交前必须问清的四件事

任何一条没确认，后面都可能白跑：

| # | 问题 | 卡在哪里 |
|---|---|---|
| 1 | 外部 API（如 DeepSeek）能否用于评测 | 决定 Track H 是否存在；不能用就只做单轨 |
| 2 | 课程提供的 `qwen-public-serve/` 能否改参数（`--max-num-seqs`、`--max-model-len`、prefix cache） | E3 的缓存消融要动服务端参数；改不了就只能做观察策略这一维 |
| 3 | `SERVICE_BASE_URL` 从集群外怎么访问（隧道 / 端口转发） | 决定能否在本地调前端，还是必须一体式作业内跑 |
| 4 | GPU 配额、是否固定 `gres=gpu:2`、2h 作业能否重复提交 | 决定总 attempts 能否跑完（见第 8 节算力账） |

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

## 3. 先跑冒烟批

```bash
cd ~/flagpilot && mkdir -p logs
sbatch --account=<alloc> --partition=<part> \
       --export=ALL,FP_BATCH=smoke,FP_LIMIT=20 \
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

## 4. 冒烟通过后：一个作业跑完 E1 四档

```bash
sbatch --account=<alloc> --partition=<part> \
  --export=ALL,FP_BATCH=e1,FP_LIMIT=20,FP_WORKERS=1,FP_ABLATIONS="B0 B1 B2 B3" \
  deploy/run.slurm
```

四档在同一作业里顺序跑，复用同一个 vLLM 实例——省掉三次排队，这是把 E1 放进单作业的唯一理由。
结束时自动生成 `runs/e1-<jobid>.md` 对比表。

`FP_WORKERS=1` 不是保守：E1/E3 要读服务端 prefix cache 计数器，并发会让多个 attempt 共用一个计数器，
差值被摊薄，命中率算出来是错的。

时间预算：20 题 × 4 档，单档约 20–40 分钟（14B，平均 3–5 轮）。四档接近 2 小时上限，
**先用 10 题试一次**；不够就降 `FP_LIMIT`，或按 `FP_SHARD=0/2`、`1/2` 分两个作业。

---

## 5. 记忆探针（E2）

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

## 6. 分离式（需要本地调试前端时用）

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

## 7. 参数速查

| 变量 | 默认 | 说明 |
|---|---|---|
| `FP_MODEL_PATH` | `<待填>` | 权重路径或 HF id |
| `FP_MODEL_NAME` | `local-model` | 客户端用的名字，必须与 `--served-model-name` 一致 |
| `FP_TP` | 2 | tensor parallel，通常等于申请的 GPU 数 |
| `FP_MAX_LEN` | 32768 | 上下文上限 |
| `FP_MAX_SEQS` | 4 | **并发闸门**，别超过集群允许值 |
| `FP_PREFIX_CACHE` | 1 | 设 0 关闭，用于 E3 缓存消融 |
| `FP_WORKERS` | 4 | 客户端并发，**不得大于 `FP_MAX_SEQS`** |
| `FP_TURNS` | 20 | 轮次上限 |
| `FP_SEEDS` | 1 | 每题重复次数 |
| `FP_LIMIT` | 空 | 只跑前 N 题（冒烟用） |
| `FP_SHARD` | 空 | `i/n`，多作业并行切片 |
| `FP_ABLATIONS` | 空 | 空格分隔，如 `"B0 B1 B2 B3"`，四档顺序跑 |
| `FP_PROBE` | 0 | 设 1 额外跑记忆探针 |

`#SBATCH` 里的 `REPLACE_ALLOCATION` / `REPLACE_PARTITION` 是占位符，
必须用命令行的 `--account` / `--partition` 覆盖，或者自己改文件。

---

## 8. 算力账（提交前先算一遍）

按 1800 attempts 估算：

```
单 attempt  ≈ 15 轮 × 350 输出 token ≈ 5.3k token
4 并发聚合吞吐 ≈ 120 tok/s（TP=2、14B、32k）
1800 × 5.3k / 120 ≈ 22 h 纯解码
加 prefill + 工具等待 ≈ 15–20 个 2h 作业
```

E3 的 2×2 网格、轮次预算三档、1/4/8 并发还没算进去，实际在 4000–5000 attempts 量级，
即 **40–50 个 2h 作业**。先跑 20 题，用真实数字替换上面每个假设，再决定要不要缩网格。

---

## 9. 中途断了怎么办

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

## 10. 拉回结果

```bash
rsync -avz <user>@<tacc-host>:~/flagpilot/runs/ ./runs_remote/
python3 -m flagpilot report --batch dev        # 本地也能出同一份指标
```

`runs/<batch>/<run_id>/` 下有三样值得带回：`trace.jsonl`（逐步证据）、
`replay/`（重放目录）、`work/`（agent 操作现场）。失败分析全靠这些。

---

## 11. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 作业秒退，`logs/*.err` 说找不到 vllm | 没激活环境 | 在 slurm 里加 `source .../conda.sh && conda activate <env>` |
| `CUDA out of memory` | 上下文 × 并发太大 | 先降 `FP_MAX_SEQS` 到 2，再降 `FP_MAX_LEN` |
| 服务起来但客户端连不上 | 分离式作业不在同一节点 | 改用一体式 `run.slurm`，或按第 6 节做隧道 |
| 4 并发之外全部超时 | `FP_WORKERS` > `FP_MAX_SEQS` | 把 workers 压到 ≤ max-num-seqs |
| 大量 `unverified / literal_flag` | 模型在背答案 | 这是**结果**不是故障，正是 E2 要量的东西 |
| 大量 `unverified / timeout` | 重放超时 | 提高 `FP_REPLAY_TIMEOUT`，或查题目是否需要暴力枚举 |
| 作业到 2h 被 kill | 正常 | 重新提交，续跑会跳过已完成的 |
| `doctor` 报 `proxy-only` | 无 user namespace | 正常现象；报告里写"proxy-only，非强隔离" |
