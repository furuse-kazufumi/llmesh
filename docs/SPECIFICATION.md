# LLMesh Specification (v2.0.1)

> **この仕様書をひとことで（中学生レベルのかみ砕き説明）**
>
> これは LLMesh という「工場や会社の中だけで動く AI のしくみ」の設計図です。むかしの電話局の交換手が、かかってきた電話を正しい相手につないでいたように、LLMesh は人や機械からの質問を受け取り、いちばん合った AI に取りつぎ、答えを返します。大事なのは、このしくみが自分の建物の中だけで動くこと。だから工場のセンサーの数字や個人の情報を、外のインターネットに出さずに AI で調べられます。
>
> この文書は、その「外に出さない約束」「いつもとちがう変化を見つける見はりのやり方」「機械やセンサーとのつなぎ方」を、作る人が守るルールとして細かく書いたものです。むずかしい英語の言葉が出てきますが、初めて出たところで日本語の言いかえを横に付けています。意味は [用語集（GLOSSARY.md）](GLOSSARY.md) でも調べられます。

LLMesh の正式仕様書 — Industrial Phase A〜G およびそれ以前の全機能の仕様を網羅。

このドキュメントは [`REQUIREMENTS.md`](REQUIREMENTS.md) で定義された要件の **実装契約** を記述します。

用語の詳しい意味は [用語集（GLOSSARY.md）](GLOSSARY.md) を参照してください。

---

## 1. システム全体仕様

### 1.1 ミッション

ローカル環境内で動作する LLM スウォームを **MCP (Model Context Protocol)** 上に
統一し、データ機密性を保証しながら産業用センサー解析・予知保全・自然言語診断を
提供する。クラウド送信ゼロ。

### 1.2 アーキテクチャ層

| 層 | 責務 |
|----|------|
| L1 — Identity | DID（Decentralized Identifier）、Ed25519/X25519 鍵管理、署名 |
| L2 — Discovery | Rendezvous / Gossip / DNS-SD / mDNS |
| L3 — Protocol Adapters | HTTP / TCP / UDP / SSH / SFTP / SMTP / IMAP / POP3 / FTP / SNMP / Modbus / OPC-UA / MQTT / EtherCAT / ROS / 3D Sensors |
| L4 — Privacy Pipeline | PromptFirewall → PrivacySummarizer → ImageFirewall → SpatialSummarizer |
| L5 — LLM Backend | Ollama / LlamaCpp（プラガブル） |
| L6 — Industrial Analysis | MTEngine / SPC（Xbar-R, CUSUM）/ IndustrialPipeline |
| L7 — Audit / Fairness | AuditChain / Fairness Receipt / Witness |

---

## 2. データモデル仕様

### 2.1 SensorEvent — 産業用センサーデータの統一エンベロープ

```python
@dataclass(frozen=True)
class SensorEvent:
    sensor_id: str          # デバイス内一意識別子
    protocol: str           # "modbus" | "opcua" | "mqtt" | "ethercat" |
                            # "aoi" | "depth" | "dvs" | ...
    timestamp_ns: int       # UNIX エポック ナノ秒（time.time_ns()）
    payload: bytes          # 生バイト（プロトコル固有）
    priority: Priority      # CRITICAL | HIGH | NORMAL
    device_id: str          # 親デバイス識別子
    sensor_type: str        # "pressure" | "temperature" | "depth_frame" | ...
    unit: str               # SI 単位文字列
    metadata: dict[str, Any]  # プロトコル固有補助情報
```

**不変条件:**
- 生成後の変更は禁止（frozen dataclass）
- `timestamp_ns` は単調増加でなくてもよいが、UNIX エポック基準
- `payload` のフォーマットは `(protocol, sensor_type)` の組み合わせで決定

### 2.2 DiagnosisResult — 産業診断結果

```python
@dataclass(frozen=True)
class DiagnosisResult:
    sensor_id: str
    device_id: str
    status: DiagnosisStatus    # NORMAL | WARNING | ANOMALY | CRITICAL | UNKNOWN
    severity: float            # [0.0, 1.0]
    summary: str               # 1 行 LLM-ready 説明
    evidence: dict[str, Any]   # MD 値・閾値・サブグループなど詳細
    timestamp_ns: int
    source_protocol: str
```

**メソッド:**
- `to_prompt_text() -> str`: PromptFirewall 入力形式へのフォーマット

---

## 3. プロトコルアダプター仕様

### 3.1 共通インターフェース

```python
class IndustrialAdapter(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def on_event(self, callback: Callable[[SensorEvent], None]) -> None: ...
```

### 3.2 ModbusAdapter（v1.4.0）

