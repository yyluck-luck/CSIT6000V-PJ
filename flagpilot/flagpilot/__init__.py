"""FlagPilot —— 重放验证型 CTF Agent。

模块划分（与 proposal 第 2 节的图一一对应）：

    tasks.py     题库接口（读索引，只拿 sha256，不碰明文 gold）
    sandbox.py   执行沙箱：独立工作目录、只读题目、资源上限、可选网络隔离
    tools.py     动作适配器：shell / python / 文件浏览
    client.py    模型客户端：OpenAI 兼容 HTTP + SSE，计量 TTFT / token / 缓存
    agent.py     Agent 循环：JSON 动作 → 执行 → 追加观察 → 预算终止
    verify.py    重放验证器：干净目录重跑 solve.py，扫字面量 flag
    runner.py    批量运行：并发闸门、断点续跑、分片
    metrics.py   Wilson 区间、raw vs verified、成本聚合
"""

__version__ = "0.1.0"
