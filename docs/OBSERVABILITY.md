# Observability Guide

> **かみ砕いた説明（中学生にもわかるように）**
>
> この文書は「LLMesh が今ちゃんと動いているか」を見張るしくみの話です。体温や脈をはかるように、システムの速さ・エラー・あやしい動きを数字や記録として集めておきます。何かおかしくなったとき、その記録をたどれば「いつ・どこで・なぜ」こわれたかを後から調べられます。さらに記録そのものが書きかえられていないかも確認できるようにしてあります。
>
> 用語の意味は [用語集（GLOSSARY.md）](GLOSSARY.md) を参照してください。

LLMesh の可観測性（observability）（監視 / ログ / トレース / 監査）の構成と運用です。

---

## 1. 三本柱

| 柱 | 目的 | 主モジュール |
|----|------|------------|
| **メトリクス** | リアルタイム数値、SLO | `llmesh.industrial.metrics.IndustrialMetrics` |
| **ログ** | 詳細イベント、トラブルシュート | 標準 `logging` + 構造化出力 |
| **トレース** | 分散リクエストの flow | `llmesh.industrial.tracing.IndustrialTracer` |

加えて LLMesh 固有:

| 補助 | 目的 |
|------|------|
| **AuditTrace** | tamper-evident HMAC チェーン（コンプライアンス用）|
| **TimelineStore** | per-task lifecycle（再開可能なタスク管理）|

---

## 2. ログ（標準 logging）

### 推奨設定

```python
# 起動時に設定
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","logger":"%(name)s","level":"%(levelname)s","msg":%(message)s}',
    stream=sys.stdout,
)

# モジュール別に粒度調整
logging.getLogger("llmesh.privacy").setLevel(logging.WARNING)
logging.getLogger("llmesh.industrial").setLevel(logging.INFO)
logging.getLogger("llmesh.industrial.dnp3_adapter").setLevel(logging.DEBUG)
```

### 主要ロガー

| ロガー名 | 主なイベント |
|---------|-------------|
| `llmesh.privacy.firewall` | 注入検出、シークレット検出、L4 ブロック |
| `llmesh.industrial.dnp3_adapter` | callback 例外（v2.16+ で可視化）|
| `llmesh.industrial.goose_adapter` | リプレイ拒否、callback 例外（v2.16+）|
| `llmesh.industrial.websocket_adapter` | handshake 失敗 |
| `llmesh.security.cross_protocol` | クロスプロトコル nonce 衝突 |
| `llmesh.discovery.gossip` | peer 不到達、レスポンス過大 |
| `llmesh.audit.trace` | 監査 append、verify 結果 |

### 集約

- **systemd-journald**: `journalctl -u llmesh -o json --since "1 hour ago"`
- **filebeat / vector / fluentbit**: stdout / 監査 JSONL を取り込み
- **Loki / ELK / Splunk**: 構造化されたままインデックス

---

## 3. メトリクス（Prometheus 互換）

### IndustrialMetrics

```python
from llmesh.industrial.metrics import IndustrialMetrics

metrics = IndustrialMetrics(
    namespace="llmesh",
    cardinality_limit=100_000,
)

# カウンター
metrics.counter("sensor_events_total").inc(labels={"sensor_type": "pressure"})

# ヒストグラム
metrics.histogram("inference_latency_seconds").observe(0.123, labels={"tool": "review_code"})

# ゲージ
metrics.gauge("active_connections").set(42)
```

### 提供メトリクス

