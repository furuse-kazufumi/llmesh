# Troubleshooting

> **かんたん説明（はじめての方へ）**
> このページは、LLMesh を動かしていて「エラーが出た」「動きがおかしい」ときの
> 直し方をまとめた“困ったときの早見表”です。症状（出てきたエラー文や、うまく
> いかない状態）を上から探して、その下に書いてある手順をそのまま試してください。
> 知らない言葉が出てきたら、用語集（[`GLOSSARY.md`](GLOSSARY.md)）で意味を
> 確認できます。
>
> 用語集: [`GLOSSARY.md`](GLOSSARY.md)

LLMesh のトラブルシューティング集。新着問題は GitHub Issues で報告
してください。

---

## 1. インストール / 環境

### `ModuleNotFoundError: No module named 'numpy'`

**原因**: `industrial` / `rag` / `vlm` extras 未インストール。

```bash
pip install "llmesh[industrial]"   # numpy + scipy + pyserial + pymodbus + asyncua + paho-mqtt
pip install "llmesh[rag]"          # numpy のみ
```

### `RuntimeError: pysoem is not installed`

**原因**: EtherCAT は Linux 限定。

```bash
sudo apt install python3-dev   # ヘッダ必要
pip install "llmesh[ethercat]"
sudo setcap cap_net_raw+ep $(which python3)   # 起動時 root でなくてもよくする
```

### `RuntimeError: pydnp3 is not installed; install llmesh[dnp3] or pass driver=`

**原因**: DNP3Adapter を実機接続で使うには `pydnp3` が必要。

- ユニットテストでは `adapter.connect(driver=fake_driver)` で driver
  を注入できます。
- 本番では `pip install "llmesh[dnp3]"`。

### `ResponseTooLargeError: HTTP response exceeded N bytes`

**原因**: v2.17.0 で導入したサイズ上限を超える応答。`docs/CHANGELOG.md`
の用途別キャップ表を参照。

**対処**:
- 正当な大量応答が必要なら、呼び出し側で `read_capped(resp, max_bytes=X)`
  を直接使い、用途に合わせた上限を指定。
- 暴走 / 悪意あるサーバー侵害なら設定上限のままで OK。

---

## 2. 起動 / ランタイム

### `RuntimeError: clock drift exceeds threshold`

**原因**: NTP 同期が崩れており、リプレイ防御の有効期間検証が誤動作する
リスクを検出。

**対処**:
```bash
# Linux
sudo systemctl restart systemd-timesyncd
sudo timedatectl set-ntp true

# Windows
w32tm /resync /force

# 環境変数で許容ドリフトを上げる（非推奨）
export LLMESH_MAX_CLOCK_DRIFT_S=30
```

### `OperationalError: database is locked`

**原因**: `SqliteNonceStore` / `SqliteVectorStore` を別プロセスから
書き込み中、かつ WAL モードが切れている。

**対処**:
```python
# WAL を明示有効化（v2.16+ では SqliteVectorStore は自動）
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
```

長時間ロックする処理は別接続に分離してください。

### `BackendError: ollama_unreachable`

**原因**: Ollama daemon が起動していないか、`OLLAMA_HOST` 設定がずれて
いる。

**対処**:
```bash
ollama serve &              # daemon 起動
curl http://localhost:11434/api/tags    # 疎通確認
```

### `BackendError: ollama_response_too_large:16777216`

**原因**: LLM 応答が 16 MiB を超過。通常はモデル設定（max_tokens）の
ミスか、generation loop。

**対処**:
- `max_tokens` を下げる
- `OllamaBackend(timeout=...)` でタイムアウトを短く
- 上限を引き上げたい場合は `llmesh/security/http_limits.py` の
  `DEFAULT_LLM_RESPONSE_BYTES` を caller でオーバーライド

### `firewall_block: layer0_injection_detected:pi_ignore_prior`

**原因**: Layer 0 prompt-injection 検出。"ignore previous instructions"
等のフレーズが含まれている。

**対処**:
- 正当なユーザー入力なら、上流で言い換える
- 開発時の検証なら一時的に `extra_patterns=[]` ではなく、
  該当 regex の改良案を PR に出してください

### `EmbeddingError: ollama returned malformed embedding`

**原因**: Ollama embedding API が想定外の JSON 形式で応答。
モデル名が間違っている可能性。

**対処**:
```bash
ollama pull nomic-embed-text   # 推奨モデル
```

### `RuntimeError: numpy is required for MT-method`

**原因**: `industrial` extras 未インストール。

```bash
pip install "llmesh[industrial]"
```

---

## 3. パフォーマンス

### MT-method の MD 計算が遅い

**症状**: `MTEngine.md(sample)` が 100 µs 以上。

**対処**:
- バッチ化して `OnlineMTEngine.score_batch(batch)` に切替（einsum で
  ベクトル化、p=8 で n=10⁵ が ~80 ms）
