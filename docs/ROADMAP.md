# LLMesh Roadmap — v0.2.0 → v2.0.0

## Vision

LLMesh evolves from an HTTP/MCP-only local LLM mesh into a **multi-protocol LLM
gateway** that can receive task requests and deliver responses over any standard
network protocol. Every protocol adapter is subject to the same privacy pipeline
(PromptFirewall → PrivacySummarizer → LLM backend → OutputValidator) and the
same identity/audit infrastructure introduced in v0.2.0.

From v1.3.0, LLMesh targets **manufacturing / industrial** deployments: sensor
data from Modbus, OPC-UA, serial, and EtherCAT devices feeds local LLM swarms for
predictive maintenance, anomaly detection, and natural-language diagnostics —
all without cloud egress.

---

## Industrial Roadmap (v1.3.0 → v2.0.0)

| Version | Phase | Content |
|---------|-------|---------|
| **v1.3.0** | A — Foundation | `SensorEvent` unified model, `IndustrialConfig`, `llmesh configure` wizard ✓ |
| **v1.4.0** | B — Field Protocols | `ModbusAdapter` (TCP/RTU), `SerialAdapter` (RS-232/485) ✓ |
| **v1.5.0** | C — Analysis Engines | MT-method engine (offline training + real-time MD), SPC (Xbar-R, CUSUM) ✓ |
| **v1.6.0** | D — OPC-UA + MQTT | `OPCUAAdapter` (asyncua), `MQTTAdapter` (paho-mqtt) ✓ |
| **v1.7.0** | E — 3D Sensor Integration | mcp-3d SDK integration (AOI / depth camera / event camera) ✓ |
| **v1.8.0** | F — EtherCAT | `EtherCATAdapter` (Linux / SOEM; hardware-dependent) ✓ |
| **v2.0.0** | G — Full Release | Integrated industrial swarm (`IndustrialPipeline`), paper-ready, PyPI release ✓ |
| **v2.0.1** | Patch | hypothesis property-based + atomic write 検出 + メモリリーク対策 ✓ |
| **v2.1.0** | v3 Phase 1 | CANAdapter / IndustrialMetrics / IndustrialTracer / TenantScope ✓ |
| **v2.2.0** | v3 Phase 2 | IndustrialAdapter Protocol / E2E 統合テスト 9 件 / Volume D + E 要件定義 ✓ |
| **v2.3.0** | 論文化基盤 | 精密工学会 4 論文素材 + 合成データ生成 + 画像論文 RAD コーパス + CI 強化 ✓ |
| **v2.4.0** | Volume K + BACnet | 重要インフラ要件 20 章 + BACnetAdapter（K-10.1）✓ |
| **v2.5.0** | Rust 拡張 | PointCloud encode 6× 高速化、wheel multi-platform ✓ |
| **v2.6.0** | Multi-platform + RTOS | 8 ターゲット wheel CI + EdgeProfile + C ABI（Volume L）✓ |
| **v2.7.0** | DevEx | CLI（doctor/status/sbom）+ デバッグ機能 + 8 分野 RAD ✓ |
| **v2.8.0** | 大量論文収集 | OpenAlex/arXiv/S2/CrossRef/DBLP/PubMed/HN 統合（21 万件目標）✓ |
| **v2.9.0** | 先端 AI/量子 RAD | DL/NN/LLM/VLM/Quantum/Diffusion/Agents 7 分野追加 ✓ |
| **v2.10.0** | 数学 RAD | 多変量/統計/最適化/数値/情報理論 5 分野（21 分野体制）✓ |
| **v2.11.0** | WebSocket + Volume N | WebSocketAdapter（J-4.3）+ 学際横断融合 15 テーマ（RAD 駆動）✓ |
| **v2.12.0** | WebSocket fix + Volume N 整理 + v3 Plan | RFC 6455 handshake 検証修正 + Volume N の Research Backlog 化 + A 分類 3 テーマ（N-7 / N-11 / N-15）の v3 Implementation Plan 詳述 ✓ |
| **v2.13.0** | E-2.1 + F-1 + v3-N7/N11/N15 コア | Presidio PII 検出（Layer 1.5）+ RAG MVP（Embedder/VectorStore/Retriever）+ OnlineMTEngine + HotellingT2Chart + EventDensityMap + UnifiedSPC + LLMExplainer ✓ |
| **v2.14.0** | v3 拡張（説明可能 + 同期 + VLM + DNP3 + GOOSE + Sqlite RAG） | ExplainedCUSUM + VideoCUSUM + VLMFeatureExtractor + SqliteVectorStore + DNP3Adapter（skeleton）+ GOOSEAdapter（skeleton） ✓ |
| **v2.15.0** | LSH ANN + E2E + API stability + Performance docs | LSHVectorStore（ANN） + v3 横断 5 件 E2E テスト + 公開 API レイヤー（`__all__` 明示）+ docs/API_STABILITY.md + docs/PERFORMANCE.md ✓ |
| **v2.16.0** | Security review + RCE risk removal | `.npz` の `allow_pickle=False` 化（numpy/lsh 両ストア）+ DNP3/GOOSE callback ログ可視化 + Sqlite 親ディレクトリ自動作成 + Pillow thumbnail 最適化 + Ollama レスポンスサイズ上限 + 全体 OWASP 静的監査 ✓ |
| **v2.17.0** | HTTP response size caps（DoS hardening）| `llmesh.security.http_limits` モジュール新設 + 全 8 箇所の `resp.read()` 無制限読込を `read_capped` に置換（用途別キャップ：1MiB/16MiB/256KiB/64KiB/4MiB）✓ |
| **v2.18.0** | ドキュメント整備（v3.0 切替前の最終整備）| CONTRIBUTING + DEVELOPMENT + TROUBLESHOOTING + MIGRATION + DEPLOYMENT + OBSERVABILITY + TESTING + GLOSSARY 計 8 種新規 + README 再構成 ✓ |
| **v3.0.0** | **API Stability Release（SemVer 正式適用開始）**| 機能変更ゼロの安定性宣言。`__all__` + `API_STABILITY.md` を契約化。Deprecation policy を minor 警告→major 削除に厳格化。OWASP クリーン + 全テスト PASS で出発 ✓ |
| **v3.1.0** | クラウド / ホステッド LLM 統合（F-6） | `OpenAICompatibleBackend`（OpenAI / Azure / OpenRouter / Together / Groq / Mistral / DeepSeek 7 プロバイダ）+ `AnthropicBackend`（Claude Messages API）。urllib のみで追加依存ゼロ ✓ |

