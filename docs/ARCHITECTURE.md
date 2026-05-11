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
├── core/                       # v3.2+ Research-orchestration primitives (Phase 0a/0b)
│   ├── agent.py                # Agent ABC + AgentConfig (frozen) — typed I/O contract
│   ├── tool.py                 # Tool ABC + ToolSpec — semantic call boundary
│   ├── task.py                 # TaskGraph + TaskNode + topo_order (Kahn)
│   ├── trace.py                # TraceEntry + write_trace_jsonl (append-only JSONL)
│   └── trace_logger.py         # TraceLogger — run.start/end + prompt/tool/agent/eval helpers, threadsafe
├── classifier/
│   └── data_level.py          # DataLevel (L0〜L4) + ClassifiedPayload
├── privacy/
│   ├── firewall.py            # PromptFirewall Layer 0/1/1.5/2（注入・秘密情報・PII・構造）
│   ├── summarizer.py          # PrivacySummarizer（L3→L1 抽象化）
│   ├── presidio_detector.py   # Microsoft Presidio Layer 1.5（PII 検出、optional）— v2.13+
│   ├── image_firewall.py      # ImageFirewall（L4 画像 BLOCK / L3 サマリ）— v1.2+
│   └── image_summarizer.py    # ImageSummarizer（Vision LLM 経路）— v1.2+
├── rag/                       # v2.13+ Retrieval-Augmented Generation
│   ├── embedder.py            # Embedder ABC + MockEmbedder + OllamaEmbedder（urllib のみ）
│   ├── store.py               # VectorStore ABC + Document / RetrievedDocument
│   ├── numpy_store.py         # NumpyVectorStore（cosine、.npz アトミック永続化）
│   └── retriever.py           # Retriever（Embedder + VectorStore + PromptFirewall 統合）
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
│   ├── node_client.py         # NodeClient（HTTP/TCP/UDP マルチプロトコル MCP 呼び出し）
│   └── fanout.py              # FanoutExecutor（k-of-n 並列実行、protocol= で切替）
├── discovery/
│   ├── registry.py            # NodeRegistry（TTL/署名/サブネットフィルタ）
│   ├── client.py              # DiscoveryClient（register/discover/health）
│   └── router.py              # FastAPI /registry/* ルーター
├── challenge/
│   ├── bank.py                # ChallengeTaskBank（20問）
│   ├── evaluator.py           # ChallengeEvaluator（3軸スコアリング）
│   └── protocol.py            # ChallengeProtocol（HMAC/TTL/リプレイ防止）
├── audit/
│   └── trace.py               # AuditTrace（HMAC チェーン JSONL）
├── protocol/                  # v0.4.0+ — マルチプロトコル抽象化層
│   ├── adapter.py             # ProtocolAdapter ABC + TransportError
│   ├── registry.py            # AdapterRegistry（名前→アダプタ生成・カスタム登録）
│   ├── message.py             # UnifiedMessage + MessageType + NodeAddress
│   ├── http_adapter.py        # HTTPAdapter（FastAPI /msg + urllib クライアント）
│   ├── tcp_adapter.py         # TCPAdapter（asyncio + 4バイト長プレフィクス、接続都度）
│   ├── tcp_stream_adapter.py  # TCPStreamAdapter（永続接続 + 双方向 ReliableStream）
│   ├── udp_adapter.py         # UDPAdapter（asyncio datagram + 8バイトヘッダ）
│   ├── ssh_adapter.py         # SSHAdapter（paramiko Ed25519認証、v0.5.0）
│   ├── sftp_adapter.py        # SFTPAdapter（ファイルベース転送、v0.5.0）
│   ├── smtp_adapter.py        # SMTPAdapter（aiosmtpd、v0.6.0）
│   ├── imap_adapter.py        # IMAPAdapter（imaplib ポーリング、v0.6.0）
│   ├── pop3_adapter.py        # POP3Adapter（poplib、v0.6.0）
│   ├── ftp_adapter.py         # FTPAdapter（pyftpdlib FTPS、v0.7.0）
│   ├── snmp_adapter.py        # SNMPAdapter（pysnmp SNMPv3読み取り専用、v0.8.0）
│   ├── assembler.py           # MessageAssembler（順序組み立て + タイムアウト + ウォッチドッグ）
│   ├── chunk_sender.py        # ChunkSender（送信バッファ + 再送 + ACK処理 + TTL expire）
│   ├── watchdog.py            # WatchdogTimer（kick / is_expired / remaining / idle_s）
│   └── reliable_stream.py     # ReliableStream（bytes/dict/str 自動チャンク + ACK/RETRANSMIT）
├── security/                  # セキュリティユーティリティ
│   ├── rate_limiter.py        # PerNodeRateLimiter（トークンバケット）
│   ├── endpoint_validator.py  # EndpointValidator
│   └── clock.py               # NTPクロック同期（ClockDriftError、v0.8.0）
├── discovery/                 # ノード発見
│   ├── registry.py            # NodeRegistry（TTL/署名/サブネットフィルタ）
│   ├── client.py              # DiscoveryClient
│   ├── router.py              # FastAPI /registry/* ルーター
│   └── dns_sd.py              # DnsSdAnnouncer（mDNS DNS-SD v2、v0.8.0）
└── fairness/                  # フェアネスシステム（v0.5.x）
    ├── receipt.py             # ServiceReceipt（受益者Ed25519署名）
    ├── ledger.py              # ContributionLedger（HMACチェーン + 時間窓）
    ├── policy.py              # FairnessPolicy（閾値・ペナルティ）
    └── witness.py             # WitnessProtocol（共謀対策ランダムサンプリング）
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