- Rust 拡張をビルド（PointCloud encode 6×、DVS encode 1.6×）

### RAG search が n=10⁵ で遅い

**症状**: `NumpyVectorStore.search` が 100 ms 以上。

**対処**:
- データを `LSHVectorStore` に移行（recall@10 ≥ 0.92 で平均 O(d) 動作）
- ANN ライブラリ（faiss / hnswlib）統合は v3.x 計画

### `np.vstack` で OOM（NumpyVectorStore に大量 add）

**症状**: 1 件ずつ add すると O(n²) のメモリコピーで遅くなる。

**対処**:
- `add_many(documents)` を使う
- 大規模データセットは `SqliteVectorStore` か `LSHVectorStore` を選ぶ

### 全テストが 12 分かかる

**対処**: `pytest -n auto` で 4-5× 短縮（要 `pip install pytest-xdist`）。

---

## 4. セキュリティ

### "プロンプトに名前が含まれていて毎回 SUMMARIZE になる"

**原因**: Layer 1.5 (Presidio) が PERSON エンティティを検出。

**対処**:
- 既定では PERSON は SUMMARIZE。BLOCK にしたい場合:
  ```python
  PresidioDetector(block_entities={"PERSON", "CREDIT_CARD", ...})
  ```
- そもそも PII 検査をオフにしたい場合は `PromptFirewall(presidio=None)`
  （既定）。

### "監査チェーンが verify で FAIL"

**原因**: ファイル書き込み中の中断、または改ざん。

**対処**:
```bash
python -m llmesh audit verify /path/to/audit.jsonl
# 出力: FAIL  first_error_seq=42  detail=hmac_mismatch
```
- `first_error_seq` 直前までは整合性が確認されている。以降のエントリは
  破棄して再起動が安全。
- 改ざんが疑われる場合はインシデント対応プロセスを起動。

---

## 5. 開発 / テスト

### `pytest --tb=long` でも原因不明

```bash
pytest tests/test_x.py -v -s --pdb       # 失敗時に pdb 起動
pytest tests/test_x.py --tb=auto -lvvv   # ローカル変数も表示
```

### Hypothesis が永遠にループする

**対処**:
```bash
export HYPOTHESIS_PROFILE=ci             # 200 examples で打ち切り
pytest tests/test_x.py
```

### `RuntimeError: There is no current event loop`

**原因**: pytest-asyncio が auto mode でない。

**対処**: `pyproject.toml` に既定で `asyncio_mode = "auto"` 設定済。
それでも出る場合は pytest を 8.0+ に更新してください。

### Windows でテストが Path-related で失敗

**対処**:
- パスは `pathlib.Path` を使う
- `tmp_path` fixture を使い `tmpdir` は避ける
- forward slash と backslash の混在は `Path` で吸収される

---

## 6. デプロイ

### Docker コンテナ起動時に "permission denied" (EtherCAT)

**対処**:
```dockerfile
RUN setcap cap_net_raw+ep /usr/local/bin/python3.11
```

または:
```bash
docker run --cap-add=NET_RAW ...
```

### systemd unit がリスタート無限ループ

**対処**: クロックドリフトが原因の場合がある。

```ini
[Service]
ExecStartPre=/usr/bin/timedatectl set-ntp true
ExecStart=/usr/bin/python -m llmesh serve-mcp
Restart=on-failure
RestartSec=5
StartLimitBurst=3
StartLimitIntervalSec=120
```

---

## 7. ログ / 観測性

### "ログが出ない / 多すぎる"

LLMesh は標準 `logging` モジュールを使用。設定例:

```python
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
# モジュール別細分化
logging.getLogger("llmesh.industrial").setLevel(logging.DEBUG)
logging.getLogger("llmesh.privacy").setLevel(logging.WARNING)
```

詳細は [`OBSERVABILITY.md`](OBSERVABILITY.md) 参照。

---

## 8. FAQ

### Q. macOS で Apple Silicon (M1/M2/M3) は対応？
A. 対応。`build-wheels.yml` で macOS arm64 wheel を配布しています。
Rust 拡張も aarch64-apple-darwin 対応。

### Q. 完全エアギャップ環境で動作する？
A. はい。Ollama / llama.cpp をローカル LLM として使えば外部通信ゼロ。
`PromptFirewall.presidio` は無効化、`PrivacySummarizer` のみ使用してください。

### Q. ログを Splunk / ELK に流したい
A. `logging` 経由なので任意の handler を attach 可能。`AuditTrace` は
JSONL 出力なので filebeat 等でそのまま取り込めます。

### Q. v2.x → v3.x の移行ガイドは？
A. [`MIGRATION.md`](MIGRATION.md) 参照。

### Q. 商用利用は可能？
A. MIT ライセンスです。`LICENSE` ファイルを確認の上、自由にお使い
ください。

### Q. プロジェクトに参加したい
A. [`CONTRIBUTING.md`](../CONTRIBUTING.md) 参照。
