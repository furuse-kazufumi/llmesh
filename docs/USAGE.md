# LLMesh 使い方ガイド

LLMesh の **5 分クイックスタート** から **本番運用** までを 1 ページにまとめた実践ガイドです。
詳細仕様は [`SPECIFICATION.md`](SPECIFICATION.md)、各機能の網羅は [`INDUSTRIAL_GUIDE.md`](INDUSTRIAL_GUIDE.md) を参照。

---

## 1. インストール

```bash
# 最小（HTTP/MCP のみ）
pip install llmesh

# 産業用（Modbus/OPC-UA/MQTT/3D 全部）
pip install "llmesh[industrial]"

# Linux で EtherCAT も使う場合
pip install "llmesh[industrial,ethercat]"

# v2.13+ 強化機能
pip install "llmesh[presidio]"   # PII 検出（Layer 1.5）
pip install "llmesh[rag]"        # RAG ベクトル検索（numpy）
# v2.14+ 強化機能
pip install "llmesh[dnp3]"       # DNP3 outstation client（pydnp3）
pip install "llmesh[vlm]"        # VLMFeatureExtractor 高度モード（Pillow）

# 開発（pytest, ruff, bandit, hypothesis, coverage）
pip install "llmesh[dev]"
```

## 2. Claude Code との連携（5 分）

`~/.claude.json` に以下を追加してから Claude Code を再起動:

```json
{
  "mcpServers": {
    "llmesh": {
      "command": "python",
      "args": ["-m", "llmesh", "serve-mcp"],
      "env": {
        "LLMESH_BACKEND": "ollama",
        "LLMESH_MODEL": "llama3.2"
      }
    }
  }
}
```

これだけで Claude Code 内に下記ツールが追加されます:
- `generate_code` / `review_code` / `explain_code` / `suggest_tests`

すべて **PromptFirewall → PrivacySummarizer → 監査ログ** を経由するため、
PII / 秘密が誤って LLM へ流れることはありません。

## 3. 産業用センサーから LLM 診断まで（10 分）

```python
import asyncio, struct
from llmesh.industrial import (
    SensorEvent, IndustrialPipeline, IndustrialMetrics,
    TenantScope, ModbusAdapter, RegisterType,
)

async def main():
    pipeline = IndustrialPipeline()

    # CUSUM ドリフト検知
    pipeline.attach_cusum(
        sensor_id="line_a/pressure_01",
        target=101_325.0, k=0.5, h=4.0, sigma=50.0,
    )

    # Prometheus メトリクス（外部依存ゼロ）
    metrics = IndustrialMetrics()
    await metrics.serve_http("127.0.0.1", 9100)

    # マルチテナント分離
    tenant = TenantScope("factory_a", allow_sensor_prefixes={"line_a/"})

    pipeline.on_diagnosis(tenant.wrap_callback(
        lambda d: print(d.to_prompt_text())
    ))

    # Modbus アダプターを起動
    modbus = ModbusAdapter.tcp("192.168.1.10", 502)
    modbus.add_register(
        slave_id=1, address=0x0000, count=2,
        sensor_id="line_a/pressure_01",
        sensor_type="pressure", unit="Pa",
    )
    modbus.on_event(pipeline.process)
    await modbus.start()

    try:
        await asyncio.sleep(3600)
    finally:
        await modbus.stop()
        await metrics.stop_http()

asyncio.run(main())
```

## 4. デモを動かす（30 秒）

実機なしで全フェーズの動作を確認:

```bash
python examples/industrial_demo.py
```

別ターミナルで:

```bash
curl http://127.0.0.1:9100/metrics
```

ドリフトが Step 60 で発生し、CUSUM が WARNING を出力する様子が観察できます。

## 5. プライバシーパイプラインの使い方

3D センサー（カメラ）の生ピクセルを LLM へ送らないために:

```python
from llmesh.industrial.sensor_3d import SpatialSummarizer
from llmesh.privacy import PromptFirewall

summarizer = SpatialSummarizer()
firewall = PromptFirewall()

def safe_forward_to_llm(sensor_event):
    text = summarizer.summarize(sensor_event)   # ピクセル → テキスト
    safe = firewall.scrub(text)                 # PII 除去
    response = llm_backend.generate(safe)
    return response
```

## 5.5 v2.13+ 強化機能

### Presidio PII 検出（Layer 1.5）

```python
from llmesh.privacy import PromptFirewall, PresidioDetector

# Presidio を有効化（presidio-analyzer 未インストールでも no-op で安全）
detector = PresidioDetector()
firewall = PromptFirewall(presidio=detector)

decision = firewall.classify("My SSN is 123-45-6789")
# decision.action == "BLOCK"  (US_SSN は既定 BLOCK エンティティ)

decision = firewall.classify("Contact me at alice@example.com")
# decision.action == "SUMMARIZE"  (EMAIL_ADDRESS は SUMMARIZE)
```