### v3 残ロードマップ（Volume C-N は REQUIREMENTS.md 参照）

#### v3 コア完了（v2.13.0 〜 v2.14.0）
- ✓ E-2.1 Microsoft Presidio 統合（Layer 1.5、Optional extras）
- ✓ F-1 RAG MVP（Embedder / VectorStore / Retriever — numpy + sqlite 二系統）
- ✓ F-1.1 SqliteVectorStore（純 stdlib 永続バックエンド）
- ✓ v3-N7 LLMExplainer + ExplainedCUSUM
- ✓ v3-N7 DNP3Adapter（skeleton — pydnp3 optional）
- ✓ v3-N7 GOOSEAdapter（IEC 61850 — transport 注入で完全テスト可能）
- ✓ v3-N11 OnlineMTEngine + HotellingT2Chart + EventDensityMap
- ✓ v3-N15 UnifiedSPC + VideoCUSUM + VLMFeatureExtractor

#### v3 拡張（v2.15 以降、外部依存実機化）
- ★★ DNP3Adapter 実環境統合（pydnp3 ベース wire-protocol レイヤー）
- ★★ GOOSEAdapter 実環境統合（libiec61850 / scapy ベース raw socket）
- ★★ VLMFeatureExtractor 本番化（Ollama LLaVA 連携、Pillow 高度処理）
- ★★ E-1.1 Siemens S7 / E-1.2 Allen-Bradley
- ★★ K-9.1 IPMI/Redfish
- ★★ J-3.1 OpenAPI 自動生成
- ★★ E-3.1 prometheus_client 互換 export
- ★★ F-1.2 sqlite-vec / chromadb 拡張バックエンド（ANN）
- B 分類（条件付き）: N-2 医療エッジ AI / N-3 自動運転 LLM Agent — 外部要件成立時に着手
- C/D 分類（Research Backlog 格下げ）: N-1, N-4, N-5, N-6, N-8, N-9, N-10, N-12, N-13, N-14 — 実装ロードマップから外し、要件定義書のみで管理

