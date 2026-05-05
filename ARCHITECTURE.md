# LLMesh — アーキテクチャ概要

**Secure Local LLM Swarm over MCP**  
ローカルLLM（Ollama）ノード群をMCPプロトコルで接続し、コード生成・レビュー・テスト生成を分散実行するセキュアなスウォームフレームワーク。

---

## 全体構成

```
┌─────────────────────────────────────────────────────────────────────┐
│  Orchestrator (LocalSynthesizer + FanoutExecutor)                   │
│    │                                                                 │
│    ├─── k-of-n 並列ファンアウト ───────────────────────────────────── │
│    │                                                                 │
│    ▼           ▼           ▼           ▼                            │
│  node-a      node-b      node-c      node-d      (内部ネットワーク)  │
│ generate_   generate_  review_     critique_                        │
│  code        tests      code        output                          │
│    │           │          │           │                             │
│    └─── Ollama (llama3.2) ┘           │                             │
│         LLM バックエンド                                             │
└─────────────────────────────────────────────────────────────────────┘
```

各ノードは **FastAPI + Ollama** で動作し、すべてのノード間通信は **OutputValidator** を通過してから処理される。

---

## モジュール構成

```
llmesh/
├── classifier/
│   └── data_level.py          # DataLevel (L0〜L4) + ClassifiedPayload
├── privacy/
│   ├── firewall.py            # PromptFirewall Layer1/2（秘密情報検出）
│   └── summarizer.py          # PrivacySummarizer（L3→L1 抽象化）
├── identity/
│   ├── node_id.py             # Ed25519 鍵生成 + did:llmesh:1: 導出
│   ├── manifest.py            # CapabilityManifest（TTL付き署名）
│   ├── resolver.py            # DID Resolver（did:llmesh:1: → DIDDocument）
│   └── x25519.py             # Ed25519→X25519変換 + ECDH共有秘密
├── rendezvous/
│   ├── server.py              # FastAPI rendezvous（POST /announce, GET /lookup）
│   └── client.py             # announce() / lookup() クライアント（urllib）
├── mcp/
│   ├── schemas.py             # TOOL_SCHEMAS（4ツールのJSONスキーマ）
│   ├── validator.py           # OutputValidator（7段階ゲート）
│   ├── nonce_store.py         # NonceStore（TTL + リプレイ防御）
│   ├── sca_gate.py            # SCA Gate（OSV CVE チェック）
│   └── server.py              # FastAPI MCP サーバー
├── llm/
│   ├── backend.py             # LLMBackend ABC
│   ├── ollama.py              # OllamaBackend（llama3.2:latest）
│   └── prompt.py              # ToolPromptBuilder（4ツール用プロンプト）
├── orchestrator/
│   ├── synthesizer.py         # LocalSynthesizer（結果統合）
│   ├── node_client.py         # NodeClient（urllib MCP 呼び出し）
│   └── fanout.py              # FanoutExecutor（k-of-n 並列実行）
├── discovery/
│   ├── registry.py            # NodeRegistry（TTL/署名/サブネットフィルタ）
│   ├── client.py              # DiscoveryClient（register/discover/health）
│   └── router.py              # FastAPI /registry/* ルーター
├── challenge/
│   ├── bank.py                # ChallengeTaskBank（20問）
│   ├── evaluator.py           # ChallengeEvaluator（3軸スコアリング）
│   └── protocol.py            # ChallengeProtocol（HMAC/TTL/リプレイ防止）
└── audit/
    └── trace.py               # AuditTrace（HMAC チェーン JSONL）
```

---

## データフロー

```
クライアントリクエスト
  │
  ▼
PromptFirewall (Layer1: 正規表現秘密検出 / Layer2: 構造チェック)
  │  BLOCK → 即時拒否
  ▼
ClassifiedPayload (DataLevel L0〜L4 ラッピング)
  │
  ▼
FanoutExecutor (ThreadPoolExecutor で n ノードへ並列送信)
  │
  ├──▶ node-a: POST /tools/generate_code  ─┐
  ├──▶ node-b: POST /tools/generate_tests  │  各ノード内:
  ├──▶ node-c: POST /tools/review_code     │  Ollama LLM → レスポンス生成
  └──▶ node-d: POST /tools/critique_output─┘
  │
  ▼ (k個の応答が集まったら)
OutputValidator (7段階ゲート) ← 各応答に適用
  │
  ▼
LocalSynthesizer (コンセンサス統合)
  │
  ▼
AuditTrace (HMAC チェーン記録)
  │
  ▼
クライアントへ返却
```

---

## OutputValidator — 7段階ゲート

すべてのノードレスポンスはこの順序で検証される。どこかで失敗すると `ValidationError` を送出し、**フェイルクローズ**で処理を中断する。