| 名前 | 種別 | ラベル |
|------|------|--------|
| `llmesh_requests_total` | counter | `tool`, `status` |
| `llmesh_firewall_decisions_total` | counter | `action` (`ALLOW`/`SUMMARIZE`/`BLOCK`), `layer` |
| `llmesh_inference_latency_seconds` | histogram | `tool`, `backend` |
| `llmesh_sensor_events_total` | counter | `protocol`, `sensor_type` |
| `llmesh_anomalies_detected_total` | counter | `engine` (`mt`, `xbar_r`, `cusum`, `t2`), `sensor_id` |
| `llmesh_audit_entries_total` | counter | `event_type` |
| `llmesh_circuit_breaker_state` | gauge | `node_id`, `state` |
| `llmesh_clock_drift_seconds` | gauge | `ntp_server` |
| `llmesh_rag_searches_total` | counter | `backend`, `outcome` |
| `llmesh_http_response_too_large_total` | counter | `client` (v2.17+) |

### Prometheus 設定例

```yaml
scrape_configs:
  - job_name: llmesh
    static_configs:
      - targets: ['llmesh-node-01:9100', 'llmesh-node-02:9100']
    metric_relabel_configs:
      - source_labels: [__name__]
        regex: 'llmesh_.*'
        action: keep
```

### Grafana ダッシュボード KPI

- **健全性**: `llmesh_clock_drift_seconds` < 5 / `up` == 1
- **セキュリティ**: `rate(llmesh_firewall_decisions_total{action="BLOCK"}[5m])` の異常スパイク
- **パフォーマンス**: `histogram_quantile(0.95, llmesh_inference_latency_seconds_bucket)`
- **異常検知**: `rate(llmesh_anomalies_detected_total[1m])` per sensor_id

---

## 4. トレース（OpenTelemetry 互換）

### IndustrialTracer

```python
from llmesh.industrial.tracing import IndustrialTracer

tracer = IndustrialTracer(service_name="llmesh-edge-01")

with tracer.start_as_current_span("process_event") as span:
    span.set_attribute("sensor_id", "modbus:01:0")
    span.set_attribute("priority", "high")
    # ...
    with tracer.start_as_current_span("mt_inference") as child:
        child.set_attribute("md", 4.2)
```

### スパン構造

```
span: receive_event (root)
├── span: prompt_firewall
│   └── attribute: layer, action, reason
├── span: privacy_summarizer (if SUMMARIZE)
├── span: llm_inference
│   ├── attribute: backend, model, tokens_in, tokens_out
│   └── span: ollama_http_call
└── span: output_validator
```

### Backend 設定

OTLP / Jaeger / Zipkin の任意 OTel exporter を attach 可能。詳細は
opentelemetry-python ドキュメント参照。

---

## 5. AuditTrace（コンプライアンス監査）

### 仕組み

- JSONL 形式 append-only、各エントリは前エントリの SHA-256 ハッシュを
  含む HMAC チェーン
- 改ざん（差し替え / 削除 / 順序変更）が `verify_chain` で検出

### 利用例

```python
from llmesh.audit import AuditTrace

trace = AuditTrace(
    log_path="/var/log/llmesh/audit.jsonl",
    hmac_key=bytes.fromhex(os.environ["LLMESH_AUDIT_HMAC_KEY_HEX"]),
)

trace.log(
    event_type="firewall_block",
    node_id="edge-01",
    task_id="t-12345",
    policy_decision="BLOCK",
    output_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
    data_level=4,
)

# 起動時 / 定期に検証
result = AuditTrace.verify_chain_detailed("/var/log/llmesh/audit.jsonl", hmac_key)
assert result.valid, f"audit chain invalid at seq {result.first_error_seq}"
```

### CLI

```bash
python -m llmesh audit verify /var/log/llmesh/audit.jsonl
# OK  entries=12345  file=/var/log/llmesh/audit.jsonl
```

### 推奨ローテーション

```bash
# 毎日 00:00 にローテート
mv /var/log/llmesh/audit.jsonl /var/log/llmesh/audit-$(date +%F).jsonl
# 旧ファイルの整合性検証 + 圧縮 + S3 アーカイブ
python -m llmesh audit verify /var/log/llmesh/audit-$(date +%F).jsonl \
  && gzip /var/log/llmesh/audit-$(date +%F).jsonl \
  && aws s3 cp /var/log/llmesh/audit-$(date +%F).jsonl.gz s3://compliance/llmesh/
```

