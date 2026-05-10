# llmesh デバッグ手法多角適用レポート (2026-05-10)

llove で展開したデバッグパイプライン (coverage / mypy / bandit / hypothesis /
textual-snapshot / 実バイナリ E2E) を llmesh にも適用した結果。

---

## サマリ

| 観点 | 数字 / 状態 |
|------|------------|
| ベースライン test count | **2442 PASS / 1 fail (既存 flaky tick) / 1 skip / ~7 分** |
| 全体 coverage | **84%** (12,616 statements / 2,043 missing) |
| ruff | All checks passed |
| bandit (HIGH) | No issues identified |
| mypy | 77 errors / 30 files → 修正可能箇所 3 件は **修正済** |
| 新規 property-based tests | **28 件 × ~1,400 random inputs** 全 PASS |

---

## 修正した実バグ (3 件)

### 1. `llmesh/privacy/sensor_summarizer.py:187` 型シグネチャ不一致

`_summarise_diagnostic(data: dict[str, Any])` の型注釈が呼び出し側
(`_summarise_diagnostic(raw: str | None)`) と一致しなかった。関数本体は
すでに `isinstance(data, str)` で両ケースを処理していたため、**型注釈
だけが事実とズレていた**。

**修正**: `data: str | dict[str, Any]` に変更。

### 2. `llmesh/cli/doctor.py:180` で存在しない `check_drift_ok` を import

`from llmesh.security.clock import check_drift_ok` が import-not-found 状態で、
`try/except Exception` でサイレント skip 化されていた。**ntp drift check
機能が無症状で死んでいた**。

**修正**: `llmesh/security/clock.py` に `check_drift_ok` 関数を新設
(既存 `check_clock_sync` の non-raising wrapper、`(ok: bool, drift: float)`
を返す)。これで `llmesh doctor` の ntp drift report が実機能化。

### 3. `llmesh/protocol/http_adapter.py:181-188` dead code

`_handle_http_request` メソッドが定義されていたが:
- どこからも呼ばれていない (grep 全 codebase + tests で参照 0)
- 参照する `self._JSONResponse` 属性がそもそも存在しない (mypy で attr-defined)
- 実際の HTTP 処理は `start()` 内のローカル `_msg_endpoint` で完結

**修正**: 該当メソッドを削除し、跡地に「削除済」コメントを残した。

---

## 報告のみ (本セッション外で要対応)

### A. `llmesh/protocol/pop3_adapter.py:197,234` 変数シャドウイング

```python
response, lines, _ = pop.retr(index)   # response は bytes (POP3 status)
...
response = asyncio.run(self._handler(unified))  # response は UnifiedMessage
```

同名 `response` で 2 つの異なる型を上書きしている。Python は許すが mypy は混乱する。
**ロジック上は動くが、後で「最初の `response` (POP3 ステータス)」を使いたく
なったときに既に上書き済みでバグ生む**。リネーム推奨。

### B. `llmesh/protocol/imap_adapter.py:202,207,209,212,218,224` IMAP API の bytes/str 不整合

`imap.fetch(msg_id, ...)` 等で `msg_id: bytes` を渡しているが imaplib stub は
`str` を期待。実 imaplib は両方受けるので動作するが mypy は警告。**stub 都合
だが、コードは bytes/str を明示変換した方が堅牢**。

### C. `llmesh/protocol/tcp_stream_adapter.py` の flaky test

`tests/test_protocol_tcp_stream.py::TestTickLoop::test_tick_called_during_server_connection`
が Windows dev 環境で reproducibly fail。

**根本原因**:
1. `TCPStreamAdapter().send()` は one-shot client (送受信完了後すぐ接続クローズ)
2. server 側 `_tick_loop` は `await asyncio.sleep(_TICK_INTERVAL=1.0)` してから
   tick を発火する
3. send round-trip は ~30ms で完了 → 接続クローズ → tick_task キャンセル
4. polling deadline (8s) は無意味 — 接続自体が死んでいる

**修正案** (本セッションでは適用せず):
- handler を `await asyncio.sleep(_TICK_INTERVAL * 1.2)` で遅延させる
  → 接続を tick 1 回分以上開きっぱなしにする
- または `_TICK_INTERVAL` を test 中だけ monkeypatch で 0.05 程度に縮める

**該当ファイル coverage**: `tcp_stream_adapter.py 21%` — 低 coverage + flaky
test = real risk。優先対応すべき。

---

## 新規追加テスト (28 件)

### `tests/test_property_unified_message.py` (11 件)

protocol 層中核 (UnifiedMessage / NodeAddress / codec) の round-trip 不変条件:
- NodeAddress.from_dict(addr.to_dict()) == addr
- UnifiedMessage 各種コンストラクタの dict / bytes round-trip
- make_response の correlation_id / target 反転
- chunk(STREAM_CHUNK / STREAM_END) 構築
- codec encode/decode JSON / 空 bytes ValueError / unknown codec

### `tests/test_property_audit_qos.py` (7 件)

監査ログ HMAC chain + QoS deadline:
- clean append → verify_chain() == True
- 1 文字改竄 → False (HMAC 整合性)
- 異なる key → False (key 不知攻撃者防御)
- 不存在ファイル → entry_count=0 + valid=False
- QoS is_expired の境界値 + 冪等性

### `tests/test_property_security.py` (10 件)

PerNodeRateLimiter (token bucket) + NonceStore (replay defence):
- 初期 capacity == burst / burst+1 で RateLimitExceeded
- reset() 後はフル容量
- per-node bucket 独立性
- nonce 1 度 accept → 2 度目 False (replay)
- 異なる nonce / 異なる node の独立性
- 32 桁 hex 以外の nonce → ValueError
- rate limit + nonce store の orthogonal evaluation

各テストは Hypothesis で 20-100 random samples を生成、合計 **~1,400 random
inputs** で「どんな valid 値でも不変条件が成り立つ」を検証。

---

## Coverage hotspot (要対応)

`< 70%` カバレッジモジュール (テスト追加候補):

| モジュール | 行数 | 未網羅 | カバレッジ |
|-----------|-----|--------|------------|
| `llmesh/__main__.py` | 416 | 416 | **0%** (entry point) |
| `protocol/tcp_stream_adapter.py` | 281 | 222 | **21%** |
| `rendezvous/client.py` | 47 | 36 | **23%** |
| `protocol/local_file_adapter.py` | 304 | 139 | **54%** |
| `orchestrator/node_client.py` | 94 | 38 | **60%** |
| `auth/verifier.py` | 51 | 20 | **61%** |
| `cli/doctor.py` | 103 | 35 | **66%** |
| `rag/__init__.py` | 13 | 4 | **69%** |

`tcp_stream_adapter.py` は flaky test の本拠地でもあるため最優先。

---

## 本セッションのコミット

- `4b01a3f` fix(types): mypy 検出の 3 件の実バグを修正
- `65bcad8` test: Hypothesis property-based tests for UnifiedMessage / NodeAddress / codec
- `2d6b077` test: Hypothesis property-based tests for audit chain / QoS / rate limiter / nonce store

---

## 次セッションで着手する候補

1. **flaky tick test を fix** (slow handler パターン)
2. **`tcp_stream_adapter.py` のテスト追加** (現 21% → 70%+)
3. **`rendezvous/client.py` のテスト追加** (現 23%、urllib 利用周り)
4. **pop3_adapter の response 変数リネーム** (シャドウイング解消)
5. **imap_adapter の bytes/str 統一** (stub 警告解消)
6. **`__main__.py` の boot smoke test** (現 0%)
