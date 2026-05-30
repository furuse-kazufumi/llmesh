# Glossary

LLMesh に登場する用語集。LLM / セキュリティ / 産業用語を一元化します。

---

## llmesh とは（中学生レベルのかみ砕き説明）

llmesh は、会社や工場の中だけで動く「AI の交換機（こうかんき）」です。むかしの電話局の交換手が、かかってきた電話を正しい相手につないでいたように、llmesh は人や機械からの質問を受け取って、いちばん合った AI に取りつぎ、その答えを返します。

大事なのは、この交換機が「自分の建物の中」に置かれることです。個人情報や工場のデータを外のインターネットに出さずに AI を使えるので、秘密を守りやすいのが特徴です。さらに、工場のセンサーの数字をいつも見はっていて「いつもとちがう」変化を見つけたり（品質の見はり機能）、つくった部品のメーターと直接つないだりもできます。

たとえると、llmesh は「家の中だけで完結する、たくさんの AI をまとめる受付係＋見はり番」のようなものです。

用語集本体: 専門用語の意味は、このあとの A〜X 各セクションと末尾の「略語表」を参照してください。

---

## A

### Adapter（アダプタ）
プロトコル固有の framing と LLMesh 内部の `UnifiedMessage` を相互変換
するレイヤー。13 種以上の `ProtocolAdapter` 実装。

### Allow-list（許可リスト）
受け入れる接続元 / 識別子の明示的なリスト。LLMesh では DNP3
`allow_addresses`、GOOSE `allow_iedids`、CIDR の `accept_cidrs` 等で使用。

### ANN（Approximate Nearest Neighbor）
近似最近傍探索。`LSHVectorStore`（v2.15+）が実装。

### Audit chain
`AuditTrace` が生成する HMAC ベースの改ざん検知チェーン。各エントリは
前エントリのハッシュを含む。

### AOI（Automated Optical Inspection）
製造ラインの外観検査カメラ。`AoiAdapter` でサポート。

---

## B

### BACnet
建物設備管理の標準プロトコル（ASHRAE 135）。`BACnetAdapter` が
ASHRAE 135 BACnet/IP に対応。

### BLOCK / SUMMARIZE / ALLOW
`PromptFirewall` の3つの判定アクション:
- **BLOCK** — L4、リクエスト拒否
- **SUMMARIZE** — L3、`PrivacySummarizer` 経由で要約後通過
- **ALLOW** — L0/L1、そのまま通過

---

## C

### CapabilityManifest
ノードが提供する機能を署名付きで宣言するメタデータ。TTL 付き。

### CIDR allowlist
IP アドレス範囲（CIDR 表記）でクライアントを制限。`WebSocketAdapter`
等で使用。

### Circuit breaker
連続失敗時に呼び出しを遮断する回復力パターン。
`AdapterCircuitBreakerRegistry` が per-adapter で管理。

### CUSUM
Cumulative Sum chart。微小な平均シフトを累積和で検出する管理図。
`CUSUMChart` 実装。

### CVE
Common Vulnerabilities and Exposures。`SCA Gate` が OSV API 経由で
依存ライブラリの CVE を検出。

---

## D

### DataLevel
データの機密度ラベル L0–L4。
- L0: 公開可能
- L1: 内部用（一般的なログ等）
- L2: 制限あり（社外秘）
- L3: 機密（要約後のみ LLM 通過）
- L4: 規制対象（決して LLM 通過しない、PII / 秘密 / 攻撃ペイロード）

### DID（Decentralized Identifier）
LLMesh は `did:llmesh:1:` スキームで Ed25519 公開鍵から DID を導出。

### DNP3
電力・水道・ガス等の重要インフラ SCADA で標準的なプロトコル
（IEEE 1815）。`DNP3Adapter` がサポート。

### DNS-SD
DNS Service Discovery。zeroconf / mDNS で同一 LAN 内のノードを発見。

### DVS（Dynamic Vision Sensor）
イベントカメラ。1 µs オーダーで `(t, x, y, polarity)` を発行。
`EventCameraAdapter` + `EventDensityMap` でサポート。

---

## E

### EdgeProfile
RTOS / 組込み環境向けの軽量ランタイムプロファイル（Volume L）。

### Embedder
テキストを密ベクトルに変換するインターフェース。`MockEmbedder` /
`OllamaEmbedder` 実装。

### EtherCAT
産業用イーサネット規格。`EtherCATAdapter` が `pysoem` 経由でサポート
（Linux + CAP_NET_RAW 必要）。

---

## F

### Fail-closed / Fail-safe / Fail-open
- **Fail-closed**: 例外で安全側（拒否）にフォールバック。LLMesh 全体規約。
- **Fail-safe**: 失敗時に機能の一部だけ縮退。`LLMExplainer` の LLM
  失敗 → テンプレート復帰など。
- **Fail-open**: 例外で許可側にフォールバック → **LLMesh では禁止**。

### Federated learning
モデルを各拠点で学習し集約する手法。Volume N（Research Backlog）の
N-2 / N-12 で言及。