---

## 6. TimelineStore（タスクライフサイクル）

タスク単位の状態遷移を per-task で保持。再開可能タスク（ネットワーク
障害で中断したリクエストなど）の特定に使用。

```bash
# 一覧
python -m llmesh timeline show --db /var/lib/llmesh/timeline.sqlite --limit 50

# タスク別
python -m llmesh timeline task <task_id> --db /var/lib/llmesh/timeline.sqlite

# 再開可能（最終イベントが terminal でない）
python -m llmesh timeline resumable --db /var/lib/llmesh/timeline.sqlite
```

---

## 7. SLO 例

### 推奨 SLO（Edge 単機）

| 指標 | 目標 | 対応メトリクス |
|------|------|--------------|
| 可用性 | 99.5 % / month | `up` + healthcheck |
| Inference P95 latency | ≤ 5 s | `llmesh_inference_latency_seconds` |
| Firewall BLOCK 率 | < 5 % | `llmesh_firewall_decisions_total{action="BLOCK"}` |
| 監査チェーン整合性 | 100 % | 日次 `verify_chain` |
| Clock drift | < 5 s | `llmesh_clock_drift_seconds` |

### Burn-rate アラート例（Prometheus）

```yaml
- alert: LLMeshFirewallBlockRateHigh
  expr: |
    rate(llmesh_firewall_decisions_total{action="BLOCK"}[5m])
      / rate(llmesh_firewall_decisions_total[5m]) > 0.10
  for: 10m
  labels: {severity: warning}

- alert: LLMeshAuditChainBroken
  expr: llmesh_audit_chain_valid == 0
  for: 1m
  labels: {severity: critical}

- alert: LLMeshClockDriftHigh
  expr: llmesh_clock_drift_seconds > 10
  for: 5m
  labels: {severity: warning}
```

---

## 8. ダッシュボード推奨レイアウト

### Row 1: 健全性
- service `up`、healthcheck ratio、clock drift

### Row 2: セキュリティ
- firewall decisions（stacked area: ALLOW / SUMMARIZE / BLOCK）
- 注入検出率、PII 検出率

### Row 3: パフォーマンス
- inference latency P50 / P95 / P99
- backend health（Ollama / llama.cpp）
- request throughput

### Row 4: 産業
- sensor events / sec by protocol
- anomalies detected by engine
- circuit breaker state

### Row 5: 観測性自身
- audit log size growth
- timeline DB size
- HTTP response_too_large rate

---

## 9. プライバシー考慮

- ログにプロンプト本文を含めない（`firewall.py` は SHA-256 のみ記録）
- AuditTrace のメタデータも sensitive 情報を含めない
- Grafana / Prometheus へ流す前に `data_level` ラベルでフィルタ
- 顧客テナント分離: `TenantScope`（`llmesh/industrial/tenant.py`）で
  メトリクスにテナント ID をラベル付与

---

## 10. 障害切り分けクックブック

### 「inference latency が突然上がった」
1. `llmesh_inference_latency_seconds` で外れ値を特定（tool / backend）
2. backend `up` を確認（Ollama 落ちていないか）
3. `circuit_breaker_state` が open になっていないか
4. NTP drift が原因なら時計同期再起動

### 「firewall_block が急増」
1. `layer` ラベルで原因を特定（Layer 0 / 1 / 1.5 / 2）
2. Layer 0 急増 → 攻撃か aggressive prompt
3. Layer 1.5 急増 → Presidio が新パターンに反応（false positive 確認）
4. Audit log で `output_sha256` を逆引き調査（プロンプト本文は別途保管）

### 「監査ログが verify FAIL」
1. `first_error_seq` を確認
2. 該当エントリの前後を別ストレージにバックアップ
3. インシデント対応プロセス起動（改ざん疑い）
4. 該当ノードを隔離、新しい HMAC 鍵で再起動
