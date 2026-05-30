# LLMesh Performance Characteristics — v2.14+

> **かみ砕いた説明（中学生にもわかるように）**
>
> この文書は、LLMesh という仕組みの中身が「どれくらい速く動くか」「どれくらいメモリを使うか」を、料理のレシピに目安の調理時間が書いてあるのと同じ感覚でまとめた早見表です。たとえば「個人情報を消す作業はだいたいまばたき一回より速い」というふうに、各部品の手間と所要時間を一覧にしています。数字はあくまで手元のパソコンで測った目安なので、使う機械やソフトの版が変われば前後します。
>
> 言葉の意味は [用語集（GLOSSARY.md）](./GLOSSARY.md) を参照してください。

---

LLMesh の主要モジュールの計算・メモリ特性をまとめたリファレンスです。
具体的な数値は `benchmarks/` 配下のベンチマーク結果（手元計測）を要約
したもので、環境（CPU、numpy バージョン、Rust 拡張の有無）で前後します。

---

## 1. Privacy Pipeline

| Stage | 計算量 | 1 prompt あたり目安 (10⁴ chars) | 備考 |
|-------|-------|----------------------------------|------|
| Layer 0 注入検出 | O(L · n_patterns) | 0.1–0.3 ms | regex の最初の match で短絡 |
| Layer 1 シークレット | O(L · n_patterns) | 0.2–0.6 ms | 同上 |
| Layer 1.5 Presidio | spaCy NLP 依存 | 5–30 ms | optional、未インストール時 0 ms |
| Layer 2 構造 | O(L) | 0.05 ms | 絶対パス + サイズチェック |
| **合計（Presidio 無し）** | | **0.3–1.0 ms** | 通常パス |

L = prompt 長。n_patterns は Layer 0/1 に組込まれた regex 数（v2.14 時点で 5/12）。

## 2. RAG（v2.14）

| Backend | Index O() | Search O() | 推奨規模 | 永続化 |
|---------|----------:|-----------:|---------:|--------|
| `NumpyVectorStore` | O(d) per add | O(n·d) per query | ≤ 10⁵ docs | `.npz`（atomic） |
| `SqliteVectorStore` | O(d + 1 INSERT) | O(n·d) per query | ≤ 10⁶ docs | sqlite WAL |
| `LSHVectorStore` | O(P·T·d) per add | O(候補·d) で平均 O(d) スキャン | ≥ 10⁶ docs | `.npz`（atomic） |

P = 平面数（既定 12）、T = テーブル数（既定 8）、d = 次元、n = 文書数。
LSH の目標 recall@10 ≥ 0.95（L2 正規化埋め込み + 軽い正規分布ノイズ
0.05σ）。実測 recall@10 = **0.92**（500 doc / 64 dim ベンチ、正確には
recall ベンチで 100 query 中 >85 ヒット、CI 緩和閾値）。

### 埋め込み器（embedder）

| Embedder | 1 text あたり |
|----------|--------------:|
| `MockEmbedder` (dim=64) | 0.05 ms |
| `OllamaEmbedder` (`nomic-embed-text`) | 50–200 ms（ローカル GPU/CPU 依存） |

## 3. Industrial — v3 Engines（v2.14）

### MTEngine / OnlineMTEngine

| 操作 | 計算量 | 目安 |
|------|------:|------|
| `MTEngine.fit(N, p)` | O(N·p² + p³) | N=10⁴ p=8 で ~30 ms |
| `MTEngine.md(x)` | O(p²) | p=8 で 5 µs |
| `OnlineMTEngine.score_batch(n, p)` | O(n·p²)（einsum） | n=10⁵ p=8 で ~80 ms |

### HotellingT2Chart

| 操作 | 計算量 | 目安 |
|------|------:|------|
| `fit(N, p)` | O(N·p² + p³) | N=10³ p=4 で ~5 ms（pinv コスト） |
| `score(x)` | O(p²) | p=4 で 3 µs |
| `score_batch(n, p)` | O(n·p²) | n=10⁵ p=4 で ~30 ms |

