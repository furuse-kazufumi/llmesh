# Deployment Guide

> **かんたんな説明（中学生レベル）**
> これは LLMesh（自分のパソコンや工場の機械の中だけで動く AI のしくみ）を、
> 実際の現場に設置して動かすための手順書です。1 台だけの小さな置き方から、
> 同じネットワークにつないだ何台もの機械で力を合わせる置き方まで、必要な
> 部品・つなぎ方・安全のための鍵の守り方を順番に説明します。
>
> 用語がむずかしいときは [用語集（GLOSSARY.md）](GLOSSARY.md) を見てください。

---

LLMesh の本番デプロイガイド。エッジ単機から複数ノード swarm までを
カバーします。

---

## 1. デプロイモデル

| モデル | 用途 | 規模 |
|--------|------|------|
| **Edge / Single Node** | 工場ライン、医療端末、現場 | 1 機 |
| **Swarm（LAN）** | 同一 LAN 内の協調推論 | 2–32 機 |
| **Multi-Site** | 拠点間連携 | 数十拠点 |
| **MCP Sidecar** | Claude Code / 他 MCP クライアント連携 | 開発者ローカル |

---

## 2. システム要件

| 項目 | Edge | Swarm | Multi-Site |
|------|------|-------|-----------|
| CPU | 4 core | 8 core | 16 core |
| RAM | 8 GB | 16 GB | 32 GB |
| Disk | 10 GB | 50 GB | 200 GB |
| GPU | 任意 | 推奨（LLM 推論） | 必須 |
| ネットワーク | LAN | LAN（NTP 必須）| WAN + VPN |
| OS | Linux / Windows / macOS | Linux 推奨 | Linux |
| Python | 3.11+ | 3.11+ | 3.11+ |

---

## 3. インストール

### 標準（pip）

```bash
python -m venv /opt/llmesh
source /opt/llmesh/bin/activate
pip install --upgrade pip
pip install "llmesh[industrial,ssh,email,mgmt,vision,presidio,rag]"
```

### Docker

```dockerfile
FROM python:3.11-slim

# システム依存（pyserial / pysoem 等が要求）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libssl-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/llmesh
COPY pyproject.toml README.md /opt/llmesh/
COPY llmesh /opt/llmesh/llmesh
RUN pip install -e ".[industrial,ssh,email,mgmt,vision,rag]"

# 必要時のみ EtherCAT cap を付与
# RUN setcap cap_net_raw+ep $(which python3.11)

EXPOSE 8000
HEALTHCHECK CMD python -m llmesh.cli.doctor || exit 1

ENTRYPOINT ["python", "-m", "llmesh"]
CMD ["serve-mcp"]
```

### docker-compose（Swarm 例）

```yaml
version: "3.9"
services:
  rendezvous:
    image: llmesh:latest
    command: ["rendezvous-serve", "--port", "8765"]
    ports: ["8765:8765"]
    healthcheck:
      test: ["CMD", "python", "-m", "llmesh.cli.doctor"]
      interval: 30s

  node-a:
    image: llmesh:latest
    command: ["serve-mcp"]
    environment:
      LLMESH_BACKEND: ollama
      LLMESH_MODEL: llama3.2
      LLMESH_RENDEZVOUS_URL: http://rendezvous:8765
      LLMESH_NTP_SERVERS: pool.ntp.org
    depends_on: [rendezvous]

  node-b:
    image: llmesh:latest
    command: ["serve-mcp"]
    environment:
      LLMESH_BACKEND: ollama
      LLMESH_MODEL: llama3.2
      LLMESH_RENDEZVOUS_URL: http://rendezvous:8765
    depends_on: [rendezvous]
```

### systemd unit