### 完了済（v2.x で対応）

- ✓ C-12 Rust 拡張（v2.5.0、PointCloud encode 6×）
- ✓ Volume K BACnet（v2.4.0）
- ✓ Volume L C ABI + EdgeProfile（v2.6.0）
- ✓ G-1/G-4/H-3.3 CLI + SBOM（v2.7.0）
- ✓ 8 → 21 分野 RAD（v2.7.0〜v2.10.0）
- ✓ J-4.3 WebSocket（v2.11.0）

### MT-Method Offline Training Flow

```
[Collect]   llmesh mt-collect --device smt_01 --duration 3600  → normal_data.npz
[Train]     llmesh mt-train   --input normal_data.npz --device smt_01  → unit_space.npz
[Infer]     real-time Mahalanobis distance → LLM anomaly report
```

Unit spaces are stored per device under `unit_space_dir` (configurable via `llmesh configure`).

---

---

## Protocol Coverage Target

| Layer | Protocol | Target version | Notes |
|-------|----------|---------------|-------|
| L3    | IP       | ✓ already (OS/uvicorn) | No code change needed |
| L4    | TCP      | ✓ already (uvicorn)    | Reliable stream transport |
| L4    | UDP      | v0.4.0                 | Gossip, discovery, fire-and-forget |
| L7    | HTTP     | ✓ already (FastAPI)    | MCP task endpoint |
| L7    | HTTPS    | ✓ already (TLS via uvicorn) | |
| L7    | SSH      | v0.5.0                 | Secure channel for node-to-node |
| L7    | SFTP     | v0.5.0                 | File-based prompt/result transfer |
| L7    | SMTP     | v0.6.0                 | Email task intake |
| L7    | IMAP     | v0.6.0                 | Email task polling (multi-device) |
| L7    | POP3     | v0.6.0                 | Email task retrieval |
| L7    | FTP      | v0.7.0                 | File-based prompt intake |
| L7    | DNS      | v0.4.0 / v0.8.0       | Discovery (v0.4) + DNS-SD (v0.8) |
| L7    | SNMP     | v0.8.0                 | Node health monitoring |
| L7    | NTP      | v0.8.0                 | Replay-window clock sync |
| L7    | Telnet   | v0.9.0 (opt-in only)  | Legacy; explicitly deprecated |
| L7    | ROS 2 topic/service | v1.1.0       | rclpy; SROS2 auth |
| L7    | ROS 1 topic/service | v1.1.0 (opt-in) | rospy; Noetic only |

---

## Architecture: Protocol Adapter Layer

All protocols share a common pipeline. Adapters translate protocol-specific
framing into a normalized `TaskRequest` and back.

```
          ┌───────────────────────────────────────┐
          │          Protocol Adapters             │
          │  HTTP  UDP  SSH  SMTP  IMAP  FTP  ...  │
          └──────────────────┬────────────────────┘
                             │  TaskRequest (normalized)
                             ▼
          ┌─────────────────────────────────────────┐
          │  Privacy Pipeline (unchanged per-level) │
          │  PromptFirewall → PrivacySummarizer      │
          └──────────────────┬──────────────────────┘
                             │  effective_prompt
                             ▼
          ┌─────────────────────────────────────────┐
          │  LLM Backend (Ollama / LlamaCpp)        │
          └──────────────────┬──────────────────────┘
                             │  raw_response
                             ▼
          ┌─────────────────────────────────────────┐
          │  OutputValidator + AuditTrace            │
          └──────────────────┬──────────────────────┘
                             │  validated TaskResponse
                             ▼
          ┌─────────────────────────────────────────┐
          │  Protocol Adapter (reply path)           │
          └─────────────────────────────────────────┘
```

