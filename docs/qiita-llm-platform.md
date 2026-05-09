<!--
title: ローカル LLM とクラウド LLM を「同じ書き方」で扱いたい人のための LLMesh — 30 秒で動かせる Python フレームワーク
tags: LLM,Python,OpenAI,Anthropic,Ollama
-->

# ローカル LLM とクラウド LLM を「同じ書き方」で扱いたい人のための LLMesh — 30 秒で動かせる Python フレームワーク

> Ollama / OpenAI / Azure / Anthropic / OpenRouter / Groq / Together / Mistral / DeepSeek を 同じ ABC で
> `pip install llmesh-mcp`

---

## まず動かす（30 秒）

```bash
pip install llmesh-mcp
```

```python
# どこの LLM でも同じインターフェース
from llmesh.llm import OllamaBackend

llm = OllamaBackend(model="llama3.2")          # ローカルなら API キー不要
print(llm.complete("Pythonの`yield`を1行で説明して"))
```

クラウドに切り替えるのはこれだけです。

```python
from llmesh.llm import openai_backend

llm = openai_backend(api_key="sk-...", model="gpt-4o-mini")
print(llm.complete("Pythonの`yield`を1行で説明して"))
```

**呼び出しコードは 1 文字も変わりません。** これがやりたかったポイントです。

---

## 何が嬉しいのか（3 つだけ）

1. **backend の差し替えがコード 1 行**：開発はローカル Ollama、本番は OpenAI、検証は Anthropic、コスト圧縮で OpenRouter。
2. **エラー型・タイムアウト・リトライが統一**：プロバイダごとに try/except を書き分けなくていい。
3. **LLM の前後にセキュリティ層が無料で乗る**：Prompt Firewall / OutputValidator / Audit Log を **オプションで挟める**。

---

## 対応 backend 一覧

| backend | 用途 | 必要なもの |
|---|---|---|
| `OllamaBackend` | ローカル LLM | `ollama` を起動しておく（`ollama serve`） |
| `LlamaCppBackend` | ローカル GGUF | `llama-cpp-python` |
| `openai_backend(...)` | OpenAI / Azure OpenAI / OpenRouter / Together / Groq / Mistral / DeepSeek（OpenAI 互換 API なら全部） | API キー |
| `anthropic_backend(...)` | Claude (Haiku / Sonnet / Opus) | API キー |

**OpenAI 互換 API は 1 つの関数で吸う**ので、新しいプロバイダが出ても `base_url` を変えるだけで使えます。

```python
# OpenRouter 経由で複数モデルを比較
or_llm = openai_backend(
    api_key=OR_KEY,
    base_url="https://openrouter.ai/api/v1",
    model="anthropic/claude-haiku-4-5",
)
```

---

## 「最初の RAG」を 5 分で

外部 DB ゼロ・全部 stdlib + numpy で動く RAG が入っています。

```python
from llmesh.rag import Retriever, MockEmbedder, NumpyVectorStore, Document

store = NumpyVectorStore(path="kb.npz")        # .npz に永続化
embedder = MockEmbedder(dim=128)               # 決定論ハッシュ（依存ゼロ）

# 文書を入れる
store.add([
    Document(id="d1", text="LLMesh はローカル LLM とクラウド LLM を同じ ABC で扱う"),
    Document(id="d2", text="PromptFirewall は注入・PII・シークレットを 4 層で塞ぐ"),
    Document(id="d3", text="SensorEvent は産業プロトコル 20+ を 1 つに統一する"),
], embedder=embedder)
store.save()

# 検索
retriever = Retriever(embedder=embedder, store=store)
hits = retriever.search("プロンプトインジェクション対策は？", k=2)
for h in hits:
    print(h.score, h.document.text)
```

実装が育ったら **そのまま Ollama Embedder に差し替え** できます。

```python
from llmesh.rag import OllamaEmbedder
embedder = OllamaEmbedder(model="nomic-embed-text")  # urllib のみで動く
```

データが増えたら **3 段階のストア** から選びます。

| ストア | 件数の目安 | 永続化 | 検索 |
|---|---:|---|---|
| `NumpyVectorStore` | 〜10⁵ | `.npz` | O(n) cosine |
| `SqliteVectorStore` | 〜10⁶ | sqlite3 (WAL) | O(n) cosine |
| `LSHVectorStore` | 10⁶〜 | `.npz` | LSH ANN（recall@10 ≥ 0.92） |

