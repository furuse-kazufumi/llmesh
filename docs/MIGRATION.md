# Migration Guide

LLMesh のバージョン間移行ガイドです。SemVer 開始（v3.0.0 予定）以降、
**メジャーバージョン更新時は必ず本ドキュメントを確認**してください。

---

## 移行タイムライン

| From → To | 概要 | 互換性 |
|----------|------|-------|
| v0.x → v1.0 | プロトコル抽象化導入 | 互換維持（FastAPI handler は HttpAdapter 化） |
| v1.0 → v1.5 | Industrial 機能追加 | 100% 後方互換 |
| v1.5 → v2.0 | 産業全 Volume 完成 | 100% 後方互換 |
| v2.0 → v2.13 | E-2.1 Presidio + F-1 RAG + v3-N コア | optional 機能追加のみ |
| v2.13 → v2.16 | コードレビュー反映 + DoS 緩和 | 100% 後方互換 |
| v2.16 → v2.17 | HTTP DoS hardening | 100% 後方互換 |
| **v2.17 → v3.0** | **SemVer 正式適用、API 安定保証** | **後方互換維持予定（変更点は本ガイドに集約）** |

---

## v2.17 → v3.0（予定）

> **注**: v3.0 は API 安定性宣言のためのリリースであり、**機能の破壊的変更は最小限**です。
> 既存コードは多くの場合変更不要で、段階的な移行を推奨します。

### 1. パッケージインストール

```bash
# 旧（v2.17）
pip install "llmesh[industrial]"

# 新（v3.0）— 変更なし
pip install "llmesh[industrial]"
```

### 2. Optional extras 名の整理

v3.0 で extras を機能カテゴリ別に再編する可能性があります（変更時は
別途 `[Deprecated]` セクションに記載）。現時点では同じ名前を維持予定。

### 3. 廃止予定（Deprecation cycle 開始）

v3.0 で deprecation 警告が出る項目（v4.0 で削除予定）:

| 旧 | 新 | 移行例 |
|----|----|--------|
| （該当なし — v3.0 は安定版宣言のみ） | | |

### 4. 公開 API の取扱い

v3.0 以降、`docs/API_STABILITY.md` の「公開シンボル一覧」が**契約**になります:

- 公開 API のシグネチャ変更 → major bump（v3 → v4）
- 公開 API 追加 → minor bump（v3.0 → v3.1）
- バグ修正・内部最適化 → patch bump（v3.0.0 → v3.0.1）

### 5. 推奨コーディング規約（変更点）

v3.0 で正式に推奨される書き方:

```python
# ✅ 推奨: トップレベル import から
from llmesh import PromptFirewall, PresidioDetector, SensorEvent

# ⚠️ 動作するが Internal 扱い: サブパッケージ内部から直接
from llmesh.privacy.firewall import PromptFirewall
```

サブパッケージから直接 import するコードは動作し続けますが、
**SemVer の保証外**になります。

---

## v2.x 内部移行（破壊的変更なし）

### 既存コードへの新機能追加

#### Layer 1.5 PII 検出（v2.13+）を有効化

```python
# 旧（v2.12）
from llmesh.privacy import PromptFirewall
firewall = PromptFirewall()

# 新（v2.13+） — optional
from llmesh.privacy import PromptFirewall, PresidioDetector
firewall = PromptFirewall(presidio=PresidioDetector())
```

`presidio=None` がデフォルトなので何もしなければ従来動作のまま。

#### LSH ANN への移行（v2.15+）

```python
# 旧（v2.14）— 全件 cosine スキャン
from llmesh.rag import NumpyVectorStore
store = NumpyVectorStore(dimension=384)

# 新（v2.15+）— ≥10⁶ 件で ANN
from llmesh.rag import LSHVectorStore
store = LSHVectorStore(dimension=384, n_planes=12, n_tables=8)
```

`VectorStore` インターフェースは同一なので `Retriever` から見て
透過的に置換できます。

#### `.npz` ファイルの再保存（v2.16+）

v2.15 以前で保存した `.npz` ファイルは pickle ベース、v2.16+ は
JSON payload。**読み込み非互換**：

```python
# v2.15 → v2.16+ 移行スクリプト
from llmesh.rag import NumpyVectorStore

# 旧フォーマットで読み込み（v2.15 のコードを保持）
old = OldNumpyVectorStore.load("old.npz")

# 新フォーマットで再保存
new = NumpyVectorStore(dimension=old.dimension)
for doc_id, text, vec, md in old.iter_documents():
    new.add(Document(doc_id, text, vec, md))
new.save("new.npz")
```

> 旧形式から直接読みたい場合は v2.15 以前を pin してから export。

#### HTTP レスポンスサイズ上限（v2.17+）

```python
# 旧（v2.16） — caller が独自に制限
with urllib.request.urlopen(req) as resp:
    body = resp.read()

# 新（v2.17+） — 共通ヘルパー
from llmesh.security.http_limits import read_capped, DEFAULT_MAX_RESPONSE_BYTES
with urllib.request.urlopen(req) as resp:
    body = read_capped(resp, max_bytes=DEFAULT_MAX_RESPONSE_BYTES)
```

LLMesh の組み込みクライアント（OllamaBackend / OllamaEmbedder /
ImageSummarizer 等）は v2.17.0 で自動的に上限化済。**ユーザーコード
への影響なし**。

---

## v0.x → v1.0（過去）

`llmesh.adapters` が `llmesh.protocol` に移動:

```python
# v0.x
from llmesh.adapters.http import HttpAdapter

# v1.0+
from llmesh.protocol.http_adapter import HTTPAdapter
```

旧 import パスは v1.x の間互換性維持されていましたが、v2.0 で削除済。

---

## アップグレード手順（一般的な流れ）

```bash
# 1. 現在の wheel を確認
pip show llmesh | head -3

# 2. 新バージョンへ更新
pip install --upgrade llmesh

# 3. 自動 doctor で環境確認
python -m llmesh.cli.doctor

# 4. テスト実行
pytest

# 5. 本番デプロイ
# - 監査ログのバックアップを取る
# - blue-green でロールアウト推奨
# - 監視: error rate / latency P95 / firewall_block 率
```

---

## ロールバック手順

```bash
# 直前のバージョンに戻す
pip install "llmesh==<previous_version>"

# 監査ログは互換性あり（HMAC チェーン形式は v0.2 以来不変）
# .npz ファイルは v2.15 ↔ v2.16 で非互換 — 必要なら旧形式に再変換
```

---

## 互換性マトリクス

| 機能 | 最小サポート | 備考 |
|------|------:|------|
| Python | 3.11 | 3.12 / 3.13 でも動作 |
| Ollama | 0.1.20+ | embedding モデル必要なら 0.1.30+ |
| llama-server | b1500+ | OpenAI 互換 endpoint 対応版 |
| asyncua | 1.0+ | OPC-UA |
| pymodbus | 3.6+ | Modbus TCP/RTU |
| numpy | 1.26+ | scipy も同時 |
| Pillow | 10.0+ | vision extras |
| presidio-analyzer | 2.2+ | + spaCy 3.7+ |

---

## 質問・サポート

- 移行に詰まったら GitHub Issues
- Breaking change の議論は GitHub Discussions
