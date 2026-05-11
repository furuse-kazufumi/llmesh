# LLMesh

**Secure LLM Mesh over MCP** — **v3.1.0**

> **Family / 同系プロジェクト:** バックエンド (本リポ) / TUI ダッシュボード →
> **[llove](https://github.com/furuse-kazufumi/llove)** / 一括インストール →
> `pip install llmesh-suite` (準備中)。
>
> **不混同 (Disambiguation):** プロトコル横断・産業 IoT・プライバシーパイプライン
> 主体の本プロジェクトは、GPU を持ち寄り **1 つの LLM を分散推論** する
> [`mesh-llm`](https://github.com/michaelneale/mesh-llm) とは別物です。
> 本プロジェクトは「I/O・プロトコル・産業現場」側、`mesh-llm` は「推論並列化」側。

産業 IoT、重要インフラ、先端 AI/量子、RTOS マイコンまでを統一フレームワークで
カバーする Python 統合プラットフォーム。
**ローカル LLM**（Ollama / llama.cpp）と**クラウド LLM**（OpenAI / Azure /
Anthropic / OpenRouter / Groq / Together / Mistral / DeepSeek）を同一 ABC で
透過運用。117 章 / 500+ 要件項目、Rust 拡張で性能 6×、2300+ テスト全 PASS。
**全体 OWASP 静的監査クリーン**（`shell=True` / `pickle` / `eval` / SQL 注入 /
弱暗号 ゼロ、全 HTTP クライアントにレスポンスサイズ上限）、
**v3.0.0 から SemVer 正式適用**（`docs/API_STABILITY.md` の公開シンボル一覧が契約）。

## Quick Start

```bash
# PyPI からのインストール（配布名は llmesh-mcp、import 名は llmesh）
pip install llmesh-mcp
# 産業用フル機能
pip install "llmesh-mcp[industrial,vision,presidio,rag]"

# ローカル開発
git clone git@github.com:furuse-kazufumi/llmesh.git
cd llmesh
pip install -e ".[dev,industrial]"
pytest
```

```python
# import パスは llmesh のまま
from llmesh import PromptFirewall, SensorEvent
from llmesh.rag import Retriever, MockEmbedder, NumpyVectorStore
from llmesh.llm import openai_backend, OllamaBackend
```

主要 optional extras:

```bash
pip install -e ".[industrial]"   # MTEngine / SPC / Modbus / OPC-UA / MQTT
pip install -e ".[vision]"       # ImageFirewall / VLM 経路
pip install -e ".[presidio]"     # Layer 1.5 PII 検出（v2.13.0+）
pip install -e ".[rag]"          # RAG（ベクトル検索）（v2.13.0+）
pip install -e ".[email,udp,ssh,ftp,mgmt,can,bacnet]"
```

## Components

### Core

| Module | Description |
|---|---|
| `llmesh.classifier` | DataLevel (L0–L4) + ClassifiedPayload |
| `llmesh.privacy.firewall` | 4-layer Prompt Firewall（L0 注入検出 / L1 シークレット / **L1.5 Presidio PII** / L2 構造） |
| `llmesh.privacy.summarizer` | PrivacySummarizer（L3 要約パイプライン） |
| `llmesh.privacy.image_firewall` | ImageFirewall（L4 画像 BLOCK / L3 サマリ） |
| `llmesh.privacy.presidio_detector` | **NEW v2.13** Microsoft Presidio 統合（PII 検出、optional） |
| `llmesh.identity` | Ed25519 Node ID + `did:llmesh:1:` + Capability Manifest + X25519 ECDH |
| `llmesh.rendezvous` | 署名付きノード発見サービス（plaintext / AES-256-GCM） |
| `llmesh.mcp` | MCP tool schemas + OutputValidator |
| `llmesh.audit` | tamper-evident HMAC chain audit log |
| `llmesh.fairness` | フリーライダー防止（ServiceReceipt / ContributionLedger / FairnessPolicy） |

### RAG (v2.13.0+)

| Module | Description |
|---|---|
| `llmesh.rag.embedder` | Embedder ABC + MockEmbedder（決定論ハッシュ）+ OllamaEmbedder（urllib のみ） |
| `llmesh.rag.store` | VectorStore ABC + Document / RetrievedDocument |
| `llmesh.rag.numpy_store` | 純 numpy in-memory ストア（cosine 類似度、`.npz` アトミック永続化） |
| `llmesh.rag.sqlite_store` | **NEW v2.14** 純 sqlite3 永続ストア（WAL、UPSERT、≤10⁶ 件で実用） |
| `llmesh.rag.lsh_store` | **NEW v2.15** LSH ANN ベクトルストア（≥10⁶ 件、recall@10 ≥ 0.92） |
| `llmesh.rag.retriever` | Embedder + VectorStore + Firewall を結合した Retriever |

### Protocols (multi-protocol gateway)

| Module | Description |
|---|---|
| `llmesh.protocol.{http,tcp,udp,ssh,sftp,smtp,imap,pop3,ftp,snmp,telnet,ros2,ros1}_adapter` | プロトコルアダプタ群 |
| `llmesh.protocol.adapter` | ProtocolAdapter ABC + TransportError |
| `llmesh.protocol.registry` | AdapterRegistry（名前生成・カスタム登録・entry-points） |
| `llmesh.protocol.message` | UnifiedMessage + MessageType（STREAM_ACK / RETRANSMIT 含む） |
| `llmesh.protocol.assembler` | MessageAssembler（順序組立 + タイムアウト + ウォッチドッグ） |
| `llmesh.protocol.chunk_sender` | ChunkSender（送信バッファ + 再送 + ACK + TTL expire） |
| `llmesh.protocol.watchdog` | WatchdogTimer |
| `llmesh.security.clock` | NTPクロック同期チェック |
| `llmesh.discovery.dns_sd` | DNS-SD v2 mDNS アナウンサー |

### Industrial (v1.3.0+)

| Module | Description |
|---|---|
| `llmesh.industrial.sensor_event` | SensorEvent 統一モデル |
| `llmesh.industrial.{modbus,serial,opcua,mqtt,ethercat,can,bacnet,websocket}_adapter` | 産業プロトコル |
| `llmesh.industrial.mt_engine` | Mahalanobis-Taguchi 法（オフライン訓練 + リアルタイム推論） |
| `llmesh.industrial.mt_online` | **NEW v2.13** OnlineMTEngine（バッチ Mahalanobis、einsum、メモリ上限制御） |
| `llmesh.industrial.spc_engine` | XbarRChart / CUSUMChart（Shewhart / 累積和管理図） |
| `llmesh.industrial.hotelling_t2` | **NEW v2.13** HotellingT2Chart（多変量管理図、Tikhonov 正則化） |
| `llmesh.industrial.event_density_map` | **NEW v2.13** EventDensityMap（DVS イベント → グリッド特徴） |
| `llmesh.industrial.multimodal_spc` | **NEW v2.13** UnifiedSPC（センサー × VLM テキスト 2 系統 SPC） |
| `llmesh.industrial.explainer` | **NEW v2.13** LLMExplainer（SCADA 異常 → Markdown/JSON レポート） |
| `llmesh.industrial.explained_cusum` | **NEW v2.14** ExplainedCUSUM（自己説明 CUSUM、CUSUMChart + LLMExplainer 統合） |
| `llmesh.industrial.video_cusum` | **NEW v2.14** VideoCUSUM（動画 + センサー時刻同期 CUSUM、ペア化バッファ） |
| `llmesh.industrial.vlm_feature_extractor` | **NEW v2.14** VLMFeatureExtractor（画像 → ImageFirewall → caption → 数値ベクトル） |
| `llmesh.industrial.dnp3_adapter` | **NEW v2.14** DNP3Adapter（v3-N7 / K-1.1、SCADA outstation client、pydnp3 optional） |
| `llmesh.industrial.goose_adapter` | **NEW v2.14** GOOSEAdapter（IEC 61850 GOOSE subscriber、リプレイ防御付き） |
| `llmesh.industrial.sensor_3d` | AOI / 深度 / DVS（mcp-3d SDK 互換） |
| `llmesh.industrial.c_abi` | RTOS C ABI / EdgeProfile（Volume L） |
| `llmesh.industrial.metrics` / `tracing` / `tenant` | OpenTelemetry / マルチテナント |

## Reliability Protocol

ストリーミング通信の信頼性を `MessageAssembler` と `ChunkSender` の
組み合わせで保証する。

```
[正常完了]  受信: pop_completed() → STREAM_ACK 送信
            送信: handle_ack()    → 送信バッファ破棄

[欠落検出]  受信: check_timeouts() → RETRANSMIT 送信（1 回のみ）
            送信: handle_retransmit() → 欠落チャンクのみ再送

[切断検出]  受信: check_watchdog()  → True で切断シグナル
            送信: expire_old()      → TTL 超過バッファ自動破棄
```

## Privacy Pipeline

```
prompt → PromptFirewall (L0/L1/L1.5/L2) → PrivacySummarizer →
         LLM Backend (Ollama / LlamaCpp) → OutputValidator → caller
```

| Layer | 役割 | 出力 |
|------:|------|------|
| L0 | プロンプト注入 / jailbreak / Unicode 制御文字 | BLOCK |
| L1 | シークレット（API キー、JWT、PEM、AWS、GitHub、Anthropic、OpenAI） | BLOCK |
| **L1.5** | **Presidio PII（CC / SSN / IBAN / 医療免許 / 個人名 / Email / 電話 …）** | **BLOCK or SUMMARIZE** |
| L2 | 絶対パス / 内部 import / オーバーサイズ payload | SUMMARIZE or BLOCK |

## Security Guardrails

- `shell=True`, `pickle`, `yaml.load(unsafe)`, `eval`, `exec` を一切使わない
- Firewall は **fail-closed**（例外 → L4/BLOCK）
- OutputValidator が non-JSON / schema 不一致 / nonce replay を拒否
- subprocess 呼び出しは list 形式のみ
- すべての optional 依存は extras（軽量本体）

## Performance

| 操作 | Pure Python | Rust | 倍率 |
|------|-----------:|-----:|----:|
| PointCloud encode (1M) | 4.0M pts/s | **24.1M pts/s** | **6.0×** |
| PointCloud decode (1M) | 3.7M pts/s | 5.9M pts/s | 1.6× |
| DVS encode (1M) | 3.4M evt/s | 5.5M evt/s | 1.6× |
| Pipeline + CUSUM | 190K events/s | – | – |

## CLI

```bash
python -m llmesh.cli.doctor   # 環境健全性チェック
python -m llmesh.cli.status   # ランタイム状態
python -m llmesh.cli.sbom     # CycloneDX SBOM 自動生成
```

## Documentation

### 概要 / 計画
- `docs/ROADMAP.md` — リリース計画 / バージョン履歴
- `docs/REQUIREMENTS.md` — 117 章 / 500+ 要件、Volume A〜N
- `docs/CHANGELOG.md` — 詳細変更履歴

### アーキテクチャ / 仕様
- `docs/ARCHITECTURE.md` — アーキテクチャ
- `docs/SPECIFICATION.md` — API 仕様
- `docs/SECURITY.md` — STRIDE 脅威モデル + セキュリティ不変条件
- `docs/API_STABILITY.md` — Public/Internal API 境界 + SemVer ポリシー（v2.15+）
- `docs/PERFORMANCE.md` — モジュール別計算量 + メモリ + 推奨パラメータ（v2.15+）

### 利用ガイド
- `docs/SETUP.md` / `docs/SETUP_GUIDE.md` — セットアップ
- `docs/USAGE.md` — 使用例（v2.13/2.14 強化機能セクション含む）
- `docs/INDUSTRIAL_GUIDE.md` — 産業 IoT 利用ガイド（Phase A〜v3 含む）
- `docs/PEERING.md` / `docs/PLATFORMS.md` — ピアリング / プラットフォーム

### 運用 / トラブルシューティング（v2.18+）
- `docs/DEPLOYMENT.md` — Docker / systemd / k8s / シークレット管理
- `docs/OBSERVABILITY.md` — Prometheus / OTel / AuditTrace / SLO
- `docs/TROUBLESHOOTING.md` — エラー対処集 + FAQ
- `docs/MIGRATION.md` — バージョン間移行ガイド

### 開発者向け（v2.18+）
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — 貢献ガイド（コミット規約 / PR チェックリスト）
- `docs/DEVELOPMENT.md` — 開発環境 / 内部構造 / 新規モジュール追加手順
- `docs/TESTING.md` — テスト戦略 / Hypothesis / カバレッジ目標
- `docs/GLOSSARY.md` — 用語集（LLM / セキュリティ / 産業）
- `docs/SETUP.md` / `docs/SETUP_GUIDE.md` — セットアップ
- `docs/USAGE.md` — 使用例
- `docs/PEERING.md` / `docs/PLATFORMS.md` — ピアリング / プラットフォーム
- `docs/papers/` — RAD（21 分野コーパス）+ 論文素材