**外部 DB を立てる必要が無い** のがコンセプトです。Docker も Postgres も不要、`pip install` で完結します。

---

## ガード付きで LLM を呼ぶ（推奨パターン）

```python
from llmesh import PromptFirewall
from llmesh.llm import openai_backend

fw  = PromptFirewall(presidio_enabled=True)    # PII 層を有効化（要 [presidio]）
llm = openai_backend(api_key=KEY, model="gpt-4o-mini")

def safe_complete(prompt: str) -> str:
    v = fw.check(prompt)
    if v.action == "BLOCK":
        raise PermissionError(f"blocked at {v.layer}: {v.reason}")
    if v.action == "SUMMARIZE":
        prompt = v.summarized          # PII をプレースホルダ化済み
    return llm.complete(prompt)
```

**この 8 行**で「シークレット漏れ・プロンプト注入・PII 流出」を 1 セット塞げます。

---

## Claude Code / MCP から使う（コピペ用）

`claude_desktop_config.json` または Claude Code の設定 JSON に貼ります。

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

これだけで Claude Code から `llmesh` の tool 群（センサー読み出し・SPC 判定・RAG 検索）を呼べます。
**MCP の出力は OutputValidator を必ず通過する** ので、tool 側からの出力注入も封じています。

---

## トラブルシューティング（よくある詰まりどころ）

| 症状 | 原因 | 解決 |
|---|---|---|
| `ModuleNotFoundError: presidio_analyzer` | extras 未インストール | `pip install "llmesh-mcp[presidio]"` |
| `ModuleNotFoundError: numpy` | RAG/SPC を素の `pip install llmesh-mcp` で使った | `pip install "llmesh-mcp[rag]"` または `pip install numpy` |
| Ollama 接続失敗 | サーバ未起動 | `ollama serve`、またはコンストラクタに `base_url=` 指定 |
| 文字化け（Windows） | `cp932` がデフォルト | `set PYTHONUTF8=1`（PowerShell は `$env:PYTHONUTF8=1`） |
| OpenAI 互換 API でモデル名が通らない | プロバイダ独自のプレフィックス | `model="provider/model-name"` 形式を確認 |

困ったらまず：

```bash
python -m llmesh.cli.doctor
```

「動いていない理由を全部出す」ことに振った診断 CLI です。**初回セットアップでこれが一番早い**。

---

## ロードマップ的な現在地

| ver | 何が入った |
|---|---|
| v2.13 | Presidio PII / RAG MVP / 多変量 SPC コア |
| v2.14 | ExplainedCUSUM / VideoCUSUM / SqliteVectorStore / DNP3 / GOOSE |
| v2.15 | LSHVectorStore（ANN）/ 公開 API レイヤー / `API_STABILITY.md` |
| v2.16 | OWASP 静的監査クリーン |
| v2.17 | HTTP DoS hardening（全 HTTP クライアントにレスポンスサイズ上限） |
| v2.18 | 8 種ドキュメント新規（CONTRIBUTING / DEPLOYMENT / OBSERVABILITY / TROUBLESHOOTING …） |
| v3.0.0 | **API Stability Release**（SemVer 正式適用、`__all__` 契約化） |
| **v3.1.0** | **クラウド LLM 統合（OpenAI / Azure / Anthropic / OpenRouter / Together / Groq / Mistral / DeepSeek）** |

**v3.0.0 から SemVer 正式適用**。`docs/API_STABILITY.md` の公開シンボル一覧が契約です（minor は後方互換、major のみ破壊変更）。

---

## 次のステップ

```bash
# 何が動くか全部見たい
pip install "llmesh-mcp[industrial,vision,presidio,rag]"
python -m llmesh.cli.doctor
python -m llmesh.cli.status

# まず Quickstart スクリプト
python -c "from llmesh.llm import OllamaBackend; print(OllamaBackend(model='llama3.2').complete('hi'))"
```

- GitHub: <https://github.com/furuse-kazufumi/llmesh>
- PyPI: <https://pypi.org/project/llmesh-mcp/>
- License: MIT
- Issue 歓迎: <https://github.com/furuse-kazufumi/llmesh/issues>

---

## おわりに

「ローカルとクラウドを同じインターフェースで」「セキュリティ層を後から差し込める」「外部 DB なしで RAG が動く」 — この 3 点だけでも、最初の LLM プロトタイプから本番まで **同じコードでスケールできる** のがこのフレームワークの狙いです。
PR / Issue / 「○○ backend が欲しい」「△△ ベクトル DB が欲しい」歓迎です。