## TCPStreamAdapter — 永続接続 + ReliableStream 統合（v0.4.0）

`TCPAdapter`（接続都度）の上位互換。大きなペイロードや高頻度呼び出しに適する。

```python
# NodeClient / FanoutExecutor で切り替えるだけ
NodeClient(protocol="tcp_stream")
FanoutExecutor(k=2, protocol="tcp_stream")
```

| 特性 | TCPAdapter (`"tcp"`) | TCPStreamAdapter (`"tcp_stream"`) |
|------|---------------------|-----------------------------------|
| 接続 | リクエスト毎に新規 | (host, port) ごとに永続プール |
| ペイロードサイズ | 単一フレームに収まる範囲 | 無制限（ReliableStream で自動チャンク） |
| 再接続 | N/A（毎回新規） | 自動（切断検出 → 再接続） |
| 同時リクエスト数 | 制限なし（接続独立） | 接続あたり 1 件（asyncio.Lock） |
| ACK / RETRANSMIT | なし | あり（ReliableStream） |

### 内部フロー

```
[クライアント]                          [サーバー]
send(message.to_dict())
  │ ReliableStream.send()
  │   ├─ chunk-0 ──────────────────────▶ _handle_connection()
  │   └─ chunk-N (STREAM_END) ─────────▶   │ stream.on_message()
  │                                         │   └─ 全チャンク受信 → handler(request_msg)
  │◀── STREAM_ACK ──────────────────────── │   stream.send(response.to_dict())
  │◀── resp-chunk-0 ────────────────────── │     ├─ resp-chunk-0
  │◀── resp-chunk-M (STREAM_END) ───────── │     └─ resp-chunk-M
  │ stream.on_message()                     │
  │   └─ 全チャンク受信 → return response   │
  │──── STREAM_ACK ─────────────────────▶ │ stream.on_message() → buffer cleared
```

---

## 信頼性プロトコル（v0.3.0）

ストリーミング通信の信頼性を `MessageAssembler`（受信側）と `ChunkSender`（送信側）の組み合わせで保証する。

```
[正常完了]
  受信: pop_completed()      → STREAM_ACK 送信
  送信: handle_ack(msg)      → 送信バッファ破棄

[欠落検出]
  受信: check_timeouts()     → RETRANSMIT 送信（ストリームにつき1回のみ）
  送信: handle_retransmit()  → 欠落チャンクのみ再送

[切断検出]
  受信: check_watchdog()     → True で切断シグナル（バッファ破棄は呼び出し元責務）
  送信: expire_old()         → TTL 超過した送信バッファを自動破棄
```

### プロトコル実装メモ

| 項目 | 詳細 |
|------|------|
| UDPヘッダ | magic `b"\x4c\x4d"` + seq(2B) + reserved(4B) = 8バイト固定 |
| TCPフレーム | `_pack_frame(data)` — 4バイト長プレフィクス付きバイト列 |
| PEP 563 回避 | `from __future__ import annotations` + FastAPIエンドポイント型は `__annotations__` で実行時注入 |
| RETRANSMIT 再発火防止 | `retransmit_sent=True` フラグでタイムアウト後の多重送信を防止 |
| ウォッチドッグ | `MessageAssembler(watchdog_timeout_s=60.0)` — `push()` で自動 kick |

---

## ReliableStream — 高レベル送受信 API（v0.3.1）