### Core abstractions (introduced in v0.3.0)

```python
# llmesh/adapters/base.py
class ProtocolAdapter(ABC):
    @abstractmethod
    async def start(self) -> None: ...       # bind / listen
    @abstractmethod
    async def stop(self) -> None: ...        # graceful shutdown
    @abstractmethod
    async def receive(self) -> TaskRequest: ... # protocol → normalized
    @abstractmethod
    async def send(self, req: TaskRequest, resp: TaskResponse) -> None: ...
```

```python
@dataclass
class TaskRequest:
    task_id: str          # UUID v4
    tool_name: str
    prompt: str
    caller_nonce: str     # 32-char hex
    node_id: str          # originating node or empty
    protocol: str         # "http" | "udp" | "ssh" | "smtp" | ...
    metadata: dict        # protocol-specific extras (headers, from-address, …)

@dataclass
class TaskResponse:
    task_id: str
    result: str
    error: str            # empty on success
    data_level: int       # effective level after privacy pipeline
```

---

## Release Plan

### v0.2.0 — Operational Hardening *(in progress)*

**Theme:** Make existing HTTP/MCP path production-safe for trusted LAN swarms.

- P0-1 SQLite-backed NonceStore (restart-safe replay protection)
- P0-2 Multi-process AuditTrace locking (fcntl / msvcrt)
- P0-3 TrustedPeers max-size + gossip TTL
- P0-4 Schema-version-aware CapabilityManifest signing
- P0-5 Forced L3+ privacy pipeline at server boundary
- P1-1 `llmesh audit verify` CLI
- P1-2 Hardening demo script
- P1-3 STRIDE threat model (SECURITY.md)
- P1-4 CI quality gates

**Release gate:** All P0 ACs pass; `python -m pytest` green; `python -m build` clean.

---

### v0.3.0 — Protocol Abstraction Foundation

**Theme:** Extract the HTTP handler into a `ProtocolAdapter` interface so future
protocols can be added without touching the core privacy pipeline.

- `llmesh/adapters/base.py` — `ProtocolAdapter`, `TaskRequest`, `TaskResponse`
- `llmesh/adapters/http.py` — Migrate existing FastAPI handler into `HttpAdapter`
- `llmesh/adapters/tcp_raw.py` — Raw TCP framing (newline-delimited JSON)
- `llmesh/adapters/registry.py` — Adapter registry + multi-adapter server loop
- Adapter lifecycle hooks: `start()`, `stop()`, health check
- Per-adapter nonce namespace to prevent cross-protocol replay
- Tests: adapter registry, lifecycle, cross-protocol nonce isolation

**Dependencies added:** none (pure stdlib + existing deps)

---

### v0.4.0 — UDP + DNS Discovery

**Theme:** Add connectionless transport for gossip and fire-and-forget tasks;
add DNS-based node discovery as an alternative to the rendezvous server.

**UDP adapter**
- `llmesh/adapters/udp.py` — asyncio `DatagramProtocol`-based adapter
- Signed, encrypted datagrams using existing Ed25519 + X25519 infrastructure
- Max payload enforced (MTU-safe, default 1400 bytes)
- L3/L4 prompts rejected at UDP boundary (too large for summarization in datagrams)

**DNS-SD discovery**
- `llmesh/discovery/dns_sd.py` — Publish `_llmesh._tcp.local` mDNS records
- Announce node endpoint, DID, and capability hash via TXT records
- Auto-discover peers without a rendezvous server on LAN

