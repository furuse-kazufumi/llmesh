<!--
title: Pure Python の 6 倍速い Rust 拡張と、ストリーミング再送・HTTP DoS 対策まで詰め込んだ Python ライブラリ — LLMesh 性能と信頼性の話
tags: Python,Rust,PyO3,maturin,パフォーマンス
-->

# Pure Python の 6 倍速い Rust 拡張と、ストリーミング再送・HTTP DoS 対策まで詰め込んだ Python ライブラリ — LLMesh 性能と信頼性の話

> Rust 拡張で 6× / multi-platform wheel / 信頼性プロトコル / HTTP DoS hardening
> `pip install llmesh-mcp`（Rust 拡張は **任意・自動 fallback**）

---

## 先に結論

| 操作 | Pure Python | Rust | 倍率 |
|------|-----------:|-----:|----:|
| PointCloud encode (1M) | 4.0M pts/s | **24.1M pts/s** | **6.0×** |
| PointCloud decode (1M) | 3.7M pts/s | 5.9M pts/s | 1.6× |
| DVS encode (1M) | 3.4M evt/s | 5.5M evt/s | 1.6× |
| Pipeline + CUSUM | 190K events/s | – | – |

ポイントは **「Rust が無くても動く」**。Rust 拡張は import に失敗したら **静かに Pure Python にフォールバック** します（明示的に環境チェックをかけたいなら `python -m llmesh.cli.doctor`）。

---

## 30 秒で性能を試す

```bash
# まず Pure Python で動かす
pip install llmesh-mcp
python -c "from llmesh.industrial.sensor_3d import PointCloud; \
import numpy as np; \
pts = np.random.rand(1_000_000, 3).astype('float32'); \
import time; t=time.perf_counter(); PointCloud.encode(pts); \
print(f'pure python: {1_000_000/(time.perf_counter()-t):,.0f} pts/s')"
```

Rust 版を入れる（任意）:

```bash
git clone git@github.com:furuse-kazufumi/llmesh.git
cd llmesh/rust_ext
python -m maturin build --release
pip install --force-reinstall target/wheels/*.whl
```

CI が **Linux × macOS × Windows × CPython 3.10/3.11/3.12 の 8 ターゲット** で wheel を吐くので、自分でビルドしなくても良いケースが増えています。

---

## なぜ Rust なのか（実装上の判断）

点群と DVS イベントは「**`numpy.ndarray` を入れて、bytes 1 本にして返す**」というシンプルな I/O 変換です。これは PyO3 で書くと **GIL を解放したまま並列化** できる典型例で、Pure Python の **2〜6 倍** が普通に出ます。

逆に **CUSUM / SPC / MT 法のような数値計算は numpy のままで十分速い**（einsum / 共分散 / Tikhonov）。なので Rust 化していません。**Rust 化はホットスポット限定** が方針です。

```
rust_ext/
├── Cargo.toml
├── pyproject.toml          # maturin の設定
└── src/
    ├── lib.rs              # PyO3 エントリ
    ├── pointcloud.rs       # encode/decode
    └── dvs.rs              # encode
```

---

## 信頼性プロトコル — ストリーミング通信を「ちゃんと」やる

長時間ストリームでは **「ACK / 再送 / 切断検出 / TTL 期限切れ」** を組み合わせないと、いずれメモリが破裂します。LLMesh は `MessageAssembler`（受信）と `ChunkSender`（送信）の 2 つで全部塞いでいます。

```
[正常完了]  受信: pop_completed() → STREAM_ACK 送信
            送信: handle_ack()    → 送信バッファ破棄

[欠落検出]  受信: check_timeouts() → RETRANSMIT 送信（1 回のみ）
            送信: handle_retransmit() → 欠落チャンクのみ再送

[切断検出]  受信: check_watchdog()  → True で切断シグナル
            送信: expire_old()      → TTL 超過バッファ自動破棄
```

**RETRANSMIT を 1 回しか送らない** のは、再送ループによる増幅攻撃を抑えるためです。
切断検出は `WatchdogTimer` の単一ソース（時刻は `llmesh.security.clock` の NTP チェック付き）。

```python
from llmesh.protocol import MessageAssembler, ChunkSender, WatchdogTimer

assembler = MessageAssembler(timeout=5.0)
sender    = ChunkSender(ttl=30.0)
watchdog  = WatchdogTimer(timeout=10.0)

# 受信側
for chunk in incoming:
    assembler.feed(chunk)
    while msg := assembler.pop_completed():
        handle(msg)
    for missing in assembler.check_timeouts():
        send_retransmit(missing)

# 送信側
sender.send(payload)
sender.expire_old()                # TTL 期限切れを掃除
```

---

## HTTP DoS Hardening（v2.17）

LLM 周辺は **HTTP 越しに巨大なレスポンスを食わされる** リスクが地味に大きいです。Ollama・OpenAI 互換・Webhook・RAG 用の埋め込みサーバ、全部 HTTP です。

LLMesh は `llmesh.security.http_limits.read_capped` を **全 8 個の HTTP クライアントに統一適用** しました。

