# Testing Guide

LLMesh のテスト戦略・書き方・実行方法。新規テスト追加時の指針です。

---

## 1. テストピラミッド

```
        /\
       /E2E\         <-  5 件（v3 横断、tests/test_v3_integration_e2e.py）
      /----\
     / int  \        <-  数十件（複数モジュール組合せ、tests/test_*_integration_*.py）
    /--------\
   /  unit    \      <-  約 2200 件（単一モジュール、tests/test_*.py）
  /------------\
```

ガイドライン:
- **9 割はユニット**: モジュール単独で完結する pure logic
- **integration は重要パスのみ**: privacy + RAG、industrial + LLMExplainer 等
- **E2E は最小限**: v3 横断 5 件で公開 API のみで構成

---

## 2. 実行コマンド

```bash
# 全テスト
pytest -q

# 詳細出力
pytest -v --tb=short

# 特定ファイル
pytest tests/test_rag_retriever.py

# 特定クラス・テスト
pytest tests/test_firewall.py::TestLayer1::test_api_key_blocked

# キーワード一致
pytest -k "presidio or rag"

# 並列（pytest-xdist）
pytest -n auto

# カバレッジ
coverage run -m pytest && coverage report -m
coverage html  # HTML レポート
```

---

## 3. テストの書き方

### 3.1 命名規約

| 対象 | 命名 |
|------|------|
| ファイル | `tests/test_<module>.py` |
| クラス | `class Test<Class>:`（オプショナル） |
| 関数 | `def test_<expected_behavior>(...)` |
| Fixture | スネークケース、`tmp_path` / `monkeypatch` 等 stdlib に準拠 |

### 3.2 構造

```python
"""Tests for FooBar — short module summary."""
from __future__ import annotations

import pytest

from llmesh.foo import FooBar


class TestConstruct:
    """Argument validation tests live in their own class."""

    def test_invalid_dimension(self):
        with pytest.raises(ValueError):
            FooBar(dimension=0)

    def test_default_threshold(self):
        f = FooBar(dimension=4)
        assert f.threshold == 3.0


class TestBehavior:
    def test_happy_path(self):
        f = FooBar(dimension=4)
        out = f.process([1, 2, 3, 4])
        assert out.score > 0
```

### 3.3 Fixture vs パラメトライズ

```python
# Fixture（複数テストで共有する初期化）
@pytest.fixture
def fitted_engine(rng):
    eng = MTEngine()
    eng.fit(rng.normal(size=(100, 4)))
    return eng

def test_score(fitted_engine):
    assert fitted_engine.md([0, 0, 0, 0]) >= 0


# Parametrize（同じ振る舞いを多パターン検証）
@pytest.mark.parametrize("entity,expected", [
    ("CREDIT_CARD", "BLOCK"),
    ("PERSON", "SUMMARIZE"),
    ("UNKNOWN", "ALLOW"),
])
def test_action_per_entity(entity, expected):
    ...
```

---

## 4. Hypothesis（property-based）

ランダム入力で性質を検証する強力な手法。LLMesh では SPC / 監査チェーン /
プロトコル framing で多用しています。

```python
from hypothesis import given, strategies as st

@given(values=st.lists(st.floats(allow_nan=False, allow_infinity=False), min_size=10, max_size=1000))
def test_cusum_in_control_for_zero_mean_input(values):
    chart = CUSUMChart(target=0.0, k=0.5, h=4.0)
    out_of_control = sum(1 for v in values if not chart.update(v).in_control)
    # zero-mean input → expected drift rate is small
    assert out_of_control / len(values) < 0.2
```

設定:
```python
# tests/conftest.py
from hypothesis import settings, Phase

settings.register_profile("ci", max_examples=200, phases=[Phase.generate, Phase.shrink])
settings.register_profile("dev", max_examples=20)
settings.load_profile("ci")
```

---

## 5. Optional 依存のガード

numpy / scipy / Pillow / presidio は extras。テスト環境に未インストール
でも全テストが動くようにする:

```python
# tests/test_rag_lsh_store.py
pytest.importorskip("numpy")  # ファイル先頭で

# 以降の import / テストは numpy 環境のみで実行
import numpy as np
from llmesh.rag.lsh_store import LSHVectorStore
```

ファイル単位で skip すると pytest の collection 時に「N skipped」と
報告される。これは想定動作。

---

## 6. モック / Fake

### 推奨: 依存性注入 + 最小限の Fake