```ini
# /etc/systemd/system/llmesh.service
[Unit]
Description=LLMesh node
After=network-online.target time-sync.target
Wants=network-online.target

[Service]
Type=simple
User=llmesh
Group=llmesh
WorkingDirectory=/opt/llmesh
Environment="LLMESH_BACKEND=ollama"
Environment="LLMESH_MODEL=llama3.2"
Environment="LLMESH_NTP_SERVERS=pool.ntp.org"
Environment="LLMESH_AUDIT_LOG_PATH=/var/log/llmesh/audit.jsonl"
Environment="LLMESH_AUDIT_HMAC_KEY_HEX=<32-byte hex>"
ExecStartPre=/usr/bin/timedatectl set-ntp true
ExecStart=/opt/llmesh/bin/python -m llmesh serve-mcp
Restart=on-failure
RestartSec=5
StartLimitBurst=3
StartLimitIntervalSec=120

# セキュリティ強化（systemd 247+）
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
NoNewPrivileges=yes
ReadWritePaths=/var/log/llmesh /var/lib/llmesh

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now llmesh
sudo journalctl -u llmesh -f
```

### Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: llmesh
spec:
  replicas: 3
  selector:
    matchLabels: {app: llmesh}
  template:
    metadata:
      labels: {app: llmesh}
    spec:
      containers:
        - name: llmesh
          image: llmesh:2.17.0
          args: ["serve-mcp"]
          env:
            - name: LLMESH_BACKEND
              value: ollama
            - name: LLMESH_AUDIT_HMAC_KEY_HEX
              valueFrom:
                secretKeyRef: {name: llmesh-audit-key, key: hex}
            - name: LLMESH_RENDEZVOUS_URL
              value: http://llmesh-rendezvous:8765
          ports: [{containerPort: 8000}]
          livenessProbe:
            exec:
              command: ["python", "-m", "llmesh.cli.doctor"]
            periodSeconds: 30
          readinessProbe:
            httpGet: {path: /healthz, port: 8000}
            periodSeconds: 10
          resources:
            requests: {cpu: "1000m", memory: "2Gi"}
            limits:   {cpu: "4000m", memory: "8Gi"}
---
apiVersion: v1
kind: Service
metadata: {name: llmesh}
spec:
  selector: {app: llmesh}
  ports: [{port: 8000}]
```

---

## 4. 設定

### 環境変数

| 変数 | デフォルト | 説明 |
|------|----------|------|
| `LLMESH_BACKEND` | `ollama` | `ollama` / `llamacpp` |
| `LLMESH_MODEL` | `llama3.2` | LLM モデル名 |
| `LLMESH_AUDIT_LOG_PATH` | `audit.jsonl` | 監査ログ出力先 |
| `LLMESH_AUDIT_HMAC_KEY_HEX` | （必須）| 32 byte 16進数 HMAC 鍵 |
| `LLMESH_NTP_SERVERS` | `pool.ntp.org` | カンマ区切り |
| `LLMESH_MAX_CLOCK_DRIFT_S` | `10` | NTP ドリフト閾値秒 |
| `LLMESH_RENDEZVOUS_URL` | （任意）| Rendezvous endpoint |
| `LLMESH_NONCE_DB_PATH` | `nonces.sqlite` | NonceStore DB |
| `LLMESH_TIMELINE_DB_PATH` | `timeline.sqlite` | TimelineStore DB |
| `LLMESH_MT_ONLINE_MAX_BATCH_BYTES` | `16777216` | OnlineMTEngine バッチ上限 |

### llmesh.toml

```toml
[node]
node_id = "factory-line-a-01"
endpoint = "https://10.0.0.5:8000"

[adapters]
http = {bind = "0.0.0.0:8000"}
modbus = {host = "10.0.0.10", port = 502, unit_id = 1}
opcua = {url = "opc.tcp://plc.local:4840"}
mqtt = {host = "mqtt.local", port = 1883, topic_prefix = "factory/"}

[security]
audit_log_path = "/var/log/llmesh/audit.jsonl"
audit_hmac_key_env = "LLMESH_AUDIT_HMAC_KEY_HEX"
max_clock_drift_s = 10
ntp_servers = ["pool.ntp.org", "time.cloudflare.com"]

[circuit_breaker]
fail_threshold = 5
half_open_after_s = 60

