# LLMesh

**Secure Local LLM Swarm over MCP** 是一个以安全为优先的 P2P mesh PoC，用于让多个本地 LLM 节点在受控环境中协同工作。

LLMesh 将运行在 Ollama 或 llama.cpp 上的本地 LLM 节点，通过带签名的 MCP 风格 HTTP tool interface 连接起来。它面向代码生成、测试生成、代码审查、输出评价等分布式工作流，并从一开始就内置 fail-closed Prompt Firewall、基于 JSON Schema 的 I/O 验证、HMAC 链式审计日志和依赖风险检查。

> **状态:** 526 tests passing / 0 failures / Critical 0 / High 0。
> **成熟度:** research / PoC。当前目标是可信 LAN 或单一操作者管理的多台 PC 环境。它尚未针对任意公网节点的开放式互联场景完成充分加固。

## 为什么需要 LLMesh

Local LLM 在隐私和保密性方面很有吸引力，但单个节点的能力和专业性有限。另一方面，一旦连接多个节点，就会引入 prompt leakage、恶意 patch、依赖攻击、replay、节点冒充等风险。

LLMesh 的目标，是为 Local LLM Swarm 实验提供一个默认偏向安全的起点。跨节点请求必须签名，tool 输出必须通过 7 阶段 OutputValidator，疑似包含秘密信息的 prompt 会 fail-closed 阻断，允许通过的决策会写入 HMAC 链式审计日志。

## 主要特性

- **Ed25519 Node Identity**: 提供稳定的 `peer:` ID 和 `did:llmesh:1:` 标识符。
- **Signed Capability Manifest**: 节点以带 TTL 的签名 manifest 发布 tool、subnet 和 policy。
- **TOFU + signed P2P discovery**: 通过 rendezvous server 和 gossip pull 进行带初次确认的 peer discovery。
- **Request-level auth**: 对 `METHOD`, `PATH`, `NODE_ID`, timestamp, `BODY_SHA256` 进行请求级签名。
- **Fail-closed Prompt Firewall**: guard component 发生异常时默认返回 BLOCK。
- **OutputValidator 7-stage gate**: 包括 size cap、JSON parse、schema validation、nonce echo、UUIDv4 task_id、replay store 和 OSV SCA Gate。
- **HMAC append-only AuditTrace**: L3/L4 prompt 正文不会进入日志，只保存 SHA-256。
- **Container hardening**: sandbox 设计包括 network none、cap drop、read-only、tmpfs noexec、no-new-privileges 和 non-root UID。

## 安装

从 PyPI 安装:

```bash
pip install llmesh-mcp
```

从源码进行开发:

```bash
git clone https://github.com/furuse-kazufumi/llmesh.git
cd llmesh
pip install -e ".[dev]"
python -m pytest
```

## 5-node PoC

```bash
docker compose -f docker-compose.poc.yml up --build
```

该 PoC 会启动 4 个 worker node 和 1 个 orchestrator。

- generate_code
- generate_tests
- review_code
- critique_output
- orchestrator

## 发布链接

- GitHub: <https://github.com/furuse-kazufumi/llmesh>
- PyPI: <https://pypi.org/project/llmesh-mcp/>
- Qiita: <https://qiita.com/furuse-kazufumi/items/ac398349ec42e40913f1>
- LinkedIn: <https://www.linkedin.com/feed/update/urn:li:share:7457372822668230657/>

## 后续计划

- 将 NonceStore 持久化到 SQLite
- 为 AuditTrace 增加 file lock 支持
- 为 TrustedPeers 增加大小上限和 gossip TTL
- 让 CapabilityManifest 的签名字段列表具备 schema-version-aware 能力
- 对 L3+ 输入强制执行 Firewall → PrivacySummarizer → LLMBackend pipeline

## License

Apache License 2.0。

Copyright 2026 Kazufumi Furuse.