```python
from llmesh.security.http_limits import read_capped

# 例: 任意の HTTP レスポンスをサイズ上限付きで読む
body = read_capped(response, max_bytes=8 * 1024 * 1024)   # 8 MiB
```

用途別キャップ:

| 用途 | 既定上限 |
|---|---:|
| LLM 補完レスポンス | 16 MiB |
| Embedding レスポンス | 8 MiB |
| センサー HTTP プル | 4 MiB |
| Webhook | 1 MiB |

**使う側は 1 行**。本体ライブラリ全体に効きます。

---

## テスト戦略 — 2300+ 件 + Hypothesis property-based 1,200 ケース

LLMesh は普通の例ベース pytest に加えて、**プロパティベース** を多用しています。`hypothesis` で:

- センサー時系列を **任意の dtype / 形状** で生成して SPC が落ちないことを検証
- メッセージ分割と再送を **任意の損失率** で生成して `MessageAssembler` がメッセージを保証することを検証
- Firewall に **Unicode 全範囲** の入力を流して fail-closed を検証

```python
# 例: MessageAssembler property test
@given(st.lists(st.binary(min_size=1, max_size=32), min_size=1, max_size=64),
       st.lists(st.integers(min_value=0, max_value=63), unique=True))
def test_assembler_recovers_arbitrary_loss(chunks, dropped_indices):
    ...
```

これで **「テストが通る = 動く」** にだいぶ近付きました。

---

## OWASP 静的監査をクリアし続ける

v2.16 で全コードベースに対して **Bandit + 自前レビュー** を一周しました。HIGH/MEDIUM をゼロに。
**たまたまクリーン** ではなく、CI で再発を止めています。コードベース全体で:

- `shell=True` ゼロ
- `pickle` ゼロ
- `yaml.load(unsafe)` ゼロ（`yaml.safe_load` のみ）
- `eval` / `exec` ゼロ
- 弱暗号 ゼロ

`subprocess` 呼び出しは **list 形式のみ**。文字列で渡すと shell 解釈の余地が生まれるので禁止しています。

---

## CycloneDX SBOM を吐く CLI

```bash
python -m llmesh.cli.sbom > llmesh.sbom.cdx.json
```

依存関係を CycloneDX 形式で吐きます。供給連鎖監査（GHSA / OSV）にそのまま流せます。

---

## 全体の動線（性能 + 信頼性）

```
   ┌────────────────────────────────────────────────────────┐
   │ Sensor / 3D / DVS                                      │
   │  ├ PointCloud.encode  (Rust 24.1M pts/s)              │
   │  └ DVS.encode         (Rust 5.5M evt/s)               │
   └───────────┬────────────────────────────────────────────┘
               │
               ▼
   ┌────────────────────────────────────────────────────────┐
   │ ChunkSender ─► [network] ─► MessageAssembler          │
   │   │                                  │                 │
   │   ACK / RETRANSMIT / TTL ◄───────────┘                 │
   │   WatchdogTimer (NTP-checked clock)                    │
   └───────────┬────────────────────────────────────────────┘
               │
               ▼
   ┌────────────────────────────────────────────────────────┐
   │ HTTP layer (read_capped on every client)              │
   │   LLM / Embedding / Webhook / Sensor pull             │
   └───────────┬────────────────────────────────────────────┘
               │
               ▼
   ┌────────────────────────────────────────────────────────┐
   │ Pipeline + CUSUM   190K events/s                       │
   └────────────────────────────────────────────────────────┘
```

---

## ベンチを再現する

```bash
git clone git@github.com:furuse-kazufumi/llmesh.git
cd llmesh
pip install -e ".[dev,industrial]"
pytest benchmarks/ -k bench --benchmark-only    # ローカル PC で再現可
```

CI artifact にも `bench-report.json` を残しています（`docs/PERFORMANCE.md` にモジュール別計算量とメモリ目安）。

---

## トラブルシューティング

| 症状 | 原因 | 解決 |
|---|---|---|
| Rust 拡張のビルド失敗 | `cargo` 未インストール | rustup から入れる、もしくは Pure Python のままで OK |
| maturin で「manifest path not found」 | `cd rust_ext` 忘れ | `rust_ext` ディレクトリで実行 |
| Windows で wheel が選ばれない | Python 3.10 未満 | 3.10+ にアップグレード |
| `pytest` が遅い | property-based の試行回数 | `--hypothesis-profile=ci` を使う |

---

## 試す（クイックリンク）

- GitHub: <https://github.com/furuse-kazufumi/llmesh>
- PyPI: <https://pypi.org/project/llmesh-mcp/>
- 仕様: `docs/API_STABILITY.md` / `docs/PERFORMANCE.md`
- License: MIT

---

## おわりに

性能と信頼性は、**「ホットスポットだけ Rust 化、それ以外は numpy で十分」「再送と TTL を組で扱う」「HTTP は全部キャップ」「テストはプロパティベース」** という地味な原則の積み重ねで作られています。
派手な仕掛けが無い代わりに、**24 時間動かし続けて壊れない** を狙っています。
