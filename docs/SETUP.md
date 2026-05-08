# LLMesh — 環境構築ガイド

## 必要条件

| ツール | バージョン | 用途 |
|--------|-----------|------|
| Python | 3.11 以上 | ランタイム |
| Ollama | 最新版 | ローカル LLM バックエンド |
| Docker + Docker Compose | v3.9以上 | 5ノード PoC 実行 |

---

## 1. ローカル開発環境のセットアップ

### 1-1. 依存関係のインストール

```bash
# プロジェクトディレクトリに移動
cd D:/projects/llmesh

# 開発用依存関係も含めてインストール
pip install -e ".[dev]"
```

インストールされるパッケージ:

| パッケージ | 用途 |
|-----------|------|
| `cryptography>=42.0` | Ed25519 鍵生成・署名 |
| `jsonschema>=4.21` | ツールスキーマ検証 |
| `base58>=2.1` | did:llmesh:1: エンコーディング |
| `fastapi>=0.111` | MCP HTTP サーバー |
| `uvicorn[standard]>=0.29` | ASGI サーバー |
| `pytest>=8.0` | テストフレームワーク（dev） |
| `pytest-cov>=5.0` | カバレッジ計測（dev） |
| `bandit>=1.8` | セキュリティ静的解析（dev） |
| `httpx>=0.27` | HTTP クライアント（dev） |

### 1-2. Ollama のセットアップ

```bash
# Ollama インストール後、使用モデルを pull
ollama pull llama3.2

# Ollama サーバーが起動していることを確認
ollama list
```

デフォルトエンドポイント: `http://localhost:11434`

### 1-3. テスト実行

```bash
# 全テスト実行
python -m pytest

# カバレッジ付き
python -m pytest --cov=llmesh --cov-report=term-missing

# 特定モジュールのみ
python -m pytest tests/test_sca_gate.py -v
```

期待結果: **526 passed, 0 failed**

### 1-4. セキュリティ静的解析

```bash
# bandit による危険なコードパターン検出
python -m bandit -r llmesh/ -c pyproject.toml
```

---

## 2. 単体ノードの起動（開発用）

```bash
# MCP サーバーを起動（ポート 8000）
uvicorn llmesh.mcp.server:app --host 0.0.0.0 --port 8000 --reload
```

### エンドポイント一覧

| メソッド | パス | 説明 |
|---------|------|------|
| `POST` | `/tools/generate_code` | コード生成 |
| `POST` | `/tools/generate_tests` | テスト生成 |
| `POST` | `/tools/review_code` | コードレビュー |
| `POST` | `/tools/critique_output` | 出力評価 |
| `POST` | `/registry/register` | ノード登録 |
| `GET` | `/registry/discover` | ノード探索 |
| `GET` | `/registry/health` | ヘルスチェック |
| `DELETE` | `/registry/{node_id}` | ノード削除 |

### ツール呼び出し例

```bash
curl -X POST http://localhost:8000/tools/generate_code \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "12345678-1234-4234-89ab-123456789abc",
    "code_request": "Fibonacci function in Python",
    "language": "python",
    "caller_nonce": "aabbccddeeff00112233445566778899"
  }'
```

---

## 3. 5ノード PoC（Docker Compose）

### 3-1. イメージビルドと起動

```bash
docker compose -f docker-compose.poc.yml up --build
```

### 3-2. ノード構成

| コンテナ | ポート | 役割 |
|---------|--------|------|
| `llmesh-node-a` | 8001 | `generate_code` |
| `llmesh-node-b` | 8002 | `generate_tests` |
| `llmesh-node-c` | 8003 | `review_code` |
| `llmesh-node-d` | 8004 | `critique_output` |
| `llmesh-orchestrator` | 8005 | オーケストレーター |

### 3-3. セキュリティ設定（自動適用）

Docker Compose により各コンテナに以下が強制される:

```yaml
read_only: true              # ルートファイルシステムを読み取り専用
cap_drop: [ALL]              # Linux ケーパビリティを全削除
security_opt:
  - no-new-privileges:true  # 権限昇格禁止
tmpfs:
  - /tmp:size=64m,noexec    # 書き込み可能な唯一のマウント（実行禁止）
networks:
  - llmesh-internal          # external: false（外部ネットワーク遮断）
```

### 3-4. ヘルスチェック

```bash
# 全ノードの状態確認
curl http://localhost:8001/registry/health
curl http://localhost:8005/registry/health

# オーケストレーターからノード一覧取得
curl http://localhost:8005/registry/discover
```

### 3-5. 停止

```bash
docker compose -f docker-compose.poc.yml down
```

---

## 4. SCA Gate の動作確認

`dependencies_added` にCVEがある依存を含めると `sca_blocked` で拒否される。

```bash
# 脆弱な依存関係を含むリクエスト（ブロックされる例）
curl -X POST http://localhost:8001/tools/generate_code \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "12345678-1234-4234-89ab-123456789abc",
    "code": "import requests",
    "language": "python",
    "explanation": "example",
    "dependencies_added": ["requests==2.0.0"],
    "generated_files": [],
    "cve_scan_requested": true,
    "caller_nonce_echo": "aabbccddeeff00112233445566778899"
  }'
```

SCA Gate は OSV API（`https://api.osv.dev/v1/querybatch`）に接続するため、ノードにインターネット接続（またはプロキシ）が必要。

---

## 5. ディレクトリ構成（参考）

```
D:/projects/llmesh/
├── llmesh/                  # メインパッケージ
│   ├── audit/               # 監査ログ（HMAC チェーン）
│   ├── challenge/           # ノード能力チャレンジ
│   ├── classifier/          # データ機密レベル分類
│   ├── discovery/           # ノード登録・探索
│   ├── identity/            # Ed25519 + DID
│   ├── llm/                 # LLM バックエンド
│   ├── mcp/                 # MCP サーバー・バリデーション
│   ├── orchestrator/        # ファンアウト・シンセサイザー
│   └── privacy/             # ファイアウォール・プライバシー要約
├── tests/                   # ユニット＋E2Eテスト（526件）
├── docker/
│   └── node/
│       └── Dockerfile       # ノードイメージ（non-root, 最小権限）
├── docker-compose.poc.yml   # 5ノード PoC 構成
├── pyproject.toml           # 依存関係・ビルド設定
├── ARCHITECTURE.md          # アーキテクチャ概要（本ドキュメント群）
├── SETUP.md                 # 環境構築ガイド（このファイル）
├── README.md                # プロジェクト概要
└── SECURITY.md              # セキュリティポリシー
```

---

## 6. トラブルシューティング

### Ollama に接続できない

```bash
# Ollama サービスを確認
ollama serve

# モデルが存在するか確認
ollama list
```

`OllamaBackend` はデフォルトで `http://localhost:11434` に接続する。

### SCA Gate でネットワークエラーが発生する

Docker Compose の `internal: true` ネットワーク設定により、コンテナから外部ネットワークへのアクセスが遮断されている。PoC 環境で SCA Gate を有効にする場合は `docker-compose.poc.yml` の `internal: true` を削除するか、OSV API プロキシを内部ネットワークに配置する。

### テストが収集エラーになる

`packages/llm_analysis/` ディレクトリが存在する場合、別プロジェクトの pytest 設定と競合することがある。`pyproject.toml` の `testpaths = ["tests"]` が正しく設定されていれば、`cd D:/projects/llmesh` から実行することで回避できる。

```bash
cd D:/projects/llmesh
python -m pytest  # testpaths が tests/ に限定される
```