**NTP pre-check** (foundation for v0.8.0)
- `llmesh/security/clock.py` — Warn if system clock drift > 5 s from NTP
- Replay-window accuracy depends on clock quality

**Dependencies added:** `zeroconf>=0.131` (mDNS/DNS-SD)

---

### v0.5.0 — SSH + SFTP

**Theme:** Secure shell transport for interactive and file-based LLM tasks.

**SSH adapter**
- `llmesh/adapters/ssh.py` — Paramiko-based SSH server
- Authenticate callers via Ed25519 public keys (reuse TrustedPeers registry)
- Each SSH session maps to one `TaskRequest`; response sent back on same channel
- Replay protection: SSH session ID used as nonce seed

**SFTP adapter**
- `llmesh/adapters/sftp.py` — Paramiko SFTP subsystem
- Upload a prompt file → LLM processes → result written to outbox directory
- File naming convention: `<task_id>.prompt.txt` → `<task_id>.result.txt`
- L3/L4 files are summarized; originals are never stored server-side

**Telnet placeholder** (disabled by default, scaffold only)

**Dependencies added:** `paramiko>=3.4`

---

### v0.6.0 — Email Protocols (SMTP / IMAP / POP3) ✓ COMPLETE

**Theme:** Email as a task delivery channel for async LLM workflows.

**SMTP intake adapter**
- `llmesh/protocol/smtp_adapter.py` — `aiosmtpd`-based SMTP server ✓
- Incoming email body → `UnifiedMessage`; reply sent via SMTP relay ✓
- Sender address validated against `trusted_senders` allowlist ✓
- Subject line used as `tool_name`; body as `prompt` ✓
- Attachments: text/plain only (binary rejected) ✓

**IMAP poller adapter**
- `llmesh/protocol/imap_adapter.py` — Poll a mailbox for incoming task emails ✓
- Uses `imaplib` (stdlib); configurable poll interval ✓
- Mark processed emails as `\Seen`; store task_id in email header ✓

**POP3 poller adapter**
- `llmesh/protocol/pop3_adapter.py` — `poplib` (stdlib)-based poller ✓
- Retrieve and delete emails; process sequentially ✓

**Privacy note:** Email headers (From, To, Subject) are logged as metadata only;
body is passed through the full L0–L4 pipeline before reaching the LLM.

**Dependencies added:** `aiosmtpd>=1.4`

**Tests:** 74 new tests (smtp:24 + imap:24 + pop3:26) — 1187 passed total

---

### v0.7.0 — FTP ✓ COMPLETE

**Theme:** File Transfer Protocol support for legacy systems and batch workflows.

**FTP server adapter**
- `llmesh/protocol/ftp_adapter.py` — `pyftpdlib`-based FTP/FTPS server ✓
- Upload `.prompt` files → processed → `.result` files available for download ✓
- FTPS (TLS via pyOpenSSL) by default; plain FTP via `allow_plain_ftp=True` ✓
- Self-signed cert auto-generated when certfile/keyfile not provided ✓
- Per-user isolated home directory under adapter tmpdir ✓

**Dependencies added:** `pyftpdlib>=2.0`, `pyOpenSSL` (for FTPS)

**Tests:** 23 new tests — 1218 collected total

---

### v0.8.0 — Management Protocols (SNMP, NTP, DNS-SD v2) ✓ COMPLETE

**Theme:** Operations and observability — let standard network management tools
monitor LLMesh nodes without custom dashboards.

**SNMP agent**
- `llmesh/protocol/snmp_adapter.py` — `pysnmp`-based SNMPv3 agent (SNMPv1/v2 disabled) ✓
- OID tree under `enterprises.llmesh (1.3.6.1.4.1.99999).*`:
  - `nodeId`, `did`, `activeConnections`, `requestsTotal`, `firewallBlocksTotal`
  - `auditChainValid`, `nonceStoreSize`, `trustedPeerCount`
- Read-only; no SET operations allowed ✓
- Registered in AdapterRegistry as `"snmp"` ✓