エンティティ分類はカスタマイズ可能:

```python
detector = PresidioDetector(
    block_entities={"CREDIT_CARD", "US_SSN", "MY_INTERNAL_TAG"},
    summarize_entities={"PERSON", "EMAIL_ADDRESS"},
    score_threshold=0.6,
)
```

### RAG（ローカルベクトル検索）

```python
from llmesh.privacy import PromptFirewall
from llmesh.rag import MockEmbedder, NumpyVectorStore, Retriever

embedder = MockEmbedder(dimension=64)        # 本番は OllamaEmbedder
store    = NumpyVectorStore(dimension=64)
retriever = Retriever(store=store, embedder=embedder, firewall=PromptFirewall())

# Index — Layer 0 注入や L4 シークレット混入は自動拒否
retriever.index("doc1", "Implementing bounded retry helpers in Python")
retriever.index("doc2", "Modbus TCP register layout for SMT pick-and-place")

# Search
for hit in retriever.retrieve("how do I retry safely?", top_k=3):
    if hit.allowed:
        print(hit.document.text, hit.score)
    elif hit.requires_summarization:
        print("[needs summarizer]", hit.document.text)
```

`.npz` への永続化:

```python
store.save("/var/lib/llmesh/vectors.npz")
restored = NumpyVectorStore.load("/var/lib/llmesh/vectors.npz")
```

### Industrial v3-N7 / N11 / N15

#### LLMExplainer（説明可能 SCADA）

```python
from llmesh.industrial.explainer import AlarmEvent, LLMExplainer

ex = LLMExplainer()  # LLM optional
report = ex.explain(AlarmEvent(
    incident_id="INC-001",
    timestamp="2026-05-08T10:30:00Z",
    sensor_id="dnp3:plant_a:01",
    statistic=4.7,
    threshold=3.0,
    metric="mahalanobis",
    contributing_dims=("temp_in", "vibration_z"),
))
print(report.markdown)   # Markdown レポート
print(report.payload)    # JSON シリアライズ可能 dict
```

LLM を組合せる場合:

```python
from llmesh.llm.ollama import OllamaBackend
backend = OllamaBackend(model="llama3.2")

def llm_call(prompt: str) -> str:
    return backend.invoke("explain", {"prompt": prompt}).get("text", "")

ex = LLMExplainer(llm=llm_call)
```

#### OnlineMTEngine（µs オーダー異常検知）

```python
from llmesh.industrial.mt_engine import MTEngine
from llmesh.industrial.mt_online import OnlineMTEngine

eng = MTEngine.load("unit_space.npz")
online = OnlineMTEngine(eng, threshold=3.0)
result = online.score_batch(batch)   # batch: shape (n, p)
print(result.distances, result.anomalies)
```

#### HotellingT2Chart（多変量管理図）

```python
from llmesh.industrial.hotelling_t2 import HotellingT2Chart
chart = HotellingT2Chart().fit(reference_2d_array)
verdict = chart.score(new_observation)   # T2Decision
```

#### EventDensityMap（DVS → SPC 入力）

```python
from llmesh.industrial.event_density_map import EventDensityMap
m = EventDensityMap(sensor_w=346, sensor_h=260, grid_w=8, grid_h=8)
feature = m.aggregate(events_array)
# feature.vector を Hotelling T² や OnlineMTEngine に投入
```

#### UnifiedSPC（マルチモーダル品質管理）

```python
from llmesh.industrial.spc_engine import XbarRChart
from llmesh.industrial.multimodal_spc import UnifiedSPC

sensor = XbarRChart().fit(sensor_baseline)
text   = XbarRChart().fit(vlm_baseline)
spc = UnifiedSPC(sensor, text, mode="weighted",
                 sensor_weight=0.6, text_weight=0.6, threshold=0.5)
out = spc.update(sensor_subgroup, vlm_subgroup)
if not out.in_control:
    print("violations:", out.violations)
```

## 5.6 v2.14+ 拡張機能

### ExplainedCUSUM（自己説明 CUSUM 管理図）

```python
from llmesh.industrial.spc_engine import CUSUMChart
from llmesh.industrial.explained_cusum import ExplainedCUSUM
from llmesh.industrial.explainer import LLMExplainer

chart = CUSUMChart(target=0.0, k=0.5, h=4.0)
ec = ExplainedCUSUM(
    chart,
    sensor_id="dnp3:plant_a:01",
    contributing_dims=("temp_in", "vibration_z"),
    explainer=LLMExplainer(),
)
out = ec.update(value)
if not out.in_control:
    print(out.report.markdown)   # IncidentReport (Markdown)
    print(out.incident_id)       # uuid hex
```

### VideoCUSUM（動画 + センサー時刻同期 CUSUM）