`ChunkSender` + `MessageAssembler` を組み合わせた高レベル API。任意のデータを透過的に送受信できる。

```python
stream = ReliableStream(sender=my_addr)

# 送信（bytes / dict / str すべて対応）
stream_id = await stream.send(b"\xff\xfe...", target=peer, adapter=tcp)

# 受信（完全に組み立てられたペイロードのみ返す）
for payload in await stream.on_message(incoming_msg, adapter=adapter):
    handle(payload)   # bytes | dict | str

# 定期メンテナンス（RETRANSMIT + TTL 破棄）
await stream.tick(adapter=adapter)
```

| 機能 | 詳細 |
|------|------|
| バイナリ対応 | `bytes` → base64エンコードして `payload["_chunk"]` に格納 |
| 自動チャンク分割 | `chunk_size=256KB`（デフォルト）— 512KB OutputValidator 上限を下回る設計 |
| dict / str 対応 | JSON シリアライズ後にバイナリと同じパイプラインを通す |
| ACK 自動送信 | 完了ストリームで即座に `STREAM_ACK` を返送 |
| RETRANSMIT | `tick()` が stall ストリームを検出して再送要求 |
| ウォッチドッグ | `is_peer_silent()` でピアの無音を検出 |

---

## マルチプロトコル NodeClient / FanoutExecutor（v0.3.1）

`NodeClient` と `FanoutExecutor` に `protocol` パラメータを追加。デフォルト `"http"` で完全後方互換。

```python
# 従来通り（変更不要）
FanoutExecutor(k=2).execute(tool_name, body, nodes)

# TCP ファンアウトに切り替え（endpoint は "host:port" 形式）
FanoutExecutor(k=2, protocol="tcp").execute(tool_name, body, nodes)

# UDP ファンアウト
FanoutExecutor(k=1, protocol="udp").execute(tool_name, body, nodes)
```

| protocol | トランスポート | endpoint 形式 | 備考 |
|----------|--------------|--------------|------|
| `"http"` (デフォルト) | urllib HTTP/HTTPS | `"https://host:port"` | 既存コード完全互換 |
| `"tcp"` | TCPAdapter (asyncio) | `"host:port"` | `UnifiedMessage` ベース |
| `"udp"` | UDPAdapter (asyncio) | `"host:port"` | `UnifiedMessage` ベース |
| カスタム | `AdapterRegistry.register()` で登録 | 任意 | gRPC 等を後付け可能 |

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
├── test_data_level.py           # DataLevel / ClassifiedPayload
├── test_firewall.py             # PromptFirewall Layer1/2
├── test_identity.py             # NodeIdentity (Ed25519 + did:llmesh:1:)
├── test_did_resolver.py         # DID Resolver (did:llmesh:1:)
├── test_x25519.py               # Ed25519→X25519変換 + ECDH
├── test_rendezvous.py           # Rendezvous server/client
├── test_encrypted_announce.py   # 署名付きアナウンス + AES-256-GCM
├── test_nonce_store.py          # NonceStore（TTL/リプレイ）
├── test_mcp_validator.py        # OutputValidator（steps 1〜6）
├── test_sca_gate.py             # SCA Gate + OutputValidator step 7（32件）
├── test_server_llm.py           # MCP サーバー統合
├── test_audit.py                # AuditTrace
├── test_challenge.py            # Challenge Protocol
├── test_discovery.py            # NodeRegistry / DiscoveryClient
├── test_fanout.py               # FanoutExecutor
├── test_synthesizer.py          # LocalSynthesizer
├── test_summarizer.py           # PrivacySummarizer
├── test_llm_backend.py          # LLM Backend ABC
├── test_protocol_message.py     # UnifiedMessage / MessageType / NodeAddress
├── test_protocol_adapter.py     # ProtocolAdapter ABC / TransportError
├── test_protocol_http.py        # HTTPAdapter
├── test_protocol_tcp.py         # TCPAdapter（フレーム送受信）
├── test_protocol_udp.py         # UDPAdapter（datagram + ヘッダ）
├── test_protocol_assembler.py   # MessageAssembler（順序/タイムアウト/ウォッチドッグ）
├── test_protocol_reliability.py # ChunkSender + Assembler 協調（ACK/RETRANSMIT）
├── test_protocol_watchdog.py    # WatchdogTimer
└── e2e/
    ├── test_public_safe_task.py        # L0タスクの正常フロー
    └── test_secret_code_blocked.py     # L3タスクのブロック確認
```

**合計: 744テスト、0失敗**