**NTP sync enforcement**
- `llmesh/security/clock.py` — Query NTP servers; raise ClockDriftError if drift > threshold ✓
- Configurable: `LLMESH_NTP_SERVERS`, `LLMESH_MAX_CLOCK_DRIFT_S` (default 10), `LLMESH_NTP_TIMEOUT_S` ✓
- Falls back to next server on timeout; raises RuntimeError if all unreachable ✓

**DNS-SD v2**
- `llmesh/discovery/dns_sd.py` — `DnsSdAnnouncer` + `DnsSdConfig` via zeroconf ✓
- Enhanced TXT record: schema_version, capability_hash, data_levels_accepted ✓
- SRV records for each protocol adapter endpoint (extra_protocols) ✓

**Dependencies added:** `pysnmp>=6.1`, `ntplib>=0.4`

**Tests:** 66 new tests (clock:17 + snmp:30 + dns_sd:19) — 1284 collected total

---

### v0.9.0 — Telnet (Legacy, Opt-in) + Security Hardening ✓ COMPLETE

**Theme:** Complete protocol coverage with explicit security boundaries.

**Telnet adapter** (explicitly deprecated at implementation)
- `llmesh/protocol/telnet_adapter.py` — `asyncio`-based Telnet server ✓
- Requires `LLMESH_ENABLE_TELNET=1` AND `LLMESH_UNSAFE_TELNET_NO_TLS=1` ✓
- Startup warning logged: "TELNET IS UNENCRYPTED — NOT FOR PRODUCTION USE" ✓
- L3/L4 prompts rejected at Telnet boundary unconditionally ✓
- No auth state persisted; each connection is isolated ✓

**Cross-protocol security hardening**
- Unified rate limiter across all adapters ✓
- Cross-protocol nonce deduplication (prevent HTTP nonce replay via SMTP) ✓
- Adapter-level circuit breakers ✓

**Tests:** 54 new (telnet:29 + cross_protocol:25) — 1338 passed total

---

### v1.0.0 — Full Multi-Protocol LLM Gateway

**Theme:** Stable, documented, operator-verifiable multi-protocol LLM mesh —
including first-class Claude Code / MCP client integration.

**Unified configuration (`llmesh.toml`)**
- `llmesh/config/toml_config.py` — TOML parser using stdlib `tomllib` (Python 3.11+)
- Replaces scattered environment variables with a single file
- Sections: `[node]`, `[adapters]`, `[security]`, `[circuit_breaker]`
- Falls back to env vars when file is absent (backwards-compatible)
- `AdapterConfig`, `SecurityConfig`, `CircuitBreakerConfig` dataclasses

**Protocol adapter auto-discovery (entry-points)**
- `llmesh/protocol/registry.py` extended with `load_entrypoints()` method
- Reads `importlib.metadata.entry_points(group="llmesh.adapters")`
- Third-party packages declare adapters in their `pyproject.toml` under
  `[project.entry-points."llmesh.adapters"]`
- Built-in adapters registered as entry-points in `pyproject.toml`

**MCP stdio server — Claude Code integration**
- `llmesh/mcp/stdio_server.py` — standard MCP JSON-RPC 2.0 over stdio
- Exposes all registered tools (`generate_code`, `review_code`, …) as MCP tools
- Launched via `python -m llmesh serve-mcp`
- Privacy pipeline (PromptFirewall → PrivacySummarizer) applies to every tool call
- Nonce generated server-side for each MCP invocation (client does not need to supply one)
- Claude Code config example (add to `~/.claude.json`):
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
- Tools available in Claude Code: `generate_code`, `review_code`, `explain_code`,
  `suggest_tests` (all filtered through the privacy pipeline)
- Rate limiting and circuit breakers apply per MCP session caller

**Operator documentation**
- Per-protocol setup guide (env vars → llmesh.toml migration)
- Security model per adapter (auth, nonce, rate limit, circuit breaker)
- Claude Code / MCP integration quickstart