### EventDensityMap

| 操作 | 計算量 | 目安 |
|------|------:|------|
| `aggregate(n events, grid g²)` | O(n + g²) | n=10⁶ g=8 で ~25 ms（numpy bincount） |

### CUSUMChart / ExplainedCUSUM

| 操作 | 計算量 | 目安 |
|------|------:|------|
| `CUSUMChart.update(x)` | O(1) | < 1 µs |
| `ExplainedCUSUM.update(x)` (in-control) | O(1) | < 5 µs |
| `ExplainedCUSUM.update(x)` (alarm, テンプレート) | O(1) | 50–200 µs |
| `ExplainedCUSUM.update(x)` (alarm, LLM) | LLM レイテンシ依存 | 100 ms〜数秒 |

### VideoCUSUM

| 操作 | 計算量 | 目安 |
|------|------:|------|
| `ingest_*(t, v)` (in-control) | O(1) | < 5 µs |
| `ingest_*(t, v)` (alarm, no match) | O(buffer_size) | < 50 µs |
| `ingest_*(t, v)` (alarm + match) | O(buffer_size) | < 50 µs |

### LLMExplainer

| 操作 | 計算量 | 目安 |
|------|------:|------|
| `explain(event)` (template only) | O(message length) | 50–200 µs |
| `explain(event)` (LLM) | LLM 依存 | 100 ms〜数秒 |

### UnifiedSPC

| 操作 | 計算量 | 目安 |
|------|------:|------|
| `update(sensor, text)` | 内部チャート 2 つの更新 | < 50 µs（Xbar+Xbar） |

## 4. アダプタ（adapter） — v3-N7

### DNP3Adapter

- `poll()` 計算量 = O(driver の `read_static()` 結果数)
- 1 point あたり SensorEvent 生成は < 10 µs
- driver 依存（pydnp3）の wire レイテンシは別途

### GOOSEAdapter

- `step()` 計算量 = O(dataset_size)（最大 256）
- replay 防御は per-`goCBRef` の単一 dict lookup（O(1)）

## 5. Rust Extensions（v2.5+）

| 操作 | Pure Python | Rust | 倍率 |
|------|-----------:|-----:|----:|
| PointCloud encode (1M) | 4.0M pts/s | **24.1M pts/s** | **6.0×** |
| PointCloud decode (1M) | 3.7M pts/s | 5.9M pts/s | 1.6× |
| DVS encode (1M) | 3.4M evt/s | 5.5M evt/s | 1.6× |
| Pipeline + CUSUM | 190K events/s | – | – |

## 6. メモリプロファイル

| モジュール | 1 doc / 1 record あたり | 備考 |
|-----------|---------------------:|------|
| `NumpyVectorStore` (dim=64) | 256 B + text + metadata | float32 |
| `SqliteVectorStore` (dim=64) | 256 B BLOB + sqlite overhead | UPSERT 時のみ |
| `LSHVectorStore` (dim=64, P=12, T=8) | 256 B + 96 + bucket overhead | hash table |
| `EventDensityMap` (8×8) | 512 B (float64) | 固定 |
| `OnlineMTEngine` (p=8) | (p² + 2·p) × 8 = ~640 B | unit space |

## 7. テスト・CI 性能

- pytest 全スイート（v2.14）: **2253 passed, 29 skipped, 12 分 06 秒**
  （シングルプロセス、property-based 1200+ ケース含む）
- `pytest -n auto`（pytest-xdist）導入で 4–5× 短縮可能

## 8. 推奨運用パラメータ

| 用途 | バックエンド | 推奨パラメータ |
|------|-------------|---------------|
| ≤ 10⁵ docs / オンメモリ | NumpyVectorStore | dimension=64–384 |
| ≤ 10⁶ docs / 永続化 | SqliteVectorStore | WAL + dimension=384 |
| ≥ 10⁶ docs / ANN | LSHVectorStore | P=12 / T=8 (recall ≥ 0.92) |
| Edge / RTOS | C ABI（Volume L） | EdgeProfile + Rust 拡張 |
