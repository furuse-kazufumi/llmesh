<!--
title: Modbus / OPC-UA / DNP3 / IEC 61850 GOOSE を 1 個の SensorEvent に流し込んで、CUSUM で異常を捕まえて LLM に説明させる — LLMesh 産業 IoT 編
tags: 産業IoT,SCADA,Python,LLM,異常検知
-->

# Modbus / OPC-UA / DNP3 / IEC 61850 GOOSE を 1 個の SensorEvent に流し込んで、CUSUM で異常を捕まえて LLM に説明させる — LLMesh 産業 IoT 編

> 産業プロトコル × 多変量 SPC × LLM 説明レポート を 1 ライブラリで
> `pip install "llmesh-mcp[industrial]"`

---

## 60 秒で「異常検知 → LLM 説明」を動かす

```bash
pip install "llmesh-mcp[industrial]"
```

実機がなくても **シミュレーターで完結** します:

```python
import asyncio, random
from llmesh.industrial import SensorEvent, ExplainedCUSUM

# CUSUM だけ試す（LLM 説明は explainer=None でテンプレ fail-safe）
chart = ExplainedCUSUM(target=70.0, k=0.5, h=5.0, explainer=None)

async def run():
    for i in range(200):
        # 100 サンプル目から 5℃ 高い方にドリフトさせる
        value = 70.0 + (5.0 if i > 100 else 0) + random.gauss(0, 0.5)
        ev = SensorEvent(ts=i*0.1, sensor_id="bearing_temp_07",
                         sensor_type="temperature", value=value,
                         quality="good", meta={})
        report = chart.update(ev)
        if report:
            print(report.to_markdown()); break

asyncio.run(run())
```

CUSUM が立ち上がった時点で `IncidentReport`（Markdown）が出ます。
**LLM 説明** を有効にするには `explainer=` に backend を渡すだけです（後述）。

---

## 何を作ったか（先に結論）

- **20+ の産業プロトコル**（Modbus / Serial / OPC-UA / MQTT / EtherCAT / CAN / BACnet / DNP3 / IEC 61850 GOOSE / WebSocket / SNMP / SSH / Telnet / SFTP / IMAP / POP3 / FTP / SMTP / HTTP / TCP / UDP / ROS1 / ROS2）を **同一 ABC** で扱う
- 全部の入力を **`SensorEvent`** という 1 つのデータモデルに揃える
- **Mahalanobis-Taguchi 法 / Hotelling T² / CUSUM / Xbar-R** の多変量 SPC をかける
- 異常検出と同時に **LLM が原因仮説を Markdown / JSON で出力**（`ExplainedCUSUM`）
- **動画フレーム × 数値センサー** を時刻同期して 2 系統 CUSUM をかける（`VideoCUSUM`）
- 全部 **fail-closed**、**OWASP 静的監査クリーン**、**外部 DB 不要**（純 stdlib + numpy ベース）

---

## SensorEvent — 全プロトコル共通の入口

```python
@dataclass(frozen=True)
class SensorEvent:
    ts: float          # epoch 秒（NTP チェック済み）
    sensor_id: str
    sensor_type: str   # "temperature", "vibration", "pressure", ...
    value: float
    quality: str       # "good" / "uncertain" / "bad"
    meta: dict         # プロトコル固有の生情報
```

**プロトコルごとに別々の Event クラスを作らない** のが設計の肝です。SPC エンジン、ロガー、監査ログ、LLM 説明器がすべて同じ型に向き合えます。

```python
from llmesh.industrial import (
    ModbusAdapter, OPCUAAdapter, MQTTAdapter,
    DNP3Adapter, GOOSEAdapter,
)

modbus = ModbusAdapter(host="10.0.0.10", unit=1)
async for ev in modbus.stream():
    print(ev.sensor_type, ev.value, ev.quality)
```

`OPCUAAdapter` でも `DNP3Adapter` でも、yield されるのは **同じ `SensorEvent`** です。

---

