# LLMesh Launch Kit

This document collects the public launch materials for LLMesh.

## Publication Plan

### Recommended order

1. Publish GitHub repository.
2. Create GitHub v0.1.0 release.
3. Publish package to TestPyPI.
4. Publish package to PyPI.
5. Publish Qiita article in Japanese.
6. Publish LinkedIn launch post in English and Japanese.

### Naming

| Surface | Recommended value |
|---|---|
| Project brand | LLMesh |
| GitHub repository | `llmesh` |
| GitHub owner | `furuse-kazufumi` |
| Formal description | Secure Local LLM Swarm over MCP |
| Python import package | `llmesh` |
| PyPI distribution name | `llmesh-mcp` |

`llmesh` is already used on PyPI by another project, so the recommended PyPI distribution name is `llmesh-mcp`. The Python import package can remain `llmesh`.

## GitHub Repository Setup

### Repository name

```text
llmesh
```

### Repository description

```text
Security-first local LLM swarm over MCP with signed peer discovery, fail-closed validation, audit trails, and Docker-based PoC nodes.
```

### Topics

```text
local-llm
mcp
model-context-protocol
ollama
llama-cpp
p2p
security
ed25519
audit-log
fastapi
llm-agents
ai-security
```

### GitHub About section

```text
LLMesh is a security-first peer-to-peer mesh for collaborative local LLM workflows. It connects trusted local LLM nodes over signed MCP calls with fail-closed prompt filtering, schema-validated outputs, SCA gating, and HMAC-chained audit traces.
```

### Initial release title

```text
LLMesh v0.1.0: Security-first local LLM swarm over MCP
```

### Initial release notes

```markdown
# LLMesh v0.1.0

LLMesh is a security-first peer-to-peer mesh for collaborative local LLM workflows over MCP.

This first release is a research/PoC baseline for trusted LAN or single-operator multi-PC setups. It is not intended for open Internet deployment against arbitrary peers yet.

## Highlights

- Ed25519 node identity with stable `peer:` IDs and `did:llmesh:1:` identifiers
- Signed capability manifests with TTL
- Signed rendezvous announcements and TOFU peer onboarding
- Request-level signatures bound to `METHOD`, `PATH`, `NODE_ID`, timestamp, and `BODY_SHA256`
- Fail-closed Prompt Firewall for L0-L4 classification
- OutputValidator 7-stage gate
- Server-side nonce replay protection
- OSV-backed SCA Gate for dependency risk checks
- HMAC-chained append-only AuditTrace
- 5-node Docker Compose PoC
- 526 passing tests

## Security status

- Critical findings: 0
- High findings: 0
- Forbidden unsafe patterns in source: 0

## Known limitations

- NonceStore is in-memory only.
- AuditTrace is process-local and needs file locking for multi-worker deployments.
- TrustedPeers needs size caps and TTL for gossip-added entries.
- This release is intended for trusted LAN and PoC environments.
```

## PyPI Publication

### Package name decision

Recommended distribution name:

```text
llmesh-mcp
```

Keep the Python import name:

```python
import llmesh
```

### Confirmed `pyproject.toml` setting for PyPI

```toml
name = "llmesh-mcp"
```

Optional metadata to fill after GitHub repository is created:

```toml
authors = [{ name = "Kazufumi Furuse" }]

[project.urls]
Homepage = "https://github.com/furuse-kazufumi/llmesh"
Repository = "https://github.com/furuse-kazufumi/llmesh"
Issues = "https://github.com/furuse-kazufumi/llmesh/issues"
```

