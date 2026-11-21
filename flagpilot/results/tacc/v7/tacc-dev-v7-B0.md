### tacc-dev-v7-B0（n=40）

| 指标 | 值 |
|---|---|
| attempts | 40 |
| verified 解出率 | 82.5%（95% CI 68.0%–91.2%） |
| raw（flag 正确） | 82.5%（95% CI 68.0%–91.2%） |
| raw − verified gap | 0.0% |
| wrong / budget / error | 1 / 6 / 0 |
| 平均轮次 | 5.47 |
| 平均墙钟 | 48.12 s |
| 平均 token | 16148 |
| TTFT p50 | 519 ms |
| 墙钟 p50 / p95（全部 attempt） | 28.4 / 150.7 s（n=40） |
| 解题耗时 p50 / p95（verified，端到端） | 27.8 / 50.0 s（n=33） |
| prefix cache 命中率 | —（并发>1：服务端计数器被多个 attempt 分摊，不报命中率/prefill 占比） |
| prefix cache 命中率（整批） | 74.0%（429632/580257 token；独占服务时成立） |

| 类别 | n | verified | raw | v_rate | r_rate |
|---|---|---|---|---|---|
| Cryptography | 16 | 11 | 11 | 69% | 69% |
| Forensics | 12 | 10 | 10 | 83% | 83% |
| General Skills | 12 | 12 | 12 | 100% | 100% |

| 失败标签 | 计数 | 占失败比 |
|---|---|---|
| 不守 JSON 协议 | 6 | 86% |
| 答案错误 | 1 | 14% |
| 环境缺工具 | 1 | 14% |

共 7 条失败；同一条可命中多个标签，占比之和可超过 100%。