## DNP3 / GOOSE — 電力系の重要プロトコルを安全に扱う

### DNP3Adapter（v2.14）

- **group code → sensor_type 変換テーブル** を内蔵（Analog Input / Binary Input …）
- ポイントの **allow-list 必須**（指定外は読まない）
- driver 注入で **ライブラリ非依存テスト** ができる（pydnp3 不在時は `connect()` で明示的 `RuntimeError`）

### GOOSEAdapter（IEC 61850）

- **純 stdlib 実装**（外部依存ゼロ）
- **`stNum` per-ref リプレイ防御**（GOOSE のリプレイ攻撃は本当に来る）
- **`MAX_DATASET_VALUES` ガード**（巨大データセットによる DoS 阻止）
- HIGH 優先度で `SensorEvent` を発行（運用側で優先度ベースのルーティングが書ける）

```python
from llmesh.industrial import GOOSEAdapter

goose = GOOSEAdapter(iface="eth1", allow_refs=["IED1/LLN0$GO$gcb01"])
async for ev in goose.stream():
    if ev.quality != "good":
        alert(ev)   # bad/uncertain は別経路へ
```

---

## 多変量 SPC — どれを使うか

| ツール | 何に使う | 計算特性 |
|---|---|---|
| `XbarRChart` | 個別変数の平均と範囲 | 古典 Shewhart |
| `CUSUMChart` | 微小ドリフトの早期検知 | 累積和、k/h パラメータ |
| `HotellingT²Chart` | **多変量の中心ずれ** | Tikhonov 正則化付き共分散 |
| `MTEngine` | Mahalanobis 距離（距離分類） | オフライン訓練 + リアルタイム推論 |
| `OnlineMTEngine` | 大バッチ Mahalanobis | einsum、`LLMESH_MT_ONLINE_MAX_BATCH_BYTES` でメモリ上限 |
| `EventDensityMap` | DVS イベント → 8×8 グリッド特徴 | カメラ系を SPC に乗せる前段 |
| `UnifiedSPC` | センサー × VLM テキストの 2 系統結合 SPC | AND / OR / Weighted |

**`OnlineMTEngine` のメモリ上限** は意外と効きます。1ms ごとに 1024 ch のセンサーを 100 並列で投げると簡単にメモリが破裂するので、env で上限を切れるようにしてあります。

---

## ExplainedCUSUM — 異常検出と同時に LLM が説明する

CUSUM が異常を吐いた **その瞬間に** 、LLM がコンテキスト（直近 N サンプル + メタ情報）を読んで原因仮説を Markdown / JSON で吐きます。

```python
from llmesh.industrial import ExplainedCUSUM

chart = ExplainedCUSUM(
    target=70.0,        # 想定平均（℃）
    k=0.5, h=5.0,       # CUSUM パラメータ
    explainer=llm_explainer,   # 任意の LLM backend
)

async for ev in opcua.stream():
    report = chart.update(ev)
    if report:
        print(report.to_markdown())
        save(report.to_json())
```

`IncidentReport` の中身（抜粋）:

```markdown
## Incident at 2026-05-09 03:22:11Z

- sensor: bearing_temp_07 (temperature)
- baseline: 70.0 °C / threshold h=5.0
- observed CUSUM: +9.4

### Hypothesis (LLM)
The cumulative drift began ~12 minutes prior, coinciding with a
viscosity drop in lubricant_flow_03. Bearing wear or lubricant
degradation is plausible. Consider checking lubricant pressure and
vibration spectrum for sub-resonant components.
```

LLM 説明は **オプショナル**（`explainer=None` ならテンプレートで fail-safe）。これも fail-closed の徹底です。

---

## VideoCUSUM — 動画 × 数値センサーを時刻で噛み合わせる

カメラと PLC は別ネットワーク・別タイムソースから来ます。LLMesh は **`sync_window_s` 既定 1.0 秒の bounded deque** でペア化してから 2 系統 CUSUM をかけます。