### Build and upload commands

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
```

TestPyPI:

```bash
python -m twine upload --repository testpypi dist/*
```

PyPI:

```bash
python -m twine upload dist/*
```

### PyPI short description

```text
Security-first local LLM swarm over MCP.
```

### PyPI long description

Use `README.md` as the long description.

## Qiita Article Draft

### Title

```text
LLMesh: Local LLMをMCPで安全につなぐP2P Swarm PoCを作った
```

### Tags

```text
LLM
MCP
Python
Security
Ollama
```

### Body

```markdown
# LLMesh: Local LLMをMCPで安全につなぐP2P Swarm PoCを作った

Local LLMを複数台で協調させたい。しかし、秘密コードや社内ノウハウを外部ノードへ渡したくない。LLMeshはこの問題意識から作った、セキュリティファーストなLocal LLM SwarmのPoCです。

## 何を作ったか

LLMeshは、Ollamaやllama.cppで動くLocal LLMノードを、MCP風のHTTP tool interfaceでつなぎ、コード生成、テスト生成、コードレビュー、出力評価を分散実行するためのフレームワークです。

現在の実装は、信頼済みLANまたは単一オペレータの複数PC環境を対象にしています。公開インターネット上の任意ノードを信用して使う段階ではありません。

## セキュリティ設計

LLMeshでは、便利さより先にセキュリティ境界を設計しました。

- Ed25519によるNode IDとリクエスト署名
- `did:llmesh:1:` 形式の識別子
- TOFUによる初回ピア確認
- Prompt Firewallのfail-closed設計
- JSON SchemaベースのOutputValidator
- UUID v4 task_id検証
- nonce replay防御
- OSV APIを使ったSCA Gate
- HMAC chainのAuditTrace
- L3/L4データではprompt本文を保存しない監査ログ
- Docker Compose PoCでのcap_drop, read_only, tmpfs, no-new-privileges

## なぜ作ったか

Local LLMは守秘性の面で魅力的ですが、単体では能力や専門性に限界があります。一方で、複数ノードをつなぐと、今度はprompt leakage、悪意あるpatch、依存関係攻撃、replay、ノードなりすましが問題になります。

LLMeshは、Local LLM Swarmの実験を「安全側に倒す」前提で始めるための土台です。

## 現在の状態

- 526 tests passing
- Critical findings: 0
- High findings: 0
- 5-node Docker Compose PoCあり
- GitHub公開予定
- PyPI配布名は `llmesh-mcp` 予定

## 5-node PoC

```bash
pip install -e ".[dev]"
python -m pytest
docker compose -f docker-compose.poc.yml up --build
```

PoCでは、4つのworker nodeとorchestratorを起動します。

- generate_code
- generate_tests
- review_code
- critique_output
- orchestrator

## 今後

次に取り組む予定です。

- NonceStoreのSQLite永続化
- AuditTraceのfile lock対応
- TrustedPeersのサイズ上限とgossip TTL
- CapabilityManifest署名対象のschema-version-aware化
- L3+入力に対するFirewall → PrivacySummarizer → LLMBackendの強制パイプライン

LLMeshはまだ研究/PoC段階ですが、Local LLMを安全に協調させる実験基盤として育てていきます。
```

## LinkedIn Launch Post

### English

```text
I built LLMesh, a security-first local LLM swarm over MCP.

The idea is simple: local LLMs are great for privacy, but a single node is limited. If we connect multiple local LLM nodes, we can distribute coding workflows such as code generation, test generation, code review, and output critique.

The hard part is security.

LLMesh starts from a zero-trust design:

- Ed25519 node identity and request signatures
- Signed peer discovery and TOFU onboarding
- Fail-closed prompt firewall
- JSON-schema validated tool I/O
- UUID v4 task IDs and nonce replay protection
- OSV-backed dependency risk checks
- HMAC-chained audit traces
- No L3/L4 prompt bodies stored in audit logs
- 5-node Docker Compose PoC

Current status:

- 526 tests passing
- 0 Critical / 0 High findings in local review
- Research/PoC maturity
- Designed for trusted LAN or single-operator multi-PC setups, not arbitrary public Internet peers yet

This is part of my broader exploration of secure, local-first AI infrastructure.
```

### Japanese

```text
LLMeshという、Local LLMをMCPで安全につなぐSwarm PoCを作りました。

Local LLMは守秘性の面で魅力的ですが、単体ノードでは能力や専門性に限界があります。そこで、複数のLocal LLMノードをつなぎ、コード生成、テスト生成、コードレビュー、出力評価を分散実行する基盤を作っています。

LLMeshでは、便利さより先にセキュリティ境界を設計しました。

- Ed25519 Node IDとリクエスト署名
- TOFUによるピア確認
- fail-closed Prompt Firewall
- JSON SchemaベースのOutputValidator
- UUID v4 task_idとnonce replay防御
- OSVベースのSCA Gate
- HMAC chainのAuditTrace
- L3/L4 prompt本文を監査ログに残さない設計
- 5-node Docker Compose PoC

現在の状態:

- 526 tests passing
- Critical / High findings: 0
- 研究/PoC段階
- 信頼済みLANまたは単一オペレータの複数PC環境向け

今後は、NonceStore永続化、AuditTraceのfile lock対応、TrustedPeersのgossip制御などを進めていきます。
```

## Short Social Copy

```text
LLMesh is a security-first local LLM swarm over MCP.

It connects trusted local LLM nodes for distributed coding workflows, with signed peer discovery, fail-closed prompt filtering, schema-validated outputs, replay protection, SCA gating, and HMAC audit traces.
```

## Final Pre-Publish Checklist

- [x] Confirm GitHub repository owner: `furuse-kazufumi`
- [x] Confirm PyPI distribution name: `llmesh-mcp`
- [x] Change `pyproject.toml` package name to `llmesh-mcp`
- [x] Add GitHub URL to `pyproject.toml`
- [x] Run `python -m pytest`
- [x] Run `python -m build`
- [x] Run `python -m twine check dist/*`
- [x] Add GitHub Actions Trusted Publishing workflow for PyPI
- [x] Publish GitHub repository
- [x] Create GitHub release v0.1.0
- [ ] Configure PyPI Trusted Publisher for `llmesh-mcp`
- [ ] Publish to PyPI via GitHub Actions
- [x] Publish Qiita article: https://qiita.com/furuse-kazufumi/items/ac398349ec42e40913f1
- [ ] Publish LinkedIn post
