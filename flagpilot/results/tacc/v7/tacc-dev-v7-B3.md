### tacc-dev-v7-B3（n=40）

| 指标 | 值 |
|---|---|
| attempts | 40 |
| verified 解出率 | 87.5%（95% CI 73.9%–94.5%） |
| raw（flag 正确） | 90.0%（95% CI 77.0%–96.0%） |
| raw − verified gap | 2.5% |
| wrong / budget / error | 2 / 2 / 0 |
| 平均轮次 | 5.9 |
| 平均墙钟 | 46.52 s |
| 平均 token | 20947 |
| TTFT p50 | 630 ms |
| 墙钟 p50 / p95（全部 attempt） | 32.8 / 140.9 s（n=40） |
| 解题耗时 p50 / p95（verified，端到端） | 28.6 / 78.2 s（n=35） |
| prefix cache 命中率 | —（并发>1：服务端计数器被多个 attempt 分摊，不报命中率/prefill 占比） |
| prefix cache 命中率（整批） | 78.5%（615440/783983 token；独占服务时成立） |

| 类别 | n | verified | raw | v_rate | r_rate |
|---|---|---|---|---|---|
| Cryptography | 16 | 13 | 14 | 81% | 88% |
| Forensics | 12 | 10 | 10 | 83% | 83% |
| General Skills | 12 | 12 | 12 | 100% | 100% |

| 失败标签 | 计数 | 占失败比 |
|---|---|---|
| 不守 JSON 协议 | 3 | 60% |
| 答案错误 | 2 | 40% |
| 脚本硬编码答案 | 1 | 20% |

共 5 条失败；同一条可命中多个标签，占比之和可超过 100%。