- Modbus TCP（デフォルト port 502）/ RTU（RS-485/RS-232）対応
- 4 種レジスタ: HOLDING / INPUT / COIL / DISCRETE
- ポーリング間隔可変（最小 0.1 秒）
- スレーブ ID: 1–247 / アドレス: 0x0000–0xFFFF / カウント: 1–125

### 3.3 SerialAdapter（v1.4.0）

- RS-232 / RS-485 受信
- フレーミング: 改行・バイト数・カスタム終端

### 3.4 OPCUAAdapter（v1.6.0）

- 受信方式: **サブスクリプション（プッシュ）**
- エンドポイント: `opc.tcp://host:port`
- 周期: `subscription_period_ms`（最小 50 ms）
- 自動再接続（`reconnect_delay_s`）

### 3.5 MQTTAdapter（v1.6.0）

- プロトコル: MQTT v3.1.1 / v5.0
- ワイルドカード: `+`（単一レベル）/ `#`（複数レベル、末尾のみ）
- TLS: `ssl.SSLContext` 任意指定
- QoS: 0 / 1 / 2

### 3.6 EtherCATAdapter（v1.8.0）

- マスター実装: SOEM（pysoem 経由）
- 状態機械: INIT → PRE-OP → SAFE-OP → OPERATIONAL
- PDO データ型: 10 種（int8〜float64）
- 物理値変換: `physical = raw_value * scale + offset`
- **要件: Linux + CAP_NET_RAW or root**

### 3.7 AoiAdapter（v1.7.0）

- 監視対象拡張子: `.jpg` / `.jpeg` / `.png` / `.bmp`
- サイドカー: `<stem>.aoi.json`（最大 64 KiB）
- NG → `Priority.HIGH` 自動昇格
- **Atomic write 検出**: 連続 2 ポーリングでサイズ不変なら処理（v2.0.1）

### 3.8 DepthCameraAdapter（v1.7.0）

- 形式 1 (`.depth.bin`): uint32 width / uint32 height / float32 grid LE
- 形式 2 (`.depth.npy`): NumPy 2-D float32 配列、`allow_pickle=False` 強制
- 範囲フィルタ: `max_range_m` 超過は除外
- 出力 payload: PointCloud バイト列（12 B/点）

### 3.9 EventCameraAdapter（v1.7.0）

- 形式 (`.dvs.bin`): 9 B/イベント — `<HHIb` (uint16 x, uint16 y, uint32 t_us, uint8 polarity)
- 上限: `_MAX_EVENTS_PER_BATCH = 1,000,000`

---

## 4. 解析エンジン仕様

### 4.1 MTEngine — マハラノビス・タグチ法

- 学習: `fit(data: ndarray[N, p])` → ユニットスペース（µ, σ, R⁻¹）
- 推論: `md(sample) -> float`、`is_anomaly(sample, threshold=3.0)`
- 永続化: `.npz`（numpy）
- 退化処理: 零分散特徴は無視、特異相関行列は擬似逆行列にフォールバック

### 4.2 XbarRChart — Shewhart Xbar-R 管理図

- サブグループサイズ: 2–10
- ASTM 係数テーブル: A2 / D3 / D4
- 出力: `SPCResult(in_control, value, ucl, lcl, violations, extra)`

### 4.3 CUSUMChart — 二方向 CUSUM

- パラメータ: target / k（許容量）/ h（決定インターバル）/ sigma
- 状態: `S+` / `S-` 累積和
- メソッド: `update(value)`, `is_out_of_control()`, `reset()`

### 4.4 OnlineMTEngine — ストリーミング Mahalanobis（v2.13+）

- ラップ対象: 既存 `MTEngine`（fit 済 / load 済）
- API: `score_batch(batch) -> OnlineScore(distances, anomalies, threshold)`
- 計算: `einsum("ni,ij,nj->n", z, R⁻¹, z)`（O(n·p²) ベクトル化）
- メモリ上限: `LLMESH_MT_ONLINE_MAX_BATCH_BYTES`（既定 16 MiB）
  超過時はチャンク分割で透過処理
- 不変条件: スレッドセーフではない（1 ワーカー 1 エンジン）

### 4.5 HotellingT2Chart — 多変量 T² 管理図（v2.13+）

- 学習: `fit(reference: ndarray[N, p])` → 中心 µ + 共分散 Σ⁻¹
  （Tikhonov ε I で rank-deficient データに対応）
- UCL: 明示指定 or `α` から χ² 漸近近似
  `UCL ≈ p + sqrt(2p) * sqrt(-2 ln α)`
- API: `score(x) -> T2Decision(statistic, in_control, ucl)` /
  `score_batch(batch) -> T2BatchDecision`

