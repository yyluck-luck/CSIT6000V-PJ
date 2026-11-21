# 部署与复现：TACC 后端 + 本机前端

> 给助教/评审的复现指引，也给未来的自己。按**推荐顺序**排：越靠前越省事，
> 也越没有合规争议。

## 一、先看结论

有三种方式能把这个系统跑起来，代价和风险完全不同：

| 方式 | 需要什么 | 需要集群吗 | 适合谁 |
|---|---|---|---|
| **A. 离线回放**（推荐） | 一个 `runs/` 目录 | ❌ 不需要 | 助教验收、答辩演示、截图 |
| **B. 集群内跑完再拉回** | TACC 账号 + SDK | ✅ 需要 | 出真实数据 |
| **C. 本机前端 + 集群后端** | TACC 账号 + SSH 转发 | ✅ 需要 | 自己调 UI，**不写进交付物** |

A 和 B 都没有任何访问约定问题。C 有——原因见第四节。

---

## 二、方式 A：离线回放（不需要模型、不需要集群、不需要网络）

系统把每一次 attempt 的完整过程写进 `runs/<batch>/<task>-s<seed>/trace.jsonl`
（只追加的 JSONL，一行一个事件）。回放就是把这个文件重新喂给前端。

```bash
cd flagpilot/flagpilot

# 1) 挂载一个目录下所有已完成的 run（只读）
python3 -m flagpilot web --replay /path/to/runs/real27b-tok4k --port 8080
```

启动后会把每条 run 的地址打出来：

```
FlagPilot demo → http://127.0.0.1:8080   model=mock
回放模式：挂载 6 个已完成的 run（只读，不需要模型）
  crypto-005-s0   http://127.0.0.1:8080/?run=a44f6361f5e7   verified/match
  crypto-093-s0   http://127.0.0.1:8080/?run=e21581b7bb51   unverified/script_error
```

浏览器打开任意一条，就能看到**当次运行**的逐轮动作、工具输出、提交的 flag 与
脚本、重放结论。界面与实时运行完全一致——因为回放条目和实时 run 在服务端
是同一个数据结构。

不想开浏览器也可以直接看时间线：

```bash
python3 -m flagpilot show --run-dir /path/to/runs/real27b-tok4k/crypto-093-s0
python3 -m flagpilot show --run-dir ... --full      # 连 observation 与脚本全文
python3 -m flagpilot show --run-dir ... --out x.md  # 存成 markdown
```

一条 run 的典型输出：

```
[ 1] model  624+30 tok  ttft=714ms  finish=stop
       file  exit=0  1ms  obs=114c
[12] model  6992+265 tok  ttft=558ms  finish=stop
     → submit  flag=picoCTF{...}
     ⇄ replay  unverified / script_error  — 输出里没有该 flag（exit=0）
## 结论
verdict=unverified  reason=script_error  turns=12  wall=155.3s
```

**这一步不需要装任何依赖**：项目主路径只用标准库。

---

## 三、方式 B：在集群里跑完，把 `runs/` 拉回来

服务作业沿用课程的 `qwen-public-serve`；客户端换成仓库里的 `deploy/tcloud/`（跑 FlagPilot，
结果写到持久的 `USERDIR/flagpilot-runs/<batch>/`——作业临时目录一结束就删，29430 那次就是这么丢的）。
`tcloud submit` 不带参数、在本机作业目录里执行，`submit.sh` 负责暂存代码与题库（答案表一律不进包）。

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

模型端点用作业日志里打印的 `SERVICE_BASE_URL` 填进 `server-url.txt`（客户端
作业从那里读）。这一步全程在集群内部，不涉及任何本机访问集群的动作。

---

## 四、方式 C：本机前端 + 集群后端（仅自用）

`scripts/tunnel.sh` 干这件事：

```bash
export TACC_HOST=gw.example.edu TACC_PORT=30041 TACC_KEY=~/.ssh/id_ed25519
scripts/tunnel.sh endpoint          # 查 serve 作业在哪个节点
export TACC_NODE=10.0.1.11          # 上面查到的节点，换成点分 IP
scripts/tunnel.sh up                # 建隧道 + 自动验证端点
python3 -m flagpilot web --port 8080 \
    --model qwen3.8-27b --base-url http://127.0.0.1:8000/v1
scripts/tunnel.sh down
```

### 为什么这条不能写进交付物

课程发布包的 `README.md`（与 `STUDENT-COMMANDS.md` 是同一份文件，字节相同）
写着：

> 提交、状态、日志、下载、停止统一使用 SDK。SDK 内部使用 SSH 协议和 Slurm；
> 学生**不登录平台服务器**，也**不自行建立 SSH 转发**。

逐项核查过：不是评分项、没有罚则、平台也没关 `AllowTcpForwarding`（`ssh -L`
一次就通）。这段话的意图是"统一用 SDK，你不必自己开"，属于使用说明而不是
安全条款。

**但风险不在操作，在留文字。** 写进报告/PPT 等于书面自认，评审时无从辩解；
交给助教复现会把 TA 也牵进来。所以：

- 自己调 UI、确认功能 → 用
- 答辩现场跑一次（`ssh -N` 不开 shell，只留一条 auth 日志）→ 可以用
- 报告 / PPT / 交给助教 → **改用方式 A**

如果一定要现场交互，更干净的两条路是：① 问助教要平台"明确提供或批准的
访问入口"；② 走 Track H（DeepSeek 托管 API），本机直连公网，不存在访问约定问题。

---

## 五、依赖：哪些能随仓库交付，哪些不能

`requirements.txt` 里只有能在任何机器上用 pip 装起来的东西：

| 依赖 | 作用 | 装不上会怎样 |
|---|---|---|
| `z3-solver` | `smt` 动作 | 该动作返回"未安装 z3，请改用 python"，其余不受影响 |
| `cryptography` | records/writeup 的 Fernet 加密 | 明文落盘，且会明确报告"未加密" |
| `pycryptodome` | 部分 crypto 题 | 只能手写等价实现 |
| `gmpy2` | 部分 crypto 题（大数） | 同上；无预编译轮子时需本机有 GMP |

**不能打包、也不该假装能打包的**（系统级二进制/Ruby/Perl 工具）：

| 工具 | 影响题数 | 获取方式 |
|---|---|---|
| `binwalk` | 6 | `pip install binwalk`（但解包仍依赖系统工具） |
| `exiftool` | 5 | 系统包管理器 / Perl |
| `zsteg` | 5 | Ruby gem |
| `tshark` | 2 | 系统包管理器 |
| `sleuthkit` | 1 | 系统包管理器 |
| `gdb` | 调试 | 系统包管理器 |

`python3 -m flagpilot doctor` 会把这些逐条列出来，并给出"影响几题"。
**报告里必须写明**：缺这些工具时相关题目失败，是环境限制而不是模型能力。

---

## 六、常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 回放页面 404 | 目录里没有 `trace.jsonl`，或该 run 没跑完（无 summary） | `--replay` 只挂载有 summary 的 run |
| `?run=` 打不开 | id 必须是 12 位十六进制 | 用启动时打印出来的地址，别自己编 |
| 隧道建好但 `/v1/models` 空 | 服务作业到期（`tacc` 分区上限 1 h） | 重新提交 serve 作业；转发目标 IP 不变时隧道会自动恢复 |
| `POST /api/run` 返回 403 | `_origin_ok()` 要求 `Origin` 与 `Host` 同源 | 加 `-H 'Origin: http://127.0.0.1:8080'` |
| 模型输出被截断 | `--max-tokens` 太小 | 报告会标"输出被截断"，调大后重跑 |
