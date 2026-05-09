<!--
title: ローカル LLM × 産業 IoT × プロンプトファイアウォールを 1 つの Python フレームワークで — LLMesh v3.1.0 を作った話
tags: Python,LLM,IoT,セキュリティ,MCP
-->

# ローカル LLM × 産業 IoT × プロンプトファイアウォールを 1 つの Python フレームワークで — LLMesh v3.1.0 を作った話

> Secure LLM Mesh over MCP — `pip install llmesh-mcp`

## TL;DR

- **LLMesh** は、ローカル LLM（Ollama / llama.cpp）とクラウド LLM（OpenAI / Azure / Anthropic / OpenRouter / Groq / Together / Mistral / DeepSeek）を **同一 ABC で透過運用** できる Python 統合フレームワークです。
- それに加えて **4 層プロンプトファイアウォール**、**産業プロトコル 20+ アダプタ**（Modbus / OPC-UA / MQTT / EtherCAT / CAN / BACnet / DNP3 / IEC 61850 GOOSE / WebSocket …）、**多変量 SPC（MT 法 / Hotelling T² / CUSUM / Xbar-R）**、**RAG**、**Rust 拡張（PointCloud encode 6×）** を一本化しています。
- **117 章 / 500+ 要件項目**、**2300+ テスト全 PASS**、**OWASP 静的監査クリーン**（`shell=True` / `pickle` / `eval` / SQL 注入 / 弱暗号 ゼロ）、**v3.0.0 から SemVer 正式適用**。
- リポジトリ: <https://github.com/furuse-kazufumi/llmesh>　/　PyPI: <https://pypi.org/project/llmesh-mcp/>

```bash
pip install llmesh-mcp
# 産業用フル機能
pip install "llmesh-mcp[industrial,vision,presidio,rag]"
```

---

## なぜ作ったのか

LLM をプロダクションに乗せるとき、毎回ぶつかる壁が 3 つあります。

1. **プロンプトに何を渡すかの統制が取れない** — API キー、PEM、患者データ、絶対パスがそのまま流れる。
2. **ローカル LLM とクラウド LLM の切り替えが地獄** — backend ごとにエラー型・タイムアウト・トークン制御が違う。
3. **産業 IoT との結合層が毎回スクラッチ** — Modbus / OPC-UA / MQTT を貼り付けて、CUSUM を numpy で書き直して、JSON で吐いて…。

LLMesh はこの 3 つを **1 本のフレームワーク + 統一 ABC** で解こうとしたものです。`SensorEvent` という単一のデータモデルで、フィールドからクラウド LLM までを **fail-closed** に貫きます。

---

## アーキテクチャ概観

```
        ┌────────────────────────────────────────────────────────┐
        │  Industrial Adapters (Modbus / OPC-UA / MQTT / DNP3 / │
        │  GOOSE / EtherCAT / CAN / BACnet / WebSocket / ROS2)  │
        └───────────────┬────────────────────────────────────────┘
                        │  SensorEvent
                        ▼
        ┌────────────────────────────────────────────────────────┐
        │   SPC / MT / CUSUM / Hotelling T² / VideoCUSUM        │
        │   ExplainedCUSUM ──► IncidentReport (Markdown / JSON) │
        └───────────────┬────────────────────────────────────────┘
                        │
                        ▼
        ┌────────────────────────────────────────────────────────┐
        │   PromptFirewall  L0 → L1 → L1.5 (Presidio) → L2      │
        │   PrivacySummarizer  /  ImageFirewall                  │
        └───────────────┬────────────────────────────────────────┘
                        │
                        ▼
        ┌────────────────────────────────────────────────────────┐
        │   LLM Backend (Ollama / llama.cpp / OpenAI / Azure /   │
        │   Anthropic / OpenRouter / Groq / Together / Mistral   │
        │   / DeepSeek) — 同一 ABC                              │
        └───────────────┬────────────────────────────────────────┘
                        │
                        ▼
                 OutputValidator (JSON / schema / nonce)
                        │
                        ▼
                  RAG (Numpy / SQLite / LSH)
```

---

## ハイライト 1: 4 層プロンプトファイアウォール

LLM に渡す **直前** で、4 層に分けて検査します。

| Layer | 役割 | 出力 |
|------:|------|------|
| L0 | プロンプト注入 / jailbreak / Unicode 制御文字 | BLOCK |
| L1 | シークレット（API キー、JWT、PEM、AWS、GitHub、Anthropic、OpenAI） | BLOCK |
| **L1.5** | **Microsoft Presidio による PII（CC / SSN / IBAN / 医療免許 / 個人名 / Email / 電話 …）** | **BLOCK or SUMMARIZE** |
| L2 | 絶対パス / 内部 import / オーバーサイズ payload | SUMMARIZE or BLOCK |