### 4.6 EventDensityMap — DVS イベント空間集約（v2.13+）

- 入力: 構造化 numpy 配列 / `(n,3)` xyp / `(n,4)` txyp
- 出力: `DensityFeature(vector, grid_shape, event_count)`
- グリッド: 既定 8×8 = 64 次元、`grid_w/h` で可変
- polarity フィルタ: `both` / `on` / `off`
- センサー解像度マッピングは線形クリップで境界保護

### 4.7 UnifiedSPC — マルチモーダル（multimodal） SPC（v2.13+）

- センサー時系列 + VLM テキスト特徴の 2 系統 SPC を結合
- 各チャネルは `XbarRChart` または `CUSUMChart`
- 結合モード:
  - `or`（既定）: いずれか alarm で全体 alarm
  - `and`: 両方 alarm の場合のみ全体 alarm
  - `weighted`: `sensor_w * out_s + text_w * out_t > threshold` で alarm
- 出力: `UnifiedSPCResult(in_control, sensor_result, text_result, mode, score, violations)`

### 4.8 LLMExplainer — 異常 → 自然言語レポート（v2.13+）

- 入力: `AlarmEvent(incident_id, timestamp, sensor_id, statistic, threshold, metric, contributing_dims, metadata)`
- 出力: `IncidentReport(incident_id, severity, cause, suggestion, markdown, payload)`
- LLM オプショナル: 未配線時はテンプレート、LLM 失敗時もテンプレート復帰（fail-safe）
- LLM 応答は 1024 文字に bound、空応答はテンプレート
- severity_map: 既定 `(2.0, "critical"), (1.0, "warn"), (0.0, "info")`
  — `deviation / threshold` 比率で分類（threshold=0 は raw deviation）

### 4.9 ExplainedCUSUM — 自己説明 CUSUM（v2.14+）

- ラップ対象: 既存 `CUSUMChart`
- API: `update(value) -> ExplainedSPCResult(spc_result, report, incident_id)`
- alarm 発生時に `LLMExplainer` で `IncidentReport` を生成、in_control 時は
  `report=None`
- DI: `clock` / `incident_id_factory` で決定論的テスト可能
- 不変条件: `chart` 必須、explainer 未指定時は LLMExplainer() を遅延生成

### 4.10 VideoCUSUM — 動画 + センサー時刻同期 CUSUM（v2.14+）

- 入力: 各チャネル `(timestamp_s: float, value: float)` の連続列
- 内部: 2 つの `CUSUMChart` + 各チャネルの bounded deque（`buffer_size`、
  既定 128）+ ペアリング窓（`sync_window_s`、既定 1.0s）
- API: `ingest_frame(t, v)` / `ingest_sensor(t, v) -> VideoCUSUMResult`
- ペア化アルゴリズム: 反対チャネルの pending alarm から
  `|t - t_other| ≤ sync_window_s` のうち最近接を消費
- 古い alarm は新規受信時に左端から `t < cutoff` を popleft で eviction
- `pending_alarms() -> (frame_pending, sensor_pending)` で内部状態の検査可能

### 4.11 VLMFeatureExtractor — 画像 → 数値特徴（v2.14+）

- 段階: ImageFirewall ゲート → VisionCaptioner → デフォルト/カスタム parser
- 入力: 任意の `bytes`、出力: `VLMFeature(vector, caption, allowed, action, reason)`
- `MockVisionCaptioner`: Pillow 検出時はピクセル統計、不在時は SHA-256
- デフォルト parser:
  - 前半: caption 内の数値トークンを `_NUMBER_RE` で抽出
  - 後半: 欠陥キーワード集計 + 文字長 / 数字数 / 文字数
  - 必ず `dimension` 長で 0 パディング
- fail-closed:
  - ImageFirewall 例外 → BLOCK (`image_firewall_error_fail_closed`)
  - captioner 例外 → BLOCK (`captioner_error_fail_closed`)
  - 非文字列 caption → BLOCK (`captioner_returned_non_string`)
  - ImageFirewall が不正な action を返す → BLOCK (`image_firewall_unknown_decision`)

### 4.12 SqliteVectorStore — 純 sqlite3 永続ベクトルストア（v2.14+ / F-1.1）

- スキーマ: `docs(doc_id PRIMARY KEY, text, vec BLOB, meta JSON)` +
  `meta_kv(key, value)`（`dimension` / `schema_version` / `created_at`）
