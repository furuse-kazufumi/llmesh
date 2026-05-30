# Developer Guide

> **このドキュメントをひとことで（中学生にもわかる説明）**
> これは「LLMesh という道具を、開発者が自分で作り直したり部品を足したりするための説明書」です。
> 部屋の模様替えでいうと、家具の置き場所のルールや、新しい棚を安全に足す手順、
> 完成後に「ちゃんと動くか自動で点検するしくみ」のことが書いてあります。
> むずかしいカタカナや英語が出てきたら、下の用語集を見れば日本語のたとえで確認できます。
>
> 用語の意味は [`GLOSSARY.md`](GLOSSARY.md)（用語集）を参照してください。

LLMesh の内部構造、開発フロー、新規モジュール追加手順、CI 構成を
詳述します。エンドユーザー向けの使い方は [`USAGE.md`](USAGE.md)
を参照してください。

---

## 1. 環境要件

| 項目 | 推奨 | 最低 |
|------|------|------|
| Python | 3.11+ | 3.11 |
| OS | Linux / macOS / Windows 11 | 同左 |
| RAM | 8 GB+（テスト並列時 16 GB+） | 4 GB |
| Rust toolchain | 1.74+（拡張ビルド時のみ） | 1.70 |
| Disk | 2 GB（依存 + テストキャッシュ） | 1 GB |

### Python の調達

- Linux: `apt install python3.11 python3.11-venv` または `pyenv`
- macOS: `brew install python@3.11`
- Windows: 公式インストーラ + `py launcher`、もしくは
  `winget install Python.Python.3.11`

### Rust（任意 — Rust 拡張をビルドする場合のみ）

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup toolchain install stable
pip install maturin
```

---

## 2. ローカル開発セットアップ

```bash
git clone https://github.com/<org>/llmesh.git
cd llmesh

# 仮想環境
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

# 全機能 + 開発依存
pip install -e ".[dev,industrial,vision,presidio,rag,email,udp,ssh,ftp,mgmt,can,bacnet,vlm]"

# 動作確認
pytest -q --tb=line
ruff check llmesh/
bandit -r llmesh/ -ll
```

---

## 3. リポジトリ構成

```
llmesh/                  # ソースパッケージ
├── __init__.py          # 公開 API（__all__ + __version__）
├── classifier/          # DataLevel / ClassifiedPayload
├── privacy/             # PromptFirewall / PresidioDetector / Summarizer
├── identity/            # Ed25519 / X25519 / DID / CapabilityManifest
├── rendezvous/          # 署名付きノード発見
├── mcp/                 # Tool schemas / OutputValidator / SCA Gate
├── llm/                 # Backend ABC + Ollama / LlamaCpp
├── orchestrator/        # マルチノード fan-out
├── discovery/           # DNS-SD / Gossip / Registry
├── challenge/           # Capability evaluation
├── audit/               # Tamper-evident HMAC audit log
├── timeline/            # Per-task lifecycle store
├── auth/                # Request signer / verifier
├── routing/             # Latency / circuit breaker / contribution
├── fairness/            # Anti-freerider receipt ledger
├── security/            # endpoint_validator / clock / rate_limiter / cross_protocol / http_limits
├── industrial/          # Sensor adapters + analytics + v3-N modules
├── protocol/            # Multi-protocol adapter layer
├── rag/                 # Embedder / VectorStore / Retriever
├── config/              # TOML config / industrial config
└── cli/                 # llmesh.cli.{doctor,status,sbom}