### Firewall layer
PromptFirewall の検査段階:
- Layer 0: prompt injection
- Layer 1: secret patterns
- Layer 1.5: Presidio PII（v2.13+）
- Layer 2: structural (path, oversize)

---

## G

### GOOSE
IEC 61850 の Generic Object Oriented Substation Event。電力設備の
低レイテンシメッセージング。`GOOSEAdapter` 実装。

### Gossip
peer-to-peer な情報伝播。`llmesh.discovery.gossip` がノード情報の
収束的伝播を担当。

---

## H

### HMAC
Hash-based MAC。`AuditTrace` のチェーン整合性、challenge protocol で
使用。

### Hotelling T²
多変量管理図。共分散行列ベース。`HotellingT2Chart`（v2.13+）。

### Hypothesis
プロパティベーステストフレームワーク。LLMesh で 1200+ ケース。

---

## I

### IED（Intelligent Electronic Device）
電力設備の保護リレーや制御装置。`GOOSEAdapter` が IED の `goCBRef` を
受信。

### Industrial Pipeline
`SensorEvent` → 解析エンジン群 → `DiagnosisResult` の統合パイプライン。

---

## L

### Lazy import
モジュール先頭ではなく、関数内で `import` を実行する手法。重い依存
（numpy / scipy / Pillow）を本体 import から切り離す。

### LSH（Locality-Sensitive Hashing）
近似最近傍探索アルゴリズム。`LSHVectorStore`（v2.15+）が
random-hyperplane LSH を実装。

---

## M

### Mahalanobis distance
共分散を考慮した多変量距離。`MTEngine` の異常スコア。

### Maturin
Rust 拡張のビルドツール。LLMesh の `rust_ext/` で使用。

### MCP（Model Context Protocol）
Claude Code が外部ツールを呼び出すためのプロトコル。LLMesh は
stdio MCP サーバーとして動作可能（`python -m llmesh serve-mcp`）。

### Modbus
産業用プロトコル。`ModbusAdapter` が TCP / RTU 両対応。

### MT-method（Mahalanobis-Taguchi）
正常データから「単位空間」を学習し、Mahalanobis 距離で異常検知する
品質工学手法。`MTEngine` 実装。

### Multimodal
複数モダリティ（センサー時系列 + 画像 / 動画）を融合した解析。
`UnifiedSPC` / `VideoCUSUM` / `VLMFeatureExtractor`（v2.13+）。

---

## N

### Nonce
リプレイ攻撃を防ぐ一回限りのトークン。`NonceStore` が SQLite で
TTL 付きで保管。

### NTP
Network Time Protocol。`SqliteNonceStore` の TTL とリプレイ防御に
クロック同期が必要。

---

## O

### Ollama
ローカル LLM 実行環境。`OllamaBackend` がデフォルト LLM バックエンド。

### OPC-UA
産業用標準プロトコル。`OPCUAAdapter` が `asyncua` 経由でサポート。

### OSV
Open Source Vulnerabilities API。`SCA Gate` が依存ライブラリの CVE
を照会。

### Outstation（DNP3）
DNP3 で言うサーバー側（フィールド機器）。`DNP3Adapter` がクライアント
（Master）として接続。

---

## P

### PEM
Privacy-Enhanced Mail フォーマット。`-----BEGIN ... PRIVATE KEY-----`
を Layer 1 が検出。

### PII（Personally Identifiable Information）
個人識別情報。Layer 1.5 Presidio が検出。

### Presidio
Microsoft 製の PII 検出ライブラリ。LLMesh では optional 依存で
`PresidioDetector` 経由で利用。

### PromptFirewall
4 層構成のリクエスト検査機。LLMesh のセキュリティの中核。

### Property-based testing
ランダム入力で性質を検証するテスト手法。`hypothesis` で実装。

### Protocol Adapter
プロトコル固有 framing → 内部統一表現の変換層。LLMesh の中核拡張点。

---

## R

### RAD（Research Aggregation Directory）
Raptor の論文コーパス管理ディレクトリ。LLMesh は外部の Raptor
プロジェクト（`C:/Users/puruy/raptor/`）に 25 分野コーパスを配置。

### RAG（Retrieval-Augmented Generation）
外部知識ベースを LLM 入力に組込む手法。`llmesh.rag` モジュール
（v2.13+）。

### Replay protection
過去のメッセージの再送信攻撃を防ぐ仕組み。GOOSE の `stNum` 単調増加
チェック、Nonce TTL チェック等。

### RFC 6455
WebSocket protocol 規格。`WebSocketAdapter` が準拠。

### Rust extension
LLMesh の `rust_ext/` モジュール。PointCloud encode が 6× 高速化。

---

## S

### SBOM（Software Bill of Materials）
ソフトウェア構成表。`python -m llmesh.cli.sbom` で CycloneDX 形式
生成。

### SCA Gate（Software Composition Analysis）
依存ライブラリの脆弱性チェック。`llmesh.mcp.sca_gate`。