- ベクトル形式: little-endian float32 BLOB（machine endian と異なる場合は byteswap）
- WAL モード + `synchronous=NORMAL`、UPSERT で同 ID 上書き
- 検索: 全スキャン cosine（≤10⁶ 件で実用、ANN は別バックエンドで提供予定）
- 永続化: `save(path)` は sqlite native backup API でアトミック複写
- `load(path)` は `meta_kv` 不在時に ValueError（OperationalError ラップ）

### 4.13 DNP3Adapter — SCADA outstation client（v2.14+ skeleton / v3-N7 / K-1.1）

- group code → `sensor_type` マッピング（binary_input/output、counter、
  analog_input/output、time、unknown は `dnp3_g{N}`）
- 値エンコーディング: bool→1B / int→struct.pack("<q") / float→struct.pack("<d") /
  bytes 透過 / その他は str→utf-8
- allow-list: `(master_addr, outstation_addr)` ペア集合
- `connect(driver=None)`: driver 注入で wire 層を bypass、未指定なら pydnp3 を
  optional 動的 import、不在時 RuntimeError
- `poll()`: 接続済 + driver あり時のみ `driver.read_static()` を呼び SensorEvent
  に展開、callback 例外は隔離（loop 継続）

### 4.14 GOOSEAdapter — IEC 61850 GOOSE subscriber（v2.14+ skeleton / v3-N7）

- 入力: `GoosePDU(go_cb_ref, dat_set, st_num, sq_num, dataset)` を返す
  `GooseTransport.recv()`
- 制限: `MAX_DATASET_VALUES=256`（オーバーサイズ拒否）
- フィルタ:
  - `allow_iedids`: `goCBRef` ホワイトリスト
  - リプレイ防御: per-`goCBRef` の `stNum` 単独カウンター（後退は drop、
    等値は許可 — 同一 state 内の sqNum 違いリトランスミッション対応）
- 出力: 1 dataset member につき 1 `SensorEvent`（`Priority.HIGH`、
  protocol="iec61850_goose"、metadata に PDU 座標）
- `step()` / `drain(max_steps=1024)`、コールバック例外隔離

### 4.15 IndustrialPipeline — 統合パイプライン

```python
class IndustrialPipeline:
    def attach_mt(device_id, engine, *, threshold, feature_extractor=None) -> None
    def attach_cusum(sensor_id, *, target, k, h, sigma=None, value_extractor=None) -> None
    def attach_xbar_r(sensor_id, *, chart, subgroup_size, value_extractor=None) -> None
    def on_diagnosis(callback: Callable[[DiagnosisResult], None]) -> None
    def process(event: SensorEvent) -> DiagnosisResult
```

**振る舞い:**
- 複数アナライザの結果から **最高 severity** を返却
- アナライザ例外は分離（1 つの失敗が他をクラッシュさせない）
- デフォルト value_extractor: `metadata["physical_value"]` → payload float64 LE → payload float32 LE
- デフォルト feature_extractor: payload を float64 LE 配列として解釈

---

## 5. プライバシー仕様

### 5.1 不変条件（全アダプター共通）

1. 生 L4 プロンプトは LLM バックエンドに到達しない
2. 生 L3 プロンプトは LLM バックエンドに到達しない（要約後のみ通過）
3. 拒否動作はフェイルクローズ（任意の例外で BLOCK）
4. 監査チェーン検証で改ざんを検出
5. `shell=True` / `eval` / `exec` / `pickle` / 安全でない SQL を使用しない
6. すべてのアダプターは TrustedPeers または明示的オプトアウトで認証
7. Telnet アダプターは二重オプトイン + 廃止警告

### 5.1.5 PromptFirewall レイヤー構成（v2.13+）

| Layer | 役割 | アクション | 主な検出対象 |
|------:|------|------------|--------------|
| 0 | プロンプト注入検出 | BLOCK (L4) | "ignore previous instructions"、DAN/jailbreak、ChatML 特殊トークン、Unicode 制御文字 |
| 1 | シークレット / クレデンシャル | BLOCK (L4) | API キー、JWT、PEM 秘密鍵、AWS/GitHub/Anthropic/OpenAI トークン、bearer、password |
| **1.5** | **Presidio PII 検出（optional）** | **BLOCK (L4) or SUMMARIZE (L3)** | **CC / SSN / IBAN / 医療免許 / 個人名 / Email / 電話 / 住所 / IP / Date** |
| 2 | 構造的機密 | SUMMARIZE (L3) or BLOCK (L4) | 絶対パス、内部 import、`max_payload_chars` 超過 |

不変条件:
- Layer 1.5 は `presidio=None`（既定）で no-op、後方互換維持
- Presidio 不在 → ALLOW（reason=`presidio_unavailable`）
- Presidio 例外 → BLOCK (L4)（fail-closed）
- BLOCK エンティティと SUMMARIZE エンティティは **disjoint**（既定で衝突無し）
- BLOCK 検出は SUMMARIZE 検出より優先