```python
from llmesh import PromptFirewall

fw = PromptFirewall()
verdict = fw.check("API_KEY=sk-... を漏らさずに要約して")
# verdict.action == "BLOCK"
# verdict.layer  == "L1"
# verdict.reason == "secret_pattern: openai_api_key"
```

設計上のキモは **fail-closed**（例外が出たら BLOCK）と、**全 HTTP クライアントにレスポンスサイズ上限**（DoS 対策）。`pickle`・`yaml.load(unsafe)`・`eval`・`exec`・`shell=True` は **コードベース全体でゼロ**です。

---

## ハイライト 2: ローカル / クラウド LLM を同一 ABC で透過運用（v3.1.0）

```python
from llmesh.llm import OllamaBackend, openai_backend, anthropic_backend

# ローカル
local = OllamaBackend(model="llama3.2")

# クラウド（OpenAI / Azure / OpenRouter / Together / Groq / Mistral / DeepSeek）
cloud = openai_backend(api_key=..., model="gpt-4o-mini")

# Anthropic
claude = anthropic_backend(api_key=..., model="claude-haiku-4-5")

# どれも .complete(prompt) / .chat(messages) で呼べる
for backend in (local, cloud, claude):
    print(backend.complete("Hello in one short sentence."))
```

**フェイルオーバーやコストルーティング**を上に乗せるとき、ABC が揃っていると 30 行で済みます。

---

## ハイライト 3: 産業 IoT — `SensorEvent` で全部吸う

```python
from llmesh.industrial import (
    ModbusAdapter, OPCUAAdapter, MQTTAdapter,
    DNP3Adapter, GOOSEAdapter,             # v2.14
    SensorEvent,
    CUSUMChart, HotellingT2Chart,          # 多変量 SPC
    ExplainedCUSUM,                        # v2.14: 自己説明 CUSUM
)

modbus = ModbusAdapter(host="10.0.0.10")
chart  = ExplainedCUSUM(target=70.0, k=0.5, h=5.0)

async for ev in modbus.stream():           # SensorEvent を yield
    report = chart.update(ev)              # IncidentReport or None
    if report:
        print(report.to_markdown())        # LLM 説明付きの異常レポート
```

`ExplainedCUSUM` は **CUSUM が異常を検出した瞬間に LLM が原因仮説を出す**コンポーネントです。`IncidentReport` は Markdown / JSON のどちらでも吐けます。

`VideoCUSUM` は動画フレームと数値センサーを **時刻同期ペア化バッファ** で揃えてから 2 系統 CUSUM をかけるもの（`sync_window_s` 既定 1.0s、bounded deque）。SCADA × カメラの組み合わせを想定しています。

---

## ハイライト 4: RAG — 3 段階のベクトルストア

データ規模に合わせて 3 種類のストアを切り替えられます。**外部 DB ゼロ・全部 stdlib + numpy** です。

| ストア | 件数目安 | 永続化 | 検索 |
|---|---:|---|---|
| `NumpyVectorStore` | 〜10⁵ | `.npz` アトミック | O(n) cosine |
| `SqliteVectorStore` | 〜10⁶ | sqlite3 (WAL) | O(n) cosine |
| `LSHVectorStore` | 10⁶〜 | `.npz` | LSH ANN（recall@10 ≥ 0.92） |

```python
from llmesh.rag import Retriever, MockEmbedder, NumpyVectorStore
from llmesh import PromptFirewall

retriever = Retriever(
    embedder=MockEmbedder(dim=128),
    store=NumpyVectorStore(path="kb.npz"),
    firewall=PromptFirewall(),       # 取り出した文書も Firewall を通す
)
hits = retriever.search("Modbus のリプレイ攻撃対策", k=5)
```

`Retriever` には **Firewall を必須注入**しているので、汚染された文書がそのまま LLM に流れる事故を防げます。

---

## ハイライト 5: Rust 拡張で 6×

`rust_ext/`（PyO3 + maturin）で点群と DVS イベントのエンコードを Rust 化しています。

| 操作 | Pure Python | Rust | 倍率 |
|------|-----------:|-----:|----:|
| PointCloud encode (1M) | 4.0M pts/s | **24.1M pts/s** | **6.0×** |
| PointCloud decode (1M) | 3.7M pts/s | 5.9M pts/s | 1.6× |
| DVS encode (1M) | 3.4M evt/s | 5.5M evt/s | 1.6× |
| Pipeline + CUSUM | 190K events/s | – | – |

```bash
cd rust_ext && python -m maturin build --release
pip install --force-reinstall target/wheels/*.whl
```

Rust 拡張は **任意**（無くても Pure Python で動く）。CI は **8 ターゲットの multi-platform wheel** を吐きます。

---

## ハイライト 6: 信頼性プロトコル

ストリーミング通信の信頼性を `MessageAssembler` と `ChunkSender` の組み合わせで保証します。