[industrial]
unit_space_dir = "/var/lib/llmesh/unit_spaces"
domain = "manufacturing"
```

詳細は [`SETUP_GUIDE.md`](SETUP_GUIDE.md) を参照。

---

## 5. シークレット管理

| シークレット | 推奨保管先 |
|-------------|----------|
| `LLMESH_AUDIT_HMAC_KEY_HEX` | Vault / k8s secret / systemd `LoadCredential` |
| Ed25519 private key | KMS / TPM / Secure Enclave |
| Ollama 接続トークン（任意）| 環境変数のみ |

**禁止**: 設定ファイル / コミット / 環境変数の平文保存。

---

## 6. ヘルスチェック

```bash
# CLI
python -m llmesh.cli.doctor

# HTTP（HttpAdapter 起動時）
curl http://localhost:8000/healthz   # → 200 {"status":"ok"}

# Audit chain 整合性
python -m llmesh audit verify /var/log/llmesh/audit.jsonl
```

---

## 7. リソース要件目安

| ワークロード | CPU | RAM | Disk I/O |
|-------------|-----|-----|---------|
| MCP serve（推論なし）| < 1 % | 256 MB | 0 |
| Modbus poll 1 Hz × 10 sensors | 2 % | 512 MB | 0.1 MB/h |
| OPC-UA subscribe 100 nodes | 5 % | 768 MB | 1 MB/h |
| LLM invoke（local Ollama）| 50–100 % per req | 4 GB | 0 |
| RAG search（10⁵ docs, dim=384）| 5 ms / query | 200 MB | 0 |

---

## 8. ネットワーク要件

### Inbound

| ポート | プロトコル | 用途 |
|--------|----------|------|
| 8000 | HTTPS | MCP HTTP API |
| 5683 | UDP | CoAP（任意） |
| 1883 | MQTT | sensor pub/sub |
| 502 | TCP | Modbus |
| 4840 | OPC-UA | factory PLC |
| 5353 | UDP | mDNS / DNS-SD |

### Outbound

| 宛先 | 用途 |
|------|------|
| `pool.ntp.org:123` | NTP（必須）|
| Ollama daemon (`localhost:11434`) | LLM 推論 |
| Rendezvous URL | ノード発見 |
| `api.osv.dev` | SCA Gate（任意）|

**SSRF 対策**: `EndpointValidator` がプライベート IP / IMDS をブロック
（`llmesh/security/endpoint_validator.py`）。

---

## 9. ロギング / 監視

詳細は [`OBSERVABILITY.md`](OBSERVABILITY.md) 参照。要点:

- 構造化ログ: JSONL（systemd journal / k8s log driver で自動収集）
- 監査ログ: tamper-evident HMAC チェーン
- Prometheus exporter: `IndustrialMetrics`（カーディナリティ ≤ 100k）
- OpenTelemetry トレース: `IndustrialTracer`

---

## 10. バックアップ / 災害復旧

| 対象 | 頻度 | 方法 |
|------|------|------|
| 監査ログ（audit.jsonl）| 毎時 | append-only コピー |
| NonceStore DB | 毎時 | sqlite `.backup` |
| Timeline DB | 毎時 | 同上 |
| MT-method unit space | 学習毎 | `.npz` を S3 / Azure Blob |
| RAG ベクトルストア | 日次 | `.npz` / `.sqlite` |

復旧手順:
1. 設定 + シークレットを復元
2. `LLMESH_AUDIT_LOG_PATH` を直近のバックアップに向ける
3. `python -m llmesh audit verify <log>` で整合性確認
4. 起動

---

## 11. 段階的ロールアウト

```bash
# Blue-green デプロイ（k8s 例）
kubectl set image deployment/llmesh-blue llmesh=llmesh:2.17.0
kubectl rollout status deployment/llmesh-blue
# トラフィックを 10 → 50 → 100 % で切替（Service / Ingress 側で）
```

---

## 12. パフォーマンスチューニング

[`PERFORMANCE.md`](PERFORMANCE.md) の推奨パラメータ表に従い、用途別に
バックエンドを選定。