### 5.3 RAG プライバシー（v2.13+）

`Retriever` は Embedder + VectorStore + 任意の `PromptFirewall` を結合する:

- **インデックス時**: ドキュメントテキストを Firewall に通し、L4 BLOCK は
  インデックスから除外（戻り値 False）
- **検索時**:
  1. クエリを Firewall に通し、L4 BLOCK なら検索を行わず空リスト返却
  2. 各検索結果を再度 Firewall に通し、`drop_blocked=True`（既定）なら
     L4 結果を破棄、`SUMMARIZE` 結果は `RetrievalResult.action="SUMMARIZE"` で
     呼び出し側に通知（呼び出し側は `PrivacySummarizer` を経由する責任あり）
- 永続化（`.npz`）は `allow_pickle=False` 強制、書込みは tmp→rename で
  アトミック

### 5.2 3D センサー特有

- 画像ピクセル / 点群 / DVS イベントの生バイトは LLM へ送らない
- `SpatialSummarizer.summarize()` で安全なテキスト形式へ変換してから渡す
- AOI 画像のサイドカーは 64 KiB を上限とする
- 深度マップ `.npy` は `allow_pickle=False` 強制

---

## 6. セキュリティ仕様

### 6.1 静的解析（v2.0.1 時点）

- bandit: HIGH=0, MEDIUM=0, LOW=12（try/except pass 等の些細な指摘のみ）
- ruff: 自動修正後 `F401 / UP035 / UP037 / B905` ゼロ

### 6.2 入力サニタイズ

- EtherCAT: ifname を `[a-zA-Z0-9_\\-.]{1,15}` で検証
- MQTT: トピックは null バイト禁止、最大 65535 バイト
- AOI: ファイルパスを shell コマンドへ補間しない
- すべてのアダプター: subprocess は list 引数のみ使用

### 6.3 暗号

- 鍵対: Ed25519（署名）/ X25519（鍵交換）
- 乱数: `secrets`（v1.2.1 以降）
- 監査チェーン: SHA-256 ハッシュチェイン
- TLS: 各アダプターで `ssl.SSLContext` 渡しでオプトイン

---

## 7. テスト仕様

### 7.1 テスト総数（v2.0.1 時点）

- 例ベーステスト: ~1,829 件
- プロパティベーステスト: 12 関数 × 50 ランダム入力 = 600 ケース（hypothesis）
- すべて pytest 経由、`asyncio_mode = "auto"`

### 7.2 カバレッジ目標

- Industrial 全モジュール: line coverage ≥ 80%
- セキュリティクリティカル領域（privacy, auth, audit）: ≥ 90%

### 7.3 CI ゲート

1. `pytest -q` 全件 PASS
2. `python -m build` クリーン
3. `bandit` HIGH=0, MEDIUM=0
4. `ruff check llmesh/` エラーゼロ

---

## 8. パフォーマンス仕様

### 8.1 目標スループット（参考値）

| アダプター | 目標スループット | 備考 |
|-----------|---------------|------|
| Modbus TCP | 100 reg/s | poll_interval_s=0.1 時 |
| OPC-UA | 1000 events/s | sub_period_ms=100 |
| MQTT | 10,000 msg/s | QoS=0 |
| EtherCAT | 1000 cycles/s | cycle_time_us=1000 |
| AOI | 10 frames/s | poll_interval_s=0.1 |
| Depth | 30 frames/s | 640×480 |
| DVS | 100,000 events/s | 9 B/イベント |

### 8.2 メモリ上限

- AOI/Depth/DVS `_seen` セット: `_SEEN_SET_MAX = 10,000` で自動 FIFO 半分削除
- DVS バッチ: `_MAX_EVENTS_PER_BATCH = 1,000,000`
- AOI サイドカー: `_SIDECAR_MAX_BYTES = 65,536`

---

## 9. ロードマップ完了状況

| Phase | バージョン | 状態 |
|-------|-----------|------|
| A | v1.3.0 | ✓ |
| B | v1.4.0 | ✓ |
| C | v1.5.0 | ✓ |
| D | v1.6.0 | ✓ |
| E | v1.7.0 | ✓ |
| F | v1.8.0 | ✓ |
| G | v2.0.0 | ✓ |
| Patch | v2.0.1 | ✓（ロバスト性・最適化・property-based testing） |

---

## 10. 次期バージョン候補

[`REQUIREMENTS.md`](REQUIREMENTS.md) の v3 セクション参照。