rust_ext/                # Rust 拡張（PointCloud / DVS encode）
tests/                   # ユニット / hypothesis / integration
docs/                    # 本ドキュメント群
benchmarks/              # 性能測定スクリプト
examples/                # エンドツーエンド例
.github/workflows/       # CI: ci.yml / build-wheels.yml / security.yml
```

---

## 4. テスト戦略

詳細は [`TESTING.md`](TESTING.md) 参照。要点:

| 種類 | 場所 | 命名 | 実行 |
|------|------|------|------|
| ユニット | `tests/test_<module>.py` | `Test<Class>` / `test_<behavior>` | `pytest tests/test_x.py` |
| プロパティベース | `tests/test_<module>.py` | `from hypothesis import given` | 同上 |
| Integration / E2E | `tests/test_*_e2e.py` / `test_*_integration*.py` | 複数モジュール横断 | `pytest -k integration` |
| ベンチマーク | `benchmarks/*.py` | `bench_<x>` | `python benchmarks/bench_x.py` |

```bash
# 全テスト
pytest -q

# 特定モジュール
pytest tests/test_rag_retriever.py -v

# カバレッジ
coverage run -m pytest && coverage report -m

# 並列実行（pytest-xdist 必要）
pytest -n auto
```

---

## 5. 新規モジュール追加手順

### 例: 新規 ProtocolAdapter

```bash
# 1. 実装ファイル
touch llmesh/protocol/coap_adapter.py

# 2. ProtocolAdapter ABC を継承（adapter.py 参照）
# 3. UnifiedMessage への変換 / 認証 / レート制限を実装
# 4. registry に登録（pyproject.toml の entry-points）
```

`pyproject.toml`:
```toml
[project.entry-points."llmesh.adapters"]
coap = "llmesh.protocol.coap_adapter:CoapAdapter"
```

```bash
# 5. テスト
touch tests/test_coap_adapter.py

# 6. ドキュメント
# - docs/SPECIFICATION.md の "3. プロトコルアダプター仕様" に追加
# - docs/USAGE.md にサンプルコード
# - docs/CHANGELOG.md の [Unreleased] に Added エントリ
```

### 例: 新規 industrial 解析エンジン

```python
# llmesh/industrial/my_engine.py
from .mt_engine import _require_numpy

class MyEngine:
    def __init__(self, ...): ...
    def fit(self, data): ...
    def score(self, sample): ...
```

- numpy / scipy が必要なら `_require_numpy()` パターンで遅延 import
- テストは `pytest.importorskip("numpy")` で numpy 不在環境を skip

---

## 6. 公開 API への変更

公開 API（`llmesh/__init__.py` の `__all__` または各サブパッケージ
`__init__.py` の `__all__`）に追加 / 変更がある場合:

1. `__all__` を更新
2. `docs/API_STABILITY.md` の「公開シンボル一覧」を更新
3. SemVer に従って:
   - **追加のみ** → minor バンプ
   - **シグネチャ破壊** → major バンプ + deprecation cycle
4. テスト `tests/test_public_api.py` に新シンボルの import 動作確認を追加

---

## 7. CI 構成

`.github/workflows/`:

### `ci.yml`

各 PR / push で実行:
- pytest（全 OS）
- ruff check
- bandit
- coverage 計測 + 80% 閾値

### `build-wheels.yml`

タグ push で実行:
- 8 ターゲット（Linux x86_64 / aarch64, macOS x86_64 / arm64, Windows x86_64, manylinux）の wheel ビルド
- maturin で Rust 拡張同梱
- PyPI へ公開（要 secret）

### `security.yml`

週次 + 手動 trigger:
- bandit HIGH / MEDIUM
- safety / pip-audit
- CodeQL（GitHub）

---

## 8. リリース手順

1. `[Unreleased]` セクションを `[X.Y.Z] — YYYY-MM-DD` に切替
2. `pyproject.toml` の version を更新
3. `docs/ROADMAP.md` の表に新バージョン行を追加
4. 全テスト PASS 確認
5. tag を push（`git tag vX.Y.Z && git push --tags`）
6. CI が wheel をビルド・PyPI 公開
7. GitHub Release ノートを作成（CHANGELOG をコピー）

---

## 9. デバッグ

### Industrial debug

`llmesh/industrial/debug.py` で診断レコード（JSONL）を取得可能:

```python
from llmesh.industrial.debug import DiagnosticRecorder
rec = DiagnosticRecorder("debug.jsonl")
rec.record_event("anomaly", {"sensor_id": "x", "md": 4.2})
```

### CLI

```bash
python -m llmesh.cli.doctor    # 環境チェック（依存・wheel・config）
python -m llmesh.cli.status    # ランタイム状態
python -m llmesh.cli.sbom      # CycloneDX SBOM 自動生成
python -m llmesh audit verify <log_path>  # 監査チェーン検証
python -m llmesh timeline show --db <path> --limit 50
```

### Pytest デバッグ

```bash
pytest tests/test_x.py -v -s --tb=long --pdb   # 失敗時に pdb 起動
pytest tests/test_x.py -k "test_specific" --lf  # 直近の失敗のみ
```

---

## 10. ベストプラクティス

- **依存性注入**: 外部 I/O（HTTP / sqlite / LLM 呼び出し）は引数で
  受け取る。テストでは fake / mock を渡す。
- **フェイルクローズド（fail-closed）**: セキュリティ判定モジュールは例外で BLOCK / L4 を
  返す。捕捉漏れによるフェイルオープン（fail-open）を避ける。
- **副作用の分離**: pure な計算と I/O を関数レベルで分ける。
- **Optional 依存は遅延 import**: `def f(): import numpy as np` 形式で
  本体 import を軽量に保つ。
- **ドキュメント**: 公開 API には docstring 必須。fail mode と
  thread-safety 注釈を含める。

---

## 11. よくある落とし穴

- **`np.load(path, allow_pickle=True)` 禁止**: 信頼境界では RCE。
  `llmesh/rag/numpy_store.py` の pickle-free パターンを参考に。
- **`subprocess.run(..., shell=True)` 禁止**: list 形式のみ。
- **`resp.read()` 無制限**: `llmesh.security.http_limits.read_capped`
  で上限化。
- **テストで `time.sleep`**: 可能なら `monkeypatch` で時刻を注入し決定
  論的にする。
- **dataclass の mutable default**: `field(default_factory=...)` を使う。
