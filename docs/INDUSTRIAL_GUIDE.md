# LLMesh Industrial Guide (v2.0.0)

このガイドは LLMesh Industrial（Phase A〜G）の機能と使い方を網羅的にまとめた資料です。

---

## かみ砕いた説明（中学生レベル）

この資料は「工場の機械やセンサーの声を AI につなぐしくみ」の説明書です。温度や圧力などをはかる機械は、それぞれちがう言葉（しゃべり方）で数字を送ってきます。LLMesh はその数字を一つの形にそろえてから、「いつもとちがう変化（=異常）」がないかを見はり、見つけたら人にわかる言葉で伝えます。

イメージは、いろんな国の人が集まる会議の通訳さんです。全員の話を共通の言葉に直して、おかしな点があれば「ここが変ですよ」と教えてくれる係です。むずかしい英語の言葉が出てきたら、初めて出たところに日本語の言いかえを付けてあります。意味をくわしく知りたいときは [用語集（GLOSSARY.md）](GLOSSARY.md) を見てください。

---

## 目次

1. [アーキテクチャ概要](#アーキテクチャ概要)
2. [SensorEvent — 統一センサーデータ envelope](#sensorevent)
3. [Phase B — Modbus / Serial アダプター](#phase-b--modbus--serial)
4. [Phase C — 解析エンジン（MT法・SPC）](#phase-c--解析エンジン)
5. [Phase D — OPC-UA / MQTT アダプター](#phase-d--opc-ua--mqtt)
6. [Phase E — 3D センサー統合](#phase-e--3d-センサー統合)
7. [Phase F — EtherCAT アダプター](#phase-f--ethercat)
8. [Phase G — IndustrialPipeline 全統合](#phase-g--industrialpipeline)
9. [プライバシーパイプラインとの統合](#プライバシーパイプラインとの統合)
10. [導入手順](#導入手順)

---

## アーキテクチャ概要

```
[Field Sensors / Cameras]
        │
        │  Modbus / Serial / OPC-UA / MQTT / EtherCAT / AOI / Depth / DVS
        ▼
┌──────────────────────────────────┐
│  Industrial Adapters             │   ← Phase B, D, E, F
│  すべて SensorEvent を生成        │
└────────────────┬─────────────────┘
                 ▼
┌──────────────────────────────────┐
│  IndustrialPipeline              │   ← Phase G
│  MT法 / CUSUM / Xbar-R を並走     │
└────────────────┬─────────────────┘
                 ▼
┌──────────────────────────────────┐
│  DiagnosisResult                 │
│  status / severity / summary     │
└────────────────┬─────────────────┘
                 ▼
┌──────────────────────────────────┐
│  PromptFirewall → LLM Backend    │   ← 既存 LLMesh プライバシーパイプライン
└──────────────────────────────────┘
```

すべての産業用アダプターは **`SensorEvent`** という単一フォーマットを生成するため、
解析エンジンと診断パイプラインはプロトコルに依存しません。

---

## SensorEvent

`llmesh.industrial.sensor_event.SensorEvent` — frozen dataclass の不変データ envelope。

| フィールド | 型 | 説明 |
|----------|----|------|
| `sensor_id` | `str` | デバイス内のユニーク ID |
| `protocol` | `str` | `"modbus"` / `"opcua"` / `"mqtt"` / `"ethercat"` / `"aoi"` / `"depth"` / `"dvs"` |
| `timestamp_ns` | `int` | UNIX エポック ナノ秒 |
| `payload` | `bytes` | 生バイト（プロトコル依存） |
| `priority` | `Priority` | CRITICAL / HIGH / NORMAL |
| `device_id` | `str` | 親デバイス ID |
| `sensor_type` | `str` | `"pressure"` / `"temperature"` / `"depth_frame"` 等 |
| `unit` | `str` | SI 単位（`"Pa"` / `"°C"` 等） |
| `metadata` | `dict` | プロトコル固有の補助情報 |

```python
from llmesh.industrial import SensorEvent, Priority

ev = SensorEvent.create(
    sensor_id="pressure_01",
    protocol="modbus",
    payload=b"\x00\x00\x80\x3f",
    sensor_type="pressure", unit="Pa",
    priority=Priority.HIGH,
)
```

---

## Phase B — Modbus / Serial

### `ModbusAdapter`

Modbus TCP / RTU からホールディングレジスタ・入力レジスタ・コイル・離散入力をポーリング。

```python
from llmesh.industrial import ModbusAdapter, RegisterType, Priority

adapter = ModbusAdapter.tcp("192.168.1.10", 502, poll_interval_s=1.0)
adapter.add_register(
    slave_id=1, address=0x0000, count=2,
    sensor_id="pressure_01",
    sensor_type="pressure", unit="Pa",
    register_type=RegisterType.HOLDING,
)
adapter.on_event(lambda ev: print(ev))
await adapter.start()
# ...
await adapter.stop()
```

RTU の場合：
```python
adapter = ModbusAdapter.rtu("/dev/ttyUSB0", baud_rate=9600)
```

### `SerialAdapter`

汎用 RS-232 / RS-485 シリアル受信。改行・バイト数・カスタム終端で
フレーミング可能（詳細は `serial_adapter.py` の docstring を参照）。

---

## Phase C — 解析エンジン

### `MTEngine` — マハラノビス・タグチ法

正常データから **ユニットスペース**（平均・標準偏差・逆相関行列）を学習し、
新サンプルとのマハラノビス距離 `MD = √(zᵀ R⁻¹ z / p)` を返す。

```python
from llmesh.industrial import MTEngine

engine = MTEngine()
engine.fit(normal_data)              # shape: (N, p)
md = engine.md([0.5, 1.2, 3.1])      # スカラー距離
is_anom = engine.is_anomaly(sample, threshold=3.0)
engine.save("smt01_unit_space.npz")  # 永続化
```

### CLI

```bash
llmesh mt-collect --device smt01 --duration 3600 --output normal.npz
llmesh mt-train   --input normal.npz --device smt01
llmesh mt-infer   --device smt01 --threshold 3.0
```

### `XbarRChart` — Shewhart Xbar-R 管理図

サブグループサイズ 2〜10 に対し ASTM 係数（A2 / D3 / D4）で UCL/LCL を計算。

```python
from llmesh.industrial import XbarRChart

chart = XbarRChart()
chart.fit(subgroups=[[1.0, 1.1, 0.9], [1.05, 0.95, 1.02], ...])
result = chart.check([0.98, 1.02, 1.05])
print(result.in_control, result.violations)
```

### `CUSUMChart` — 二方向 CUSUM 管理図

```python
from llmesh.industrial import CUSUMChart

chart = CUSUMChart(target=100.0, k=0.5, h=4.0, sigma=1.0)
for value in stream:
    res = chart.update(value)
    if not res.in_control:
        print("Drift detected:", res.violations)
```

---

## Phase v3 — 説明可能 SCADA / µs 異常検知 / マルチモーダル SPC（v2.13.0+）

v3 Implementation Plan（A 分類 3 テーマ — N-7 / N-11 / N-15）の
コアモジュール。詳細仕様は `REQUIREMENTS.md` の **v3 Implementation Plan**
セクションを参照。

### `OnlineMTEngine` — ストリーミング Mahalanobis 推論（v3-N11）

`MTEngine` をラップしたバッチ対応エンジン。`einsum` でベクトル化、
メモリ上限は環境変数 `LLMESH_MT_ONLINE_MAX_BATCH_BYTES`（既定 16 MiB）で
制御し、超過時は内部チャンキングで分割処理。

```python
from llmesh.industrial.mt_engine import MTEngine
from llmesh.industrial.mt_online import OnlineMTEngine

eng = MTEngine.load("unit_space.npz")
online = OnlineMTEngine(eng, threshold=3.0)
result = online.score_batch(batch)        # batch: shape (n, p)
print(result.distances)                   # numpy float64, shape (n,)
print(result.anomalies)                   # numpy bool, shape (n,)
```

### `HotellingT2Chart` — 多変量 Hotelling T² 管理図（v3-N11）

共分散行列ベース。Tikhonov 正則化（`pinv + ε I`）により rank-deficient
参照データでも動作。UCL は明示指定または `α` から χ² 漸近近似で自動算出。

```python
from llmesh.industrial.hotelling_t2 import HotellingT2Chart

chart = HotellingT2Chart(alpha=0.005).fit(reference_2d_array)
verdict = chart.score(new_observation)
print(verdict.statistic, verdict.in_control, verdict.ucl)

# バッチ処理
batch_verdict = chart.score_batch(batch_2d_array)
```

### `EventDensityMap` — DVS イベント → 固定次元特徴（v3-N11）

DVS センサーの `(t, x, y, polarity)` を粗いグリッド（既定 8×8 = 64 次元）に
投影し、SPC / OnlineMTEngine 入力として使えるベクトルに変換。

```python
from llmesh.industrial.event_density_map import EventDensityMap

m = EventDensityMap(sensor_w=346, sensor_h=260, grid_w=8, grid_h=8)
feature = m.aggregate(events_array)        # 構造化 / (n,3) / (n,4) 受け入れ
print(feature.vector)                      # numpy float64, shape (64,)

# polarity フィルタ可能
m_on = EventDensityMap(346, 260, polarity="on")
```

### `UnifiedSPC` — マルチモーダル品質管理（v3-N15）

センサー時系列と VLM 由来テキスト特徴の 2 系統 SPC を結合。
既存 `XbarRChart` / `CUSUMChart` の任意組合せを各チャネルに割り当て可能。

```python
from llmesh.industrial.spc_engine import XbarRChart, CUSUMChart
from llmesh.industrial.multimodal_spc import UnifiedSPC

sensor_chart = XbarRChart().fit(sensor_baseline_subgroups)
text_chart   = XbarRChart().fit(vlm_baseline_subgroups)
spc = UnifiedSPC(sensor_chart, text_chart, mode="weighted",
                 sensor_weight=0.6, text_weight=0.6, threshold=0.5)
out = spc.update(sensor_subgroup, vlm_subgroup)
if not out.in_control:
    print("violations:", out.violations)
    print("score:", out.score)
```

結合モード:

| Mode | セマンティクス |
|------|----------------|
| `or` | どちらかが alarm で全体 alarm（既定、高感度） |
| `and` | 両方 alarm の場合のみ全体 alarm（高特異度） |
| `weighted` | 重み付き投票が `threshold` を超えたら alarm |

### `LLMExplainer` — 異常 → 自然言語レポート（v3-N7）

SPC / MT-method の `AlarmEvent` を Markdown + JSON 構造化 `IncidentReport`
に変換。LLM オプショナル設計 — 未配線時はテンプレート出力、LLM 失敗時も
テンプレート復帰（フェイルセーフ（fail-safe））。

```python
from llmesh.industrial.explainer import AlarmEvent, LLMExplainer

ex = LLMExplainer()  # LLM 未配線（テンプレートのみ）
event = AlarmEvent(
    incident_id="INC-001",
    timestamp="2026-05-08T10:30:00Z",
    sensor_id="dnp3:plant_a:01",
    statistic=4.7,
    threshold=3.0,
    metric="mahalanobis",
    contributing_dims=("temp_in", "vibration_z"),
)
report = ex.explain(event)
print(report.markdown)   # Markdown 整形済み
print(report.payload)    # JSON シリアライズ可能 dict
```

LLM を組合せる場合（PromptFirewall / PrivacySummarizer 通過済みの呼び出しを
渡す責任は呼び出し側にあります）:

```python
def llm_call(prompt: str) -> str:
    return ollama_backend.invoke("explain", {"prompt": prompt}).get("text", "")

ex = LLMExplainer(llm=llm_call)
```

severity マッピングはカスタム可能:

```python
ex = LLMExplainer(severity_map=(
    (3.0, "critical"),   # 3× threshold 以上
    (1.5, "warn"),
    (0.0, "info"),
))
```

---

## Phase D — OPC-UA / MQTT

### `OPCUAAdapter`

asyncua のサブスクリプション機構を利用。OPC-UA サーバーへ **クライアント** として接続し、
登録ノードのデータ変更を SensorEvent としてプッシュ。

```python
from llmesh.industrial import OPCUAAdapter, Priority

adapter = OPCUAAdapter("opc.tcp://plc.factory.local:4840",
                       subscription_period_ms=500)
adapter.add_node(
    node_id="ns=2;i=1001",
    sensor_id="pressure_01",
    sensor_type="pressure", unit="Pa",
    device_id="plc01",
    priority=Priority.HIGH,
)
adapter.on_event(lambda ev: print(ev))
await adapter.start()
```

### `MQTTAdapter`

paho-mqtt v3.1.1 / v5.0 対応。MQTT § 4.7 ワイルドカード（`+` / `#`）対応。

```python
import ssl
from llmesh.industrial import MQTTAdapter

ctx = ssl.create_default_context()  # 任意
adapter = MQTTAdapter("broker.local", 8883, tls_context=ctx,
                      username="iot", password="...")
adapter.add_topic("factory/+/temperature", "temp_any",
                  sensor_type="temperature", unit="°C", qos=1)
adapter.add_topic("factory/#",            "all_factory")
adapter.on_event(lambda ev: print(ev.metadata["topic"], ev.payload))
await adapter.start()
```

---

## Phase E — 3D センサー統合

### `AoiAdapter` — AOI 外観検査カメラ

ディレクトリ監視方式。`.jpg` / `.png` / `.bmp` ファイルを検出し、
オプションで JSON サイドカー（`<stem>.aoi.json`）から欠陥情報を読み取り。

サイドカー JSON スキーマ:
```json
{
    "result": "ok" | "ng",
    "defects": [
        {"label": "scratch", "confidence": 0.92, "bbox": [10, 20, 5, 5]}
    ],
    "board_id": "BOARD-007"
}
```

```python
from llmesh.industrial.sensor_3d import AoiAdapter

adapter = AoiAdapter("/data/aoi_drop", device_id="smt_aoi_01",
                     move_processed_to="/data/aoi_done")
adapter.on_event(lambda ev: print(ev.metadata))
await adapter.start()
```

### `DepthCameraAdapter` — RGB-D 深度カメラ

`.depth.bin`（uint32 width / uint32 height / float32 grid）または
`.depth.npy`（NumPy 2-D float32 配列）に対応。

```python
from llmesh.industrial.sensor_3d import DepthCameraAdapter, PointCloud

adapter = DepthCameraAdapter("/data/depth_drop", device_id="rs01",
                             max_range_m=5.0)
def handle(ev):
    pc = PointCloud.from_bytes(ev.payload)
    print(pc.stats())
adapter.on_event(handle)
await adapter.start()
```

### `EventCameraAdapter` — DVS イベントカメラ

`.dvs.bin` の 9 バイト/イベント（uint16 x, uint16 y, uint32 t_us, uint8 polarity）。

```python
from llmesh.industrial.sensor_3d import EventCameraAdapter, decode_dvs_events

adapter = EventCameraAdapter("/data/dvs_drop", device_id="prophesee_01")
adapter.on_event(lambda ev: print(ev.metadata["event_count"]))
await adapter.start()
```

### `SpatialSummarizer` — LLM 向け 3D サマリー

```python
from llmesh.industrial.sensor_3d import SpatialSummarizer

s = SpatialSummarizer()
text = s.summarize(sensor_event)
# AOI:   "AOI [BOARD-007] NG — 2 defect(s) detected. ..."
# Depth: "Depth frame [rs01] 15,234 points (640×480 px); z 0.42–3.18 m, centroid ..."
# DVS:   "DVS [prophesee_01] 1,024 events; +600 / -424; Δt 5,000 µs"
```

`text` は **生ピクセルを含まない** ため、安全に LLM プロンプトへ流せます。

---

## Phase F — EtherCAT

### `EtherCATAdapter`

SOEM (Simple Open EtherCAT Master) を pysoem 経由で利用。
Linux 専用。CAP_NET_RAW または root が必要。

10 種の PDO データ型に対応:
`int8` / `uint8` / `int16` / `uint16` / `int32` / `uint32` / `int64` / `uint64` / `float32` / `float64`

```python
from llmesh.industrial import EtherCATAdapter, Priority

adapter = EtherCATAdapter("eth0", cycle_time_us=1000)
adapter.add_slave(
    slave_pos=0, sensor_id="torque_01",
    data_type="float32", byte_offset=0,
    scale=1.0, offset=0.0,
    sensor_type="torque", unit="Nm",
    priority=Priority.HIGH,
)
adapter.on_event(lambda ev: print(ev.metadata["physical_value"]))
await adapter.start()
```

scale/offset は生 PDO 値 → 物理値変換に利用:
`physical = raw_value * scale + offset`

---

## Phase G — IndustrialPipeline

### 統合パイプライン

複数の解析器（MT法 / CUSUM / Xbar-R）を **デバイス** または **センサー単位**で登録し、
SensorEvent を投入すると最高 severity の `DiagnosisResult` を返します。

```python
from llmesh.industrial import IndustrialPipeline, MTEngine, DiagnosisStatus

pipeline = IndustrialPipeline()

# デバイス全体に MT法
pipeline.attach_mt(
    device_id="smt01",
    engine=trained_mt,
    threshold=3.0,
    feature_extractor=lambda ev: [
        ev.metadata["pressure"],
        ev.metadata["temperature"],
        ev.metadata["vibration_rms"],
    ],
)

# 個別センサーに CUSUM
pipeline.attach_cusum(
    sensor_id="pressure_01",
    target=101_325.0, k=0.5, h=4.0, sigma=100.0,
)

pipeline.on_diagnosis(lambda d: print(d.to_prompt_text()))

# Adapter からの SensorEvent を投入
def handle(ev):
    diagnosis = pipeline.process(ev)
    if diagnosis.status is DiagnosisStatus.ANOMALY:
        notify_operator(diagnosis.summary)

modbus_adapter.on_event(handle)
opcua_adapter.on_event(handle)
ethercat_adapter.on_event(handle)
```

### `DiagnosisResult`

| フィールド | 型 | 説明 |
|----------|----|------|
| `status` | `DiagnosisStatus` | NORMAL / WARNING / ANOMALY / CRITICAL / UNKNOWN |
| `severity` | `float` | 0.0（正常）→ 1.0（重篤） |
| `summary` | `str` | 1 行説明（LLM 向け） |
| `evidence` | `dict` | MD 値・閾値・サブグループなど詳細 |
| `to_prompt_text()` | `str` | プライバシーパイプライン入力形式 |

### デフォルト extractor

`feature_extractor` / `value_extractor` を省略すると:
- payload の先頭を float64 LE → なければ float32 LE → 失敗で UNKNOWN
- `metadata["physical_value"]`（EtherCAT）があればそれを優先

---

## プライバシーパイプラインとの統合

`DiagnosisResult.to_prompt_text()` を **PromptFirewall** に投入することで、
生センサーデータ（画像・点群・PDO バイト）が LLM へ漏洩しないことを保証します。

```python
from llmesh.privacy import PromptFirewall
from llmesh.privacy.sensor_summarizer import SensorSummarizer

firewall = PromptFirewall()
summarizer = SensorSummarizer()

def on_diagnosis(d):
    text = d.to_prompt_text()
    safe = firewall.scrub(text)         # PII / 秘密検出
    response = llm_backend.generate(safe)
    log_audit(d, response)

pipeline.on_diagnosis(on_diagnosis)
```

---

## 導入手順

```bash
# Linux 推奨。Windows でも EtherCAT 以外は動作。
pip install "llmesh[industrial]"

# EtherCAT を使う場合（Linux のみ）
pip install "llmesh[industrial,ethercat]"

# 設定ウィザード
llmesh configure
```

`llmesh.toml` の最小例:
```toml
[node]
node_id = "factory_node_01"

[adapters.modbus]
host = "192.168.1.10"
poll_interval_s = 1.0

[adapters.opcua]
endpoint = "opc.tcp://plc.factory.local:4840"

[adapters.mqtt]
host = "broker.local"
port = 8883
tls = true

[security]
ntp_servers = ["pool.ntp.org"]
max_clock_drift_s = 10
```

---

## ロードマップ完了

| Phase | バージョン | リリース日 |
|-------|-----------|-----------|
| A | v1.3.0 | 2026 |
| B | v1.4.0 | 2026 |
| C | v1.5.0 | 2026-05-07 |
| D | v1.6.0 | 2026-05-07 |
| E | v1.7.0 | 2026-05-07 |
| F | v1.8.0 | 2026-05-07 |
| **G** | **v2.0.0** | **2026-05-07** |

詳細は [`CHANGELOG.md`](CHANGELOG.md) を参照。
