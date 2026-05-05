# LLMesh

**Secure Local LLM Swarm over MCP** は、Local LLM同士を安全に協調させるための、セキュリティファーストなP2PメッシュPoCです。

LLMeshは、Ollamaやllama.cppで動作する複数のLocal LLMノードを、署名付きMCP風HTTP tool interfaceで接続します。コード生成、テスト生成、コードレビュー、出力評価などの作業を分散実行しながら、fail-closedなPrompt Firewall、JSON Schemaベースの入出力検証、HMAC chain監査ログ、依存関係リスク検査を最初から組み込みます。

> **状態:** 526 tests passing / 0 failures / Critical 0 / High 0。
> **成熟度:** research / PoC段階です。信頼済みLAN、または単一オペレータの複数PC環境を対象にしています。任意の公開インターネットノードを信用して接続する用途には、まだ十分にハードニングされていません。

## なぜLLMeshか

Local LLMは守秘性の面で魅力的ですが、単体ノードでは能力や専門性に限界があります。一方、複数ノードをつなぐと、prompt leakage、悪意あるpatch、依存関係攻撃、replay、ノードなりすましといったリスクが増えます。

LLMeshは、Local LLM Swarmの実験を「安全側に倒す」前提で始めるための土台です。各ノード間の通信は署名され、ツール応答は7段階のOutputValidatorを通過し、秘密情報らしいpromptはfail-closedでブロックされ、許可された判断はHMAC chainで監査可能に記録されます。

## 主な特徴

- **Ed25519 Node Identity**: 安定した `peer:` IDと `did:llmesh:1:` 形式の識別子を提供します。
- **Signed Capability Manifest**: ノードのtool、subnet、policyをTTL付き署名manifestとして公開します。
- **TOFU + signed P2P discovery**: rendezvous serverとgossip pullにより、初回確認付きでピアを発見します。
- **Request-level auth**: `METHOD`, `PATH`, `NODE_ID`, timestamp, `BODY_SHA256` を署名対象にします。
- **Fail-closed Prompt Firewall**: guard componentの例外時にもBLOCKを返します。
- **OutputValidator 7-stage gate**: size cap、JSON parse、schema検証、nonce echo、UUIDv4 task_id、replay store、OSV SCA Gateを通します。
- **HMAC append-only AuditTrace**: L3/L4 prompt本文は保存せず、SHA-256のみを記録します。
- **Container hardening**: sandboxはnetwork none、cap drop、read-only、tmpfs noexec、no-new-privileges、non-root UIDを前提にします。

## インストール

PyPIから利用する場合:

```bash
pip install llmesh-mcp
```

開発用にリポジトリから利用する場合:

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

PoCでは、4つのworker nodeとorchestratorを起動します。

- generate_code
- generate_tests
- review_code
- critique_output
- orchestrator

## 公開リンク

- GitHub: <https://github.com/furuse-kazufumi/llmesh>
- PyPI: <https://pypi.org/project/llmesh-mcp/>
- Qiita: <https://qiita.com/furuse-kazufumi/items/ac398349ec42e40913f1>
- LinkedIn: <https://www.linkedin.com/feed/update/urn:li:share:7457372822668230657/>

## 今後の課題

- NonceStoreのSQLite永続化
- AuditTraceのfile lock対応
- TrustedPeersのサイズ上限とgossip TTL
- CapabilityManifest署名対象のschema-version-aware化
- L3+入力に対するFirewall → PrivacySummarizer → LLMBackendの強制パイプライン

## ライセンス

Apache License 2.0。

Copyright 2026 Kazufumi Furuse.