```python
# 良い例: Fake オブジェクトを直接渡す
class _FakeDriver:
    def read_static(self):
        return [DNP3Point(group=30, variation=1, index=0, value=1.0)]

def test_dnp3_poll_emits_event():
    adapter = DNP3Adapter("127.0.0.1", 20000)
    adapter.connect(driver=_FakeDriver())
    events = adapter.poll()
    assert len(events) == 1
```

### `monkeypatch` for 環境 / 標準ライブラリ

```python
def test_ollama_unreachable(monkeypatch):
    def _boom(*a, **kw):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr("urllib.request.urlopen", _boom)
    with pytest.raises(BackendError):
        OllamaBackend().invoke("x", {"prompt": "y"})
```

### 避けるパターン

- `unittest.mock.patch` の濫用（依存性注入で書き直せるなら避ける）
- 動的に内部メソッドを差し替え（リファクタで壊れる）
- ネットワーク I/O の実呼び出し（CI が flaky になる）

---

## 7. Async テスト

```python
# pyproject.toml で `asyncio_mode = "auto"` 設定済
@pytest.mark.asyncio
async def test_websocket_handshake():
    port = _free_port()
    a = WebSocketAdapter("127.0.0.1", port)
    await a.start()
    try:
        # ...
    finally:
        await a.stop()
```

タイムアウト系テスト:
```python
async def test_with_timeout():
    response = await asyncio.wait_for(coro(), timeout=2.0)
    assert response == ...
```

---

## 8. カバレッジ目標

| 範囲 | 目標 | 強制 |
|------|------|------|
| 公開 API（`__all__`） | ≥ 95 % | CI で gate |
| Internal モジュール | ≥ 80 % | 推奨 |
| Industrial 全体 | ≥ 80 % | CI で gate |
| Tests/ ディレクトリ | -- | 計測対象外 |

```bash
coverage run --source=llmesh -m pytest
coverage report --fail-under=80
```

---

## 9. CI 連携

`.github/workflows/ci.yml`:

```yaml
- name: Pytest
  run: |
    pip install -e ".[dev,industrial,vision,rag,presidio]"
    coverage run -m pytest -q
    coverage xml
    coverage report --fail-under=80
- uses: codecov/codecov-action@v4
```

---

## 10. パフォーマンステスト

`benchmarks/` 配下に benchmark スクリプトを置く（pytest 配下ではない）:

```python
# benchmarks/bench_lsh_recall.py
"""LSH recall@10 vs n_planes / n_tables sweep."""
import time
import numpy as np
from llmesh.rag import LSHVectorStore

# ...
print(f"recall@10={recall:.3f} latency_ms={latency * 1000:.2f}")
```

CI には組み込まず、リリース前に手動実行 → `docs/PERFORMANCE.md` に
反映。

---

## 11. テストデータ

- 大きいバイナリは `tests/fixtures/` に置かず、生成スクリプトから作る
- 個人情報や本物のシークレットを fixture に含めない
- 産業データ（DVS / 振動波形）は合成データを使う

```python
# tests/fixtures/synthetic_dvs.py
def synth_dvs_events(n=1000, seed=0):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 346, size=(n, 3))
```

---

## 12. Flaky テスト対策

- **時刻**: `monkeypatch.setattr("time.time", lambda: 1234567890.0)` で固定
- **ランダム**: 必ず `np.random.default_rng(seed)` または
  `random.Random(seed)` で seed 指定
- **並行**: タイムアウトに依存しない設計（イベント / Future で同期）
- **ファイルシステム**: `tmp_path` fixture で隔離

---

## 13. 既存テストの一覧

```bash
# テスト数 / カテゴリ別
ls tests/test_*.py | wc -l                            # 総ファイル数
pytest --collect-only -q | tail -3                    # 総テスト数

# カテゴリ別
pytest --collect-only -q tests/test_firewall.py       # privacy
pytest --collect-only -q tests/test_rag_*.py          # RAG
pytest --collect-only -q tests/test_*_adapter.py      # protocol / industrial
pytest --collect-only -q tests/test_v3_integration_e2e.py  # E2E
```

---

## 14. テスト追加チェックリスト

新機能の PR を出す前に:

- [ ] Happy path テスト
- [ ] エラー / 例外テスト
- [ ] 境界値（dim=0, top_k=0, empty input, max_size）
- [ ] Fail-closed テスト（セキュリティモジュールのみ）
- [ ] スレッド / 並行（該当する場合）
- [ ] パフォーマンス特性（O() を docstring に記載）
- [ ] Optional 依存のガード（numpy / Pillow / etc.）
- [ ] Property-based テスト（純粋関数の場合）
