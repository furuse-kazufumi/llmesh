<!--
title: LLMesh 紹介記事まとめ — どれから読めばいい？
tags: LLM,Python,IoT,セキュリティ,RAG
-->

# LLMesh 紹介記事まとめ — どれから読めばいい？

> Secure LLM Mesh over MCP — `pip install llmesh-mcp`
> GitHub: <https://github.com/furuse-kazufumi/llmesh>

LLMesh は **117 章 / 500+ 要件項目 / 2300+ テスト全 PASS** の Python 統合フレームワークで、
ローカル/クラウド LLM・産業プロトコル・SPC 異常検知・RAG・プロンプトファイアウォールを 1 つに固めたものです。
読みたい切り口に合わせて 4 本の紹介記事を用意しました。

---

## 30 秒で結論

```bash
pip install llmesh-mcp
```

```python
from llmesh import PromptFirewall
from llmesh.llm import OllamaBackend           # ローカル LLM
# from llmesh.llm import openai_backend, anthropic_backend  # クラウドも同じ ABC

fw  = PromptFirewall()
llm = OllamaBackend(model="llama3.2")

prompt = "自分の API キー sk-... をログに残さず、要件を 1 行で言って"
v = fw.check(prompt)
if v.action != "BLOCK":
    print(llm.complete(v.summarized or prompt))
else:
    print(f"blocked at {v.layer}: {v.reason}")
```

---

## あなたが○○なら、この記事から

### 1. 「LLM のプロンプトに何を入れていいか不安」 — セキュリティ視点

[**LLM のプロンプトに「何を渡してよいか」を 4 層で統制する**](./qiita-security.md)

- 4 層プロンプトファイアウォール（注入 / シークレット / Presidio PII / 構造）
- OutputValidator で **出力側** も塞ぐ
- HMAC chain の改ざん検出 Audit Log
- OWASP / Bandit 静的監査クリーン
- **対象**: LLM アプリ開発者、SRE、セキュリティエンジニア

### 2. 「現場の PLC / SCADA を LLM と繋ぎたい」 — 産業 IoT 視点

[**Modbus / OPC-UA / DNP3 / GOOSE を SensorEvent で吸って CUSUM で異常を捕まえて LLM に説明させる**](./qiita-industrial.md)

- 20+ 産業プロトコルを **同一 ABC** で扱う
- Mahalanobis-Taguchi / Hotelling T² / CUSUM / Xbar-R の多変量 SPC
- `ExplainedCUSUM` で **異常検出と同時に LLM が原因仮説を Markdown 出力**
- `VideoCUSUM` で動画 × 数値センサーを時刻同期
- **対象**: 制御エンジニア、SCADA 開発者、Predictive Maintenance チーム

### 3. 「ローカル LLM とクラウド LLM を同じ書き方で扱いたい」 — LLM プラットフォーム視点

[**ローカル LLM とクラウド LLM を「同じ書き方」で扱いたい人のための LLMesh**](./qiita-llm-platform.md)

- Ollama / OpenAI / Azure / Anthropic / OpenRouter / Groq / Together / Mistral / DeepSeek を同一 ABC
- **外部 DB ゼロ** の 3 段ベクトルストア（Numpy / SQLite / LSH ANN）
- Claude Code / MCP 連携をコピペで動かす
- **対象**: LLM アプリ開発者、RAG プロトタイプ開発者、PoC エンジニア

### 4. 「性能と信頼性が気になる」 — パフォーマンス視点

[**Pure Python の 6 倍速い Rust 拡張と、ストリーミング再送・HTTP DoS 対策まで詰め込んだ Python ライブラリ**](./qiita-performance.md)

- PyO3 + maturin の Rust 拡張（PointCloud encode **6×**）、**自動 fallback**
- 信頼性プロトコル（ACK / RETRANSMIT / Watchdog / TTL）
- HTTP DoS hardening（全 8 HTTP クライアントに `read_capped`）
- Hypothesis property-based 1,200 ケース
- **対象**: パフォーマンス重視の人、PyO3 興味層、長時間運用するシステムを作っている人

---

## 用途別の組み合わせ

| やりたいこと | 必要 extras | 主に読む記事 |
|---|---|---|
| プロンプト保護だけ | `[presidio]` | セキュリティ |
| ローカル LLM ＋ RAG | `[rag]` | LLM プラットフォーム |
| クラウド LLM 多重化 | （extras 不要） | LLM プラットフォーム |
| 産業 IoT データ収集 | `[industrial]` | 産業 IoT |
| 産業 IoT × LLM 説明 | `[industrial]` | 産業 IoT + LLM プラットフォーム |
| 動画 × センサー異常検知 | `[industrial,vision]` | 産業 IoT |
| SCADA / 電力系 | `[industrial,dnp3]` | 産業 IoT |
| 全部入り | `[industrial,vision,presidio,rag]` | 全部 |

---

## 共通の起動コマンド

```bash
# 何が動くか全部出す
python -m llmesh.cli.doctor

# 現在の状態（ノード ID / Capability / 接続先）
python -m llmesh.cli.status

# 供給連鎖監査用 SBOM
python -m llmesh.cli.sbom > llmesh.sbom.cdx.json
```

---

## バージョン状況（2026-05-09 時点）

- **現行**: v3.1.0（クラウド LLM 統合）
- **API 安定性**: v3.0.0 で SemVer 正式適用、`docs/API_STABILITY.md` の公開シンボル一覧が契約
- **テスト**: 2300+ 件全 PASS（Hypothesis property-based 1,200 ケース含む）
- **ライセンス**: MIT

---

## 次のステップ

- まず触る: `pip install llmesh-mcp` → `python -m llmesh.cli.doctor`
- 仕様: <https://github.com/furuse-kazufumi/llmesh/blob/main/docs/SPECIFICATION.md>
- ロードマップ: <https://github.com/furuse-kazufumi/llmesh/blob/main/docs/ROADMAP.md>
- Issue / Feature Request: <https://github.com/furuse-kazufumi/llmesh/issues>

「○○ backend が欲しい」「△△ プロトコルが欲しい」「日本語 PII を強化したい」、PR / Issue 全部歓迎です。