### SCADA
Supervisory Control and Data Acquisition。重要インフラの監視制御。
v3-N7 / Volume K で対応。

### SemVer（Semantic Versioning）
major.minor.patch のバージョン規約。LLMesh は v3.0.0 以降に正式適用。

### SensorEvent
全産業アダプタが共通で生成するセンサーデータエンベロープ。

### SPC（Statistical Process Control）
統計的工程管理。`XbarRChart` / `CUSUMChart` / `HotellingT2Chart`。

### Spike（DVS）
DVS イベントの単発発火。

### STRIDE
Microsoft の脅威モデリングフレームワーク。LLMesh の脅威モデル
（`docs/SECURITY.md`）の構造。

---

## T

### Taguchi
品質工学の創始者。MT-method の "T" は Taguchi。

### Telemetry
センサーデータ全般。`SensorEvent` で正規化。

### TenantScope
マルチテナント分離。テナント ID をメトリクス / ログ / 認証境界で伝播。

### TimelineStore
タスクごとのライフサイクルを SQLite で保持。再開可能タスクの特定に
使用。

### Trust boundary
信頼境界。LLMesh では LLM backend / 外部 HTTP / 受信 SensorEvent /
ファイル入力（.npz / sqlite）の各 boundary で fail-closed gate を配置。

### TrustedPeers
署名済みの信頼できるノード集合。Capability Manifest と組合せて使用。

---

## U

### UnifiedMessage
プロトコル横断の標準メッセージ型。`MessageType` ENUM で意味付け。

### Unit space（MT-method）
正常データから学習した平均・標準偏差・逆相関行列のセット。

---

## V

### VLM（Vision-Language Model）
LLaVA 等の画像理解 LLM。`VLMFeatureExtractor`（v2.14+）で取り込み。

### Volume A〜N
LLMesh 要件定義書の章分割（`docs/REQUIREMENTS.md`）。
- A-B: 基盤
- C: 解析エンジン
- D-E: ネットワーク・3D
- F: EtherCAT
- G: 統合
- H: SBOM
- I: マイグレーション
- J: WebSocket
- K: 重要インフラ
- L: エッジ・RTOS
- M: 量子・先進計算
- **N: 学際横断融合（Research Backlog）**

---

## W

### WebSocket
RFC 6455 双方向通信プロトコル。`WebSocketAdapter`（v2.11+）。

---

## X

### Xbar-R chart
Shewhart 管理図。サブグループ平均（X̄）と範囲（R）を監視。
`XbarRChart` 実装。

---

## 略語表

| 略 | 正式 |
|----|------|
| ABI | Application Binary Interface |
| ANN | Approximate Nearest Neighbor |
| AOI | Automated Optical Inspection |
| API | Application Programming Interface |
| BLE | Bluetooth Low Energy |
| BMS | Building Management System |
| CIDR | Classless Inter-Domain Routing |
| CVE | Common Vulnerabilities and Exposures |
| DID | Decentralized Identifier |
| DoS | Denial of Service |
| DVS | Dynamic Vision Sensor |
| ECDH | Elliptic Curve Diffie-Hellman |
| ELK | Elasticsearch + Logstash + Kibana |
| GOOSE | Generic Object Oriented Substation Event |
| HMAC | Hash-based Message Authentication Code |
| HMI | Human Machine Interface |
| IED | Intelligent Electronic Device |
| IoT | Internet of Things |
| L0–L4 | Data Levels 0 through 4 |
| LLM | Large Language Model |
| LSH | Locality-Sensitive Hashing |
| MCP | Model Context Protocol |
| MD | Mahalanobis Distance |
| MTU | Maximum Transmission Unit |
| OOM | Out Of Memory |
| OWASP | Open Web Application Security Project |
| PII | Personally Identifiable Information |
| PoC | Proof of Concept |
| PSRC | Power System Relaying Committee |
| RAG | Retrieval-Augmented Generation |
| RCA | Root Cause Analysis |
| RCE | Remote Code Execution |
| ReDoS | Regex DoS |
| RTOS | Real-Time Operating System |
| SAST | Static Application Security Testing |
| SBOM | Software Bill of Materials |
| SCA | Software Composition Analysis |
| SCADA | Supervisory Control and Data Acquisition |
| SHA | Secure Hash Algorithm |
| SLO | Service Level Objective |
| SMT | Surface-Mount Technology |
| SNMP | Simple Network Management Protocol |
| SPC | Statistical Process Control |
| SRoS2 | Secure Robot Operating System 2 |
| SSRF | Server-Side Request Forgery |
| TLS | Transport Layer Security |
| TOCTOU | Time-of-Check Time-of-Use |
| TPM | Trusted Platform Module |
| TTL | Time To Live |
| UCL / LCL | Upper / Lower Control Limit |
| UTC | Coordinated Universal Time |
| V2X | Vehicle-to-Everything |
| VLM | Vision-Language Model |
| WAL | Write-Ahead Log |