```python
from llmesh.industrial.spc_engine import CUSUMChart
from llmesh.industrial.video_cusum import VideoCUSUM

frame  = CUSUMChart(target=0.0, k=0.1, h=0.5)
sensor = CUSUMChart(target=0.0, k=0.1, h=0.5)
vc = VideoCUSUM(frame, sensor, sync_window_s=1.0)

# Frame 由来特徴 (VLMFeatureExtractor 出力など) と センサー値を
# それぞれタイムスタンプ付きで投入
vc.ingest_frame(t=10.0, value=1.2)
out = vc.ingest_sensor(t=10.3, value=1.1)
if out.synced_alarm:
    print("paired with", out.paired_with)
```

### VLMFeatureExtractor（画像 → 特徴ベクトル）

```python
from llmesh.privacy.image_firewall import ImageFirewall
from llmesh.industrial.vlm_feature_extractor import VLMFeatureExtractor

ex = VLMFeatureExtractor(
    image_firewall=ImageFirewall().classify,   # callable インターフェース
    dimension=16,
)
feature = ex.extract(image_bytes)
if feature.allowed:
    spc.update(sensor_subgroup, list(feature.vector[:3]))   # UnifiedSPC へ
```

### SqliteVectorStore（永続ベクトルストア — 純 stdlib）

```python
from llmesh.rag import Retriever, MockEmbedder, SqliteVectorStore
from llmesh.privacy import PromptFirewall

store = SqliteVectorStore("/var/lib/llmesh/vec.sqlite", dimension=64)
retriever = Retriever(store=store, embedder=MockEmbedder(64),
                      firewall=PromptFirewall())
retriever.index("doc1", "Implementing bounded retry helpers")
# プロセス再起動後も同じパスを開けば検索可能
store.close()
```

### DNP3Adapter（v3-N7 / K-1.1 — SCADA outstation）

```python
from llmesh.industrial.dnp3_adapter import DNP3Adapter

adapter = DNP3Adapter(
    "10.0.0.5", 20000,
    master_addr=1, outstation_addr=10,
    allow_addresses=[(1, 10)],
    device_id="plant_a",
)
adapter.on_event(lambda ev: print(ev.sensor_id, ev.payload))
adapter.connect()        # pydnp3 必須（pip install llmesh[dnp3]）
events = adapter.poll()
```

### GOOSEAdapter（IEC 61850 — variable-substation）

```python
from llmesh.industrial.goose_adapter import GOOSEAdapter, GoosePDU, GooseTransport

class MyTransport(GooseTransport):
    def recv(self):
        # libiec61850 / scapy / pcap_file から PDU を取り出す実装
        ...

adapter = GOOSEAdapter(
    transport=MyTransport(),
    allow_iedids=["IED1/LLN0$GO$gcb01"],
    device_id="substation_a",
)
events = adapter.drain()  # pending PDU をすべて取り込み
```

## 6. テスト・静的解析・セキュリティ

```bash
# 全テスト（hypothesis ベースの property 検証も含む）
pytest -q

# 静的解析
ruff check llmesh/

# セキュリティスキャン
bandit -r llmesh/ -ll

# カバレッジ
coverage run -m pytest && coverage report
```

## 7. PyPI へのリリース

```bash
python -m build
python -m twine upload dist/*
```

## 8. トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| `RuntimeError: pysoem is not installed` | EtherCAT extra 未インストール | `pip install llmesh[ethercat]`（Linux のみ） |
| `RuntimeError: asyncua is not installed` | OPC-UA 用ライブラリ未導入 | `pip install llmesh[industrial]` |
| `ValueError: invalid metric name` | Prometheus 命名規則違反 | `[a-zA-Z_:][a-zA-Z_0-9:]*` に修正 |
| `RuntimeError: cardinality limit reached` | ラベル組み合わせが 100k 超 | ラベルを統合してカーディナリティ削減 |
| AOI 画像が処理されない | 書き込み中ファイル | ファイルが完全に書き終わるまで待機（自動検知） |

## 9. 推奨運用パターン

- **ローカル LLM (Ollama / LlamaCpp)** を使う — クラウド送信ゼロ
- **TenantScope** でテナント分離 — 顧客ごとの誤配信防止
- **IndustrialMetrics + Prometheus + Grafana** で観測性確保
- **`llmesh audit verify`** で監査チェーンを定期検証
- **NTP 同期必須**（`LLMESH_MAX_CLOCK_DRIFT_S=10`）— リプレイ攻撃対策

## 10. 関連リンク

- 詳細仕様: [`SPECIFICATION.md`](SPECIFICATION.md)
- 産業ガイド: [`INDUSTRIAL_GUIDE.md`](INDUSTRIAL_GUIDE.md)
- 要件定義: [`REQUIREMENTS.md`](REQUIREMENTS.md)
- 変更履歴: [`CHANGELOG.md`](CHANGELOG.md)
- ロードマップ: [`ROADMAP.md`](ROADMAP.md)
- セキュリティ: [`SECURITY.md`](SECURITY.md)
- アーキテクチャ: [`ARCHITECTURE.md`](ARCHITECTURE.md)
