# LLMesh セッションサマリー (2026-05-05)

## 背景

LLMesh は Ed25519 署名認証付きのセキュアなローカル LLM スウォームフレームワーク。前セッションで `LlamaCppBackend` を実装済みだったが、セットアップウィザードと MCP サーバーには未統合だった。本セッションでその統合・動作確認を完了した。

---

## 実装変更

### 1. `llmesh/mcp/server.py` — バックエンド環境変数切り替え

**変更内容:** `_select_backend()` ファクトリ関数を追加し、Ollama/llama.cpp を env var で選択可能にした。

```python
# LLMESH_BACKEND=ollama|llamacpp  LLMESH_BACKEND_URL=http://...  LLMESH_MODEL=<name>
def _select_backend() -> LLMBackend:
    name  = os.environ.get("LLMESH_BACKEND", "ollama").lower()
    url   = os.environ.get("LLMESH_BACKEND_URL", "")
    model = os.environ.get("LLMESH_MODEL", "")
    kw: dict[str, Any] = {}
    if url:   kw["base_url"] = url
    if model: kw["model"]    = model
    if name == "llamacpp":
        return LlamaCppBackend(**kw)
    return OllamaBackend(**kw)

_llm_backend: LLMBackend = _select_backend()
```

| 環境変数 | 値 | デフォルト |
|---|---|---|
| `LLMESH_BACKEND` | `ollama` / `llamacpp` | `ollama` |
| `LLMESH_BACKEND_URL` | `http://...` | バックエンド依存 |
| `LLMESH_MODEL` | モデル名 | バックエンド依存 |

---

### 2. `scripts/llmesh_setup.py` — llama.cpp 検出対応

**`cmd_check`:** "-- Ollama --" セクションを "-- LLM backend --" に統合。Ollama と llama-server を両方チェックし、どちらか一方でOKとする。

```
-- LLM backend --------------------------------------------
  [OK] Ollama running at http://localhost:11434
       models: llama3.2:latest
  [??] llama-server not reachable (...)
```

**`cmd_autosetup` Step 3:** "Ollama (local LLM backend)" → "LLM backend (Ollama or llama-server)" に変更。両バックエンドをチェックし、両方とも不在の場合のみ `issues` に追加。

```
[STEP] 3/5  LLM backend (Ollama or llama-server)
  [OK] Ollama running at http://localhost:11434
  [??] llama-server not reachable at http://localhost:8080
```

---

### 3. `llmesh/llm/prompt.py` — nonce_echo 精度改善

**問題:** スキーマに `"caller_nonce_echo": "<32-hex-chars>"` と書いていたため、LLM が要求された nonce を無視して独自の値を生成するケースがあった（非決定的挙動）。

**修正:** 各ビルダーのスキーマ文字列に実際のリクエスト値を埋め込み、LLM に「コピーすべき具体値」を明示した。

```python
# 修正前
'"caller_nonce_echo": "<32-hex-chars>"'

# 修正後
f'"caller_nonce_echo": "{nonce}"'   # nonce = body.get("caller_nonce", "")
f'"task_id": "{task_id}"'           # task_id = body.get("task_id", "")
```

対象: `build_generate_code`, `build_review_code`, `build_generate_tests`, `build_critique_output`

---

### 4. `tests/test_backend_selection.py` — 新規テスト (10件)

| テストケース | 検証内容 |
|---|---|
| `test_default_is_ollama` | env var なし → OllamaBackend |
| `test_ollama_explicit` | `LLMESH_BACKEND=ollama` → OllamaBackend |
| `test_llamacpp_selected` | `LLMESH_BACKEND=llamacpp` → LlamaCppBackend |
| `test_unknown_backend_falls_back_to_ollama` | 不明値 → OllamaBackend (フォールバック) |
| `test_custom_url_applied_to_ollama` | `LLMESH_BACKEND_URL` が Ollama に適用される |
| `test_custom_url_applied_to_llamacpp` | `LLMESH_BACKEND_URL` が LlamaCppBackend に適用される |
| `test_custom_model_applied_to_ollama` | `LLMESH_MODEL` が Ollama に適用される |
| `test_custom_model_applied_to_llamacpp` | `LLMESH_MODEL` が LlamaCppBackend に適用される |
| `test_url_trailing_slash_stripped_ollama` | URL 末尾スラッシュが除去される |
| `test_url_trailing_slash_stripped_llamacpp` | URL 末尾スラッシュが除去される |

**テスト総数:** 448 passed (前回 438 + 新規 10)

---

## ローカルマルチノード動作確認

### 構成

```
node-a  127.0.0.1:8001   peer:9MtYPk183W...   fingerprint: b6:fc:35:...
node-b  127.0.0.1:8002   peer:D9MDkaQf9F...   fingerprint: c0:38:17:...
```

- LLM バックエンド: Ollama (llama3.2:latest)
- 認証: Ed25519 署名 + TOFU ピア登録
- TLS: 自己署名証明書生成済み（ローカルテストは HTTP）

### テスト結果

| # | テスト内容 | 期待 | 結果 |
|---|---|---|---|
| 1 | 未認証リクエスト | 401 | **OK** |
| 2 | node-a → node-b 署名付き LLM 呼び出し | 200 + nonce_echo 一致 | **OK** |
| 3 | リプレイ攻撃（同 nonce 再送） | 409 | **OK** |
| 4 | 不正署名 | 403 | **OK** |
| 5 | node-b → node-a 逆方向 LLM 呼び出し | 200 + nonce_echo 一致 | **OK** |

**5/5 全テスト通過。**

---

## 発見した運用上の注意点

### `LLMESH_TRUSTED_PEERS_PATH` は絶対パス必須

`llmesh/mcp/server.py` はモジュールインポート時（起動時）に以下を評価する：

```python
if _trusted_peers_path and Path(_trusted_peers_path).exists():
    app.middleware("http")(make_auth_middleware(_trusted_peers))
```

**相対パスを渡すと uvicorn プロセスの作業ディレクトリに依存し、ファイルが見つからず認証ミドルウェアが登録されない。** 起動コマンドには必ず絶対パスを使うこと。

```bash
# NG
LLMESH_TRUSTED_PEERS_PATH=nodes/node-a/config/trusted_peers.json uvicorn ...

# OK
LLMESH_TRUSTED_PEERS_PATH=/abs/path/to/nodes/node-a/config/trusted_peers.json uvicorn ...
```

---

## ノード起動コマンド（参考）

```powershell
$python = "C:/Users/puruy/AppData/Local/Programs/Python/Python311/python.exe"
$root   = "D:/projects/llmesh"

Start-Process $python `
    -ArgumentList "-m uvicorn llmesh.mcp.server:app --host 127.0.0.1 --port 8001" `
    -WorkingDirectory $root `
    -Environment @{
        LLMESH_NODE_IDENTITY_PATH = "$root/nodes/node-a/config/node.key.bin"
        LLMESH_TRUSTED_PEERS_PATH = "$root/nodes/node-a/config/trusted_peers.json"
    } -WindowStyle Hidden
```