**PyPI release**
- `python -m build` produces a clean wheel
- SemVer guarantee from this point

**Dependencies added:** none (`tomllib` is stdlib in Python 3.11+;
`mcp` SDK is optional — `pip install llmesh[claude]`)

```toml
[project.optional-dependencies]
claude = ["mcp>=1.0"]   # MCP stdio server for Claude Code integration
```

**Remaining limitations (documented, not in scope):**
- No multi-tenant authorization (deferred post-v1.0)
- No distributed AGI claims
- No encrypted overlay network
- No federated training

---

### v1.1.0 — ROS / ROS 2 Integration (Robotics) ✓ COMPLETE

**Theme:** Extend LLMesh into robotics environments by bridging ROS (Robot
Operating System) topic/service communication with the LLM privacy pipeline.

**Motivation:** ROS is the de facto standard for robotic middleware.
Connecting ROS nodes to an LLM gateway enables natural-language task
planning, sensor data summarisation, and human-robot interaction — while
the privacy pipeline ensures raw sensor streams (which may contain L3/L4
data) are never forwarded unfiltered to a remote LLM.

**ROS 2 topic adapter**
- `llmesh/protocol/ros2_adapter.py` — `rclpy`-based adapter
- Subscribe to a configurable topic (e.g. `/llmesh/request`, type `std_msgs/String`)
- Publish responses on `/llmesh/response`
- Each message maps to one `TaskRequest`; JSON payload in the string field
- L3/L4 messages rejected at adapter boundary (raw sensor payloads blocked)

**ROS service bridge**
- Expose a ROS 2 service (`llmesh_srv/LLMTask`) for synchronous request-response
- Service definition: `string prompt → string result, bool success`
- Node ID derived from ROS node name; nonce from message header stamp

**ROS 1 compatibility shim** (opt-in, requires `rospy`)
- `llmesh/protocol/ros1_adapter.py` — `rospy`-based fallback for ROS Noetic
- Requires `LLMESH_ENABLE_ROS1=1` opt-in (ROS 1 EOL is 2025)
- Same privacy pipeline and L3/L4 restrictions apply

**Privacy considerations**
- Sensor data (camera, LiDAR, IMU) is classified as L3 by default
- A `SensorSummarizer` pre-processor condenses raw topics into text descriptions
  before the prompt reaches the LLM backend
- Raw sensor payloads are never stored; only the summarised text is logged

**Security**
- Node authentication via DDS Security (ROS 2 SROS2) or explicit node-name allowlist
- Rate limiting per ROS node name using `UnifiedRateLimiter`
- Circuit breaker per ROS node via `AdapterCircuitBreakerRegistry`

**Dependencies added:** `rclpy` (ROS 2 — system package, not PyPI),
  `rospy` (ROS 1 opt-in — system package)

**Protocol coverage table additions:**

| Layer | Protocol | Target version | Notes |
|-------|----------|---------------|-------|
| L7    | ROS 2 topic/service | v1.1.0 | rclpy; SROS2 auth |
| L7    | ROS 1 topic/service | v1.1.0 (opt-in) | rospy; Noetic only |

---

### v1.2.0 — Multimodal Input (Vision) ✓ COMPLETE

**Theme:** Extend the existing privacy pipeline to accept image inputs, enabling
Vision-capable local LLM backends (e.g. Ollama + LLaVA) without breaking any
existing text-only flows.

**Motivation:** Local file workflows (`LocalFileAdapter`) and the MCP stdio server
can benefit from image context — screenshots for UI review, diagrams for code
explanation, sensor frames for robotics. The privacy pipeline already handles
text summarisation; images require an equivalent gate before reaching the LLM.

**ImageFirewall** (`llmesh/privacy/image_firewall.py`)
- Classifies images by content before passing to Vision backend
- L4 images (faces, ID documents detected via metadata/EXIF) → always BLOCK
- L3 images (screenshots containing text with PII patterns) → ImageSummarizer
- L0/L1 images (diagrams, charts, code screenshots) → pass through
- No raw L3/L4 pixel data ever reaches the LLM backend