| Step | チェック内容 | エラーコード例 |
|------|-------------|---------------|
| 1 | サイズガード（512 KB 上限） | `output_too_large` |
| 2 | JSON のみパース（YAML/pickle 禁止） | `json_parse_error` |
| 3 | JSONSchema 検証（tool スキーマ照合） | `schema_violation` |
| 4 | Nonce エコーチェック | `nonce_mismatch` |
| 5 | task_id UUID v4 検証 | `invalid_uuid4` |
| 6 | サーバーサイドリプレイ防御（NonceStore） | `replay_attack_detected` |
| 7 | SCA Gate（OSV CVE チェック） | `sca_blocked`, `sca_network_error` |

### SCA Gate（Step 7）詳細

`dependencies_added` フィールドに値がある場合のみ発動する。

- **エコシステム解決:** `language` フィールドから決定（`generate_code`）。
  `language` がない場合は `test_framework` 名から推論（`generate_tests`）。
- **対応言語:** Python→PyPI / TypeScript→npm / Go→Go / Rust→crates.io / Java→Maven
- **スキップ:** C/C++（OSV エコシステム未対応）
- **ブロック閾値:** CRITICAL または HIGH の CVE が1件でもあれば即時ブロック
- **ネットワーク障害:** フェイルクローズ（`sca_network_error`）

---

## セキュリティ設計原則

| 原則 | 実装箇所 |
|------|---------|
| フェイルクローズ | `OutputValidator`, `PromptFirewall`, `SCA Gate` |
| 秘密情報の L3-L4 保存禁止 | `AuditTrace`（プロンプト本文は除外） |
| shell=True 禁止 | 全モジュール（CI/banditで検出） |
| pickle/eval/exec 禁止 | 全モジュール（CI/banditで検出） |
| Ed25519 ノード認証 | `identity/node_id.py`, `identity/manifest.py` |
| リプレイ攻撃防御 | `NonceStore`（TTL付き） |
| コンテナ権限最小化 | `docker-compose.poc.yml`（cap_drop ALL, read_only, tmpfs） |

---

## ツールスキーマ一覧

| ツール名 | 用途 | 主要フィールド |
|---------|------|--------------|
| `generate_code` | コード生成 | `code`, `language`, `dependencies_added`, `cve_scan_requested` |
| `generate_tests` | テスト生成 | `tests_code`, `test_framework`, `dependencies_added` |
| `review_code` | コードレビュー | `findings[]`（severity/cwe_id/description/recommendation） |
| `critique_output` | 出力評価 | `scores`（correctness/security/testability/maintainability/overall） |

全ツールに共通: `task_id`（UUID v4）, `caller_nonce_echo`（hex32）

---

## データ機密レベル（DataLevel）

| レベル | ラベル | 用途例 | P2P 送信 |
|--------|--------|--------|---------|
| L0 | Public | OSSドキュメント、公開API仕様 | 可 |
| L1 | Low-risk | 抽象的なエラー、一般設計アドバイス | 可 |
| L2 | Internal | 社内コード、未公開設計 | 信頼ノードのみ |
| L3 | Confidential | 顧客情報、独自アルゴリズム | 禁止 |
| L4 | Regulated/Secret | PII、契約書、輸出規制対象 | 禁止 |

`PrivacySummarizer` は L3 データを L1 抽象表現に変換してからノードへ送出する。

---

## テスト構成

```
tests/
├── test_data_level.py      # DataLevel / ClassifiedPayload
├── test_firewall.py        # PromptFirewall Layer1/2
├── test_identity.py        # NodeIdentity (Ed25519 + did:llmesh:1:)
├── test_did_resolver.py    # DID Resolver (did:llmesh:1:)
├── test_x25519.py          # Ed25519→X25519変換 + ECDH
├── test_rendezvous.py      # Rendezvous server/client
├── test_encrypted_announce.py  # 署名付きアナウンス + AES-256-GCM
├── test_nonce_store.py     # NonceStore（TTL/リプレイ）
├── test_mcp_validator.py   # OutputValidator（steps 1〜6）
├── test_sca_gate.py        # SCA Gate + OutputValidator step 7（32件）
├── test_server_llm.py      # MCP サーバー統合
├── test_audit.py           # AuditTrace
├── test_challenge.py       # Challenge Protocol
├── test_discovery.py       # NodeRegistry / DiscoveryClient
├── test_fanout.py          # FanoutExecutor
├── test_synthesizer.py     # LocalSynthesizer
├── test_summarizer.py      # PrivacySummarizer
├── test_llm_backend.py     # LLM Backend ABC
└── e2e/
    ├── test_public_safe_task.py   # L0タスクの正常フロー
    └── test_secret_code_blocked.py # L3タスクのブロック確認
```

**合計: 526テスト、0失敗**