```
[正常完了]  受信: pop_completed() → STREAM_ACK 送信
            送信: handle_ack()    → 送信バッファ破棄

[欠落検出]  受信: check_timeouts() → RETRANSMIT 送信（1 回のみ）
            送信: handle_retransmit() → 欠落チャンクのみ再送

[切断検出]  受信: check_watchdog()  → True で切断シグナル
            送信: expire_old()      → TTL 超過バッファ自動破棄
```

GOOSE アダプタは **`stNum` の per-ref リプレイ防御** 付き、`MAX_DATASET_VALUES` ガード付き。

---

## セキュリティ設計の不変条件

LLMesh の `docs/SECURITY.md` には STRIDE モデルと **不変条件**が書いてあります。要約すると:

- `shell=True`, `pickle`, `yaml.load(unsafe)`, `eval`, `exec` を **一切使わない**
- subprocess は **list 形式のみ**
- Firewall は **fail-closed**（例外 → L4 / BLOCK）
- OutputValidator が **non-JSON / schema 不一致 / nonce replay** を拒否
- 全 HTTP クライアントは **`read_capped` で用途別レスポンス上限**
- すべての optional 依存は **extras**（軽量本体）
- Audit log は **HMAC chain で tamper-evident**

これは v2.16 で全コードに対する OWASP 静的監査をかけた結果として **クリーン**になっています（Bandit / 自前レビュー）。

---

## CLI ツールチェーン

```bash
python -m llmesh.cli.doctor   # 環境健全性チェック（依存・ポート・権限）
python -m llmesh.cli.status   # ランタイム状態（ノード ID / Capability / 接続先）
python -m llmesh.cli.sbom     # CycloneDX SBOM 自動生成
```

`doctor` はあえて **「動いてない理由を全部出す」** に振ってあります。`status` は本番ノードを覗くため、`sbom` は供給連鎖監査のために常設しています。

---

## Claude Code MCP サーバとして使う

`claude_desktop_config.json` に書くだけで、Claude Code から `llmesh` のツール群（センサー読み出し / SPC 判定 / RAG 検索）を叩けます。

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

MCP の Output は **OutputValidator** を必ず通過するので、tool 側からの注入も封じています。

---

## バージョン履歴（抜粋）

| Ver | 内容 |
|---|---|
| v2.13.0 | Presidio Layer 1.5 + RAG MVP + 多変量 SPC コア |
| v2.14.0 | ExplainedCUSUM / VideoCUSUM / VLMFeatureExtractor / SqliteVectorStore / DNP3 / GOOSE |
| v2.15.0 | LSHVectorStore（ANN）+ 公開 API レイヤー + `API_STABILITY.md` |
| v2.16.0 | 全体コードレビュー反映（OWASP 静的監査クリーン） |
| v2.17.0 | HTTP DoS hardening（全 8 HTTP クライアントに `read_capped`） |
| v2.18.0 | ドキュメント整備（CONTRIBUTING / DEVELOPMENT / TROUBLESHOOTING / MIGRATION / DEPLOYMENT / OBSERVABILITY / TESTING / GLOSSARY） |
| v3.0.0 | **API Stability Release**（SemVer 正式適用、`__all__` 契約化） |
| **v3.1.0** | **クラウド LLM 統合（OpenAI / Azure / Anthropic / OpenRouter / Together / Groq / Mistral / DeepSeek）** |

---

## 品質スコア

| 軸 | スコア |
|----|---:|
| データ網羅性 | 9.9（25 分野 RAD + 117 章要件） |
| ドキュメント | 9.8 |
| 拡張性 | 9.8 |
| テスト | 9.5（2300+ 件、Hypothesis property-based 1,200 ケース） |
| パフォーマンス | 8.5（Rust 6×） |
| **総合** | **約 9.5 / 10** |

---

## 触ってみる

```bash
pip install llmesh-mcp
python -c "from llmesh import PromptFirewall; print(PromptFirewall().check('hello'))"
```

産業プロトコルやクラウド LLM を試すときは extras を入れてください:

```bash
pip install "llmesh-mcp[industrial,vision,presidio,rag]"
```

- GitHub: <https://github.com/furuse-kazufumi/llmesh>
- PyPI: <https://pypi.org/project/llmesh-mcp/>
- License: MIT

---

## おわりに

LLMesh は「LLM をプロダクションに乗せるたびに毎回書いていた退屈な部分」を 1 つのパッケージに封じ込めるための実験です。
**プロンプトに何を渡してよいかを統制し、現場のセンサーから LLM までを fail-closed に貫き、ローカルとクラウドを差し替え可能にする** —— ここに需要があると感じる人がいたら、ぜひ Issue や PR をください。

ご意見・バグ報告: <https://github.com/furuse-kazufumi/llmesh/issues>
