# 让工业 IoT 和 LLM 在同一个框架里跑起来 — llmesh

> 我正在设计并实现 `llmesh-mcp` v3.1.0：一个跨 MCP 的安全 LLM 网格 (Secure LLM Mesh over MCP)。
> 既是为了**理解 AI 真正怎么落地**，也是为了**把自己的工程职业锚定在那些不显眼但难的问题上**。

## 为什么开始

关于 LLM 的讨论，绝大多数默认了**云端为先、对话为先**。但车间、变电站、医院、交易席的现场，活在一套完全不同的约束里：

- 数据不能离开机构边界
- 每个动作都得留下审计证迹
- 现场说的是 Modbus / OPC-UA / MQTT / Serial / EtherCAT / BACnet，不是 REST

`llmesh` 是我把这条裂缝**在同一个框架里**填上的个人尝试。云端 LLM (OpenAI / Azure / Anthropic / OpenRouter / Groq / Together / Mistral / DeepSeek) 和本地 LLM (Ollama / llama.cpp) 都坐在**同一个 ABC**之下，工业协议从第一天起就是一等公民。

## 8 个设计支柱

1. **协议横跨** — Modbus / OPC-UA / MQTT / Serial / EtherCAT / BACnet / HTTP(S) / WebSocket / gRPC / Email / SSH / FTP / SNMP / NTP 统一在一个框架。
2. **云端与本地 LLM 同一 ABC** — chat / tool call / streaming / JSON mode 调用面完全一致。
3. **MCP 合规** — 上层 agent 通过 Anthropic 的 Model Context Protocol，保持厂商中立。
4. **隐私流水线** — 4 层过滤 (PII detect / mask / consent / audit)，不允许被无意绕过。
5. **TimelineStore** — sensor / SPC / RAG / audit / trace 以 5 元组 (`task_id, node_id, event_type, timestamp_utc, metadata`) 时序保存。可视化侧 (`llove`) 只读不写。
6. **Trusted Peers + mTLS** — 按 peer 名字白名单，定位在内网内部。
7. **Rust 扩展带来 6× 提速** — 性能热点下沉到 Rust，Python 的易用性保持不变。
8. **OWASP 静态审计零问题** — 零 `shell=True` / `pickle` / `eval` / SQL 注入 / 弱加密。所有 HTTP 客户端都有响应大小上限。SemVer 正式启用。

## 为什么这对我的职业很重要

LLM 热潮总把聚光灯留给炫酷 demo，但**真正让产品停下来的，是那些不起眼的约束**。做 `llmesh` 留给我的，是实现层面的判断力，不是 buzzword：

- 在云端 LLM 用不了的现场，我能在**设计层面决定**：保留什么、替换什么、放弃什么。
- 我建立起一种**工业协议与 LLM 事件共用同一时间轴**的集成模式，让系统可观测。
- 我得到了**按职责拆分 Rust 与 Python 共存**的真实经验 (PyO3 + 热路径剖析 + 5× 门禁)。
- 我从第一天起就把 **OWASP 静态分析零问题 + SemVer** 作为硬规则 — 这在 LLM OSS 里并不常见。

这些技能，在受监管行业、基础设施、制造业、大企业 SI 的 AI 团队，都会被实际追问。

## 当前状态 (2026-05-14)

- **v3.1.0** — Secure LLM Mesh over MCP。117 章 / 500+ 需求 / 2300+ 测试全通过。
- OWASP 静态审计零问题；SemVer 正式启用 (`docs/API_STABILITY.md` 即公开符号契约)。
- Rust 扩展带来 **6×** 提速。
- 家族：后端 `llmesh` / TUI 仪表盘 `llove` / 自演化 LLM `llive` / 一次性安装 `pip install llmesh-suite` (筹备中)。
- PyPI: `pip install llmesh-mcp`。

## 走向哪里

`llmesh` 想成为**工程师在受监管环境推进 AI 落地时可以拿来论证的参考实现**。把它和 `llove`（TUI 仪表盘）、`llive`（自演化模块化记忆 LLM）组合起来，就是一套不依赖云、保留审计证迹、可在现场观测的 **LLM × 工业 IoT** 栈。

> GitHub: <https://github.com/furuse-kazufumi/llmesh>
> PyPI: `pip install llmesh-mcp`

#AI #LLM #工业物联网 #MCP #ModelContextProtocol #MLOps #Rust #开源 #个人项目 #职业发展