```python
from llmesh.industrial import VideoCUSUM, VLMFeatureExtractor

vlm = VLMFeatureExtractor(captioner=ollama_llava)   # 画像 → caption → 数値ベクトル
chart = VideoCUSUM(sync_window_s=1.0, vlm=vlm)

async for pair in chart.stream(video_iter, sensor_iter):
    if pair.alarm:
        report = pair.explain()  # 画像 + センサー両方の異常仮説
```

**`VLMFeatureExtractor` も fail-closed**：captioner が例外を投げたり、非文字列を返したら即 BLOCK（`ImageFirewall` ゲート経由）。

---

## SCADA × LLM の動線（全体図）

```
[現場]
  PLC ─Modbus──┐
  RTU ─DNP3 ───┤
  IED ─GOOSE ──┤   全部 SensorEvent に正規化
  Camera ─DVS ─┘
                │
                ▼
         ┌──────────────────────────┐
         │  SPC Engines             │
         │   CUSUM / Xbar-R         │
         │   Hotelling T²           │
         │   MT / OnlineMT          │
         │   UnifiedSPC (multi-modal)│
         └──────────┬───────────────┘
                    │
                    ▼
         ┌──────────────────────────┐
         │  ExplainedCUSUM          │
         │   ── LLM ──► IncidentReport
         └──────────┬───────────────┘
                    │  Markdown / JSON
                    ▼
            運用 / Slack / 監査ログ
```

---

## 信頼性プロトコル

長時間ストリームの再送・順序復元・切断検出を `MessageAssembler` + `ChunkSender` の組み合わせで保証します。

```
[正常完了]  受信: pop_completed() → STREAM_ACK 送信
            送信: handle_ack()    → 送信バッファ破棄

[欠落検出]  受信: check_timeouts() → RETRANSMIT 送信（1 回のみ）
            送信: handle_retransmit() → 欠落チャンクのみ再送

[切断検出]  受信: check_watchdog()  → True で切断シグナル
            送信: expire_old()      → TTL 超過バッファ自動破棄
```

クロックずれは `llmesh.security.clock` の **NTP チェック** が `SensorEvent.ts` を信用してよいかを判断します。タイムソースが信用できない時は `quality="uncertain"` として下流が選別できる設計です。

---

## CLI

```bash
python -m llmesh.cli.doctor   # 環境健全性チェック（プロトコル driver 有無、ポート、権限）
python -m llmesh.cli.status   # ランタイム状態（ノード ID、Capability、接続先）
python -m llmesh.cli.sbom     # CycloneDX SBOM 自動生成（供給連鎖監査）
```

`doctor` は **「動いていない理由を全部出す」** に振ってあります。現場の引き継ぎで一番効きます。

---

## ベンチマーク（Rust 拡張時）

| 操作 | Pure Python | Rust | 倍率 |
|------|-----------:|-----:|----:|
| PointCloud encode (1M) | 4.0M pts/s | **24.1M pts/s** | **6.0×** |
| PointCloud decode (1M) | 3.7M pts/s | 5.9M pts/s | 1.6× |
| DVS encode (1M) | 3.4M evt/s | 5.5M evt/s | 1.6× |
| Pipeline + CUSUM | 190K events/s | – | – |

Rust 拡張は **任意**。CI が **8 ターゲットの multi-platform wheel** を吐きます。

---

## 試す

```bash
pip install "llmesh-mcp[industrial,vision]"
python -m llmesh.cli.doctor
```

- GitHub: <https://github.com/furuse-kazufumi/llmesh>
- PyPI: <https://pypi.org/project/llmesh-mcp/>
- License: MIT

---

## おわりに

産業 IoT × LLM は **「現場の異常を、現場の言葉で、即時に、説明可能に」** がゴールです。
ベンダー固有のドライバを使うたびに `SensorEvent` 互換のラッパーを 50 行書けば、SPC も LLM 説明もそのまま乗ります。
DNP3 / GOOSE のような **電力系プロトコル** が同じ抽象に乗っているので、SCADA 案件にもそのまま投入できます。