**ImageSummarizer** (`llmesh/privacy/image_summarizer.py`)
- For L3 images: generates a text description via a local captioning model
- The description (not the image) is passed to the main LLM backend
- Configurable: `LLMESH_IMAGE_CAPTIONER` env var (default: `ollama/llava`)

**LocalFileAdapter extension**
- Accepts `*.prompt.png` / `*.prompt.jpg` / `*.prompt.webp` in addition to `*.prompt.txt`
- Image + optional sidecar `*.prompt.txt` combined into a multimodal request
- File naming: `task.review_code.prompt.png` → tool `review_code` with image input

**MCP stdio server extension**
- `tools/call` accepts `arguments.image_base64` (base64-encoded PNG/JPEG)
- Image routed through ImageFirewall → ImageSummarizer before LLM invocation
- Claude Code can send screenshots directly via the MCP tool call

**Security invariants (additional)**
- No image pixels stored server-side after processing
- EXIF metadata stripped before any logging
- `shell=True` never used for image processing (Pillow only, no ImageMagick subprocess)
- Image size capped at 10 MiB before decode

**Explicitly out of scope for v1.2.0**
- Command execution triggered by LLM output (deferred indefinitely — see note below)
- Audio / video input
- Training or fine-tuning on captured images

**Dependencies added:** `Pillow>=10.0` (image decode + EXIF strip)

```toml
[project.optional-dependencies]
vision = ["Pillow>=10.0"]
```

> **Note — Command execution (deferred):** Allowing LLM output to drive
> `subprocess` calls creates a "untrusted output → shell" attack path even with
> list-based arguments. This feature is parked until a sandboxing design
> (e.g. restricted `subprocess` allowlist + mandatory human-in-the-loop
> confirmation) is ready. It will NOT be added before that design is reviewed.

---

## Security Invariants Across All Versions

These invariants must hold for every protocol adapter:

1. Raw L4 prompts never reach an LLM backend.
2. Raw L3 prompts never reach an LLM backend (summarized first).
3. Guard failures fail closed — any exception returns BLOCK.
4. Replay store failures fail closed.
5. Audit chain verification detects tampering.
6. No new `shell=True`, `eval`, `exec`, `pickle`, or unsafe SQL.
7. Every adapter authenticates callers via TrustedPeers or explicit opt-out.
8. Telnet adapter requires double opt-in and logs a deprecation warning.

---

## Dependency Budget

| Version | New dependencies | Cumulative |
|---------|-----------------|------------|
| v0.2.0  | none (stdlib sqlite3, fcntl/msvcrt) | cryptography, jsonschema, base58, fastapi, uvicorn |
| v0.3.0  | none | same |
| v0.4.0  | zeroconf | +1 |
| v0.5.0  | paramiko | +2 |
| v0.6.0  | aiosmtpd | +3 |
| v0.7.0  | pyftpdlib | +4 |
| v0.8.0  | pysnmp, ntplib | +6 |
| v0.9.0  | none | +6 |
| v1.0.0  | none | +6 |
| v1.0.1  | watchdog | +7 |
| v1.1.0  | rclpy, rospy (system packages — not PyPI) | +7 (PyPI unchanged) |
| v1.2.0  | Pillow | +8 |

All new dependencies are optional extras in `pyproject.toml`:
```toml
[project.optional-dependencies]
udp   = ["zeroconf>=0.131"]
ssh   = ["paramiko>=3.4"]
email = ["aiosmtpd>=1.4"]
ftp   = ["pyftpdlib>=2.0"]
mgmt  = ["pysnmp>=6.1", "ntplib>=0.4"]
ros   = []  # rclpy/rospy are system packages installed via apt/rosdep
all   = ["zeroconf>=0.131", "paramiko>=3.4", "aiosmtpd>=1.4",
         "pyftpdlib>=2.0", "pysnmp>=6.1", "ntplib>=0.4"]
```
