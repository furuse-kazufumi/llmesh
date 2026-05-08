# Contributing to LLMesh

LLMesh への貢献を歓迎します。本ドキュメントは開発フローと品質基準を
プロジェクト一貫させるための指針です。

---

## 1. クイックスタート（新規貢献者向け）

```bash
# 1. リポジトリを fork して clone
git clone https://github.com/<you>/llmesh.git
cd llmesh

# 2. 開発依存をインストール
pip install -e ".[dev,industrial]"

# 3. テストが通ることを確認
pytest -q

# 4. ブランチを切る
git checkout -b feat/my-feature

# 5. 変更を加える + テスト追加
# 6. lint / セキュリティチェック
ruff check llmesh/
bandit -r llmesh/ -ll

# 7. PR を作成
git push origin feat/my-feature
```

---

## 2. コミット / PR の規約

### コミットメッセージ

[Conventional Commits](https://www.conventionalcommits.org/) を採用:

```
<type>(<scope>): <subject>

<body>

<footer>
```

| Type | 用途 |
|------|------|
| `feat` | 新機能 |
| `fix` | バグ修正 |
| `docs` | ドキュメントのみ |
| `test` | テスト追加 / 修正 |
| `refactor` | 機能変更なしの整理 |
| `perf` | パフォーマンス改善 |
| `chore` | ビルド / CI / 依存関係 |
| `security` | セキュリティ修正（CHANGELOG の Security セクションに集約） |

例:

```
feat(rag): add LSHVectorStore for ANN search

Random-hyperplane LSH with 12 planes × 8 tables. Recall@10 ≥ 0.92
on synthetic data. Implements VectorStore interface so it slots
into Retriever unchanged.

Closes #123
```

### PR チェックリスト

- [ ] `pytest -q` 全 PASS（skipped は理由を明記）
- [ ] `ruff check llmesh/` warning ゼロ
- [ ] `bandit -r llmesh/ -ll` HIGH / MEDIUM ゼロ
- [ ] 公開 API 変更がある場合は `__all__` を更新（`docs/API_STABILITY.md` 参照）
- [ ] 新機能には**ユニットテスト** + 該当する **integration test**
- [ ] `docs/CHANGELOG.md` の `[Unreleased]` セクションに記載
- [ ] 必要なら `docs/SPECIFICATION.md` / `docs/USAGE.md` / `README.md` も更新
- [ ] Optional 依存を追加した場合は `pyproject.toml` の extras と
      `docs/SETUP_GUIDE.md` を更新

### レビュー基準

PR は最低 1 名のメンテナーレビューが必須です。レビューでは以下を確認:

- セキュリティ不変条件（fail-closed、no shell=True、no pickle、入力サイズ
  上限など — `docs/SECURITY.md` 参照）
- 後方互換性（公開 API の変更は major bump 必要 — `docs/API_STABILITY.md`）
- テストカバレッジ（公開 API は ≥ 95%、Internal は ≥ 80% 推奨）
- パフォーマンス影響（`docs/PERFORMANCE.md` の特性表に変更があれば更新）

---

## 3. 何に取り組むか

### Good first issue

- ドキュメントのタイポ修正 / 例の追加
- skipped テストの解消（環境セットアップが必要なもの）
- `docs/USAGE.md` のサンプルコードを examples/ に切り出す

### より深い貢献候補

- **新規 ProtocolAdapter**: 既存 13 種の adapter を雛形に、新プロトコル
  をサポート（CoAP、AMQP、Kafka など）
- **新規 industrial エンジン**: 既存 SPC / MT / OnlineMT を拡張する
  解析器（PCA / オートエンコーダ / 拡散異常検知 etc.）
- **RAG バックエンド**: ChromaDB / Qdrant / sqlite-vec の `VectorStore`
  実装（既存 `NumpyVectorStore` / `SqliteVectorStore` / `LSHVectorStore`
  と同じインターフェース）
- **Volume B/C/N 系のテーマ**: `docs/REQUIREMENTS.md` の v3
  Implementation Plan / Volume N 参照

GitHub Issues の `good-first-issue` / `help-wanted` ラベルもチェック
してください。

---

## 4. 開発環境セットアップ

詳細は [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) 参照。要点のみ:

```bash
# Python 3.11+ 必須
python --version    # >= 3.11

# 推奨: 仮想環境
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate

# 全機能 + 開発依存
pip install -e ".[dev,industrial,vision,presidio,rag,email,udp,ssh,ftp,mgmt,can,bacnet,vlm]"

# Rust 拡張（オプション、6× 高速化）
cd rust_ext && python -m maturin build --release && cd ..
pip install --force-reinstall rust_ext/target/wheels/*.whl
```

---

## 5. セキュリティ報告

セキュリティ脆弱性は **公開 issue で報告しないでください**。
代わりに [`docs/SECURITY.md`](docs/SECURITY.md) の "Reporting
Vulnerabilities" セクションに従って非公開で連絡してください。

---

## 6. ライセンス

LLMesh は MIT ライセンスです。PR を提出することで、貢献内容が同じ
ライセンスで配布されることに同意したものとみなします。

---

## 7. 行動規範

すべての貢献者は相互尊重に基づいた建設的なコミュニケーションを
求められます。issue / PR / 議論で攻撃的・差別的な言動が確認された
場合、メンテナーは警告またはアカウント排除の対応を取ります。

---

## 8. 質問・議論

- バグ報告 / 機能要望: GitHub Issues
- 設計議論: GitHub Discussions
- 緊急のセキュリティ問題: `docs/SECURITY.md` の連絡先
