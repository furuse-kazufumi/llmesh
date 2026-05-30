> **かみ砕いた説明**
> このページは、たくさんの AI のお手伝い役（仲間）の中から「日本語が得意」「数学が得意」といった希望に一番ぴったりの相手を自動で選ぶ仕組みの、お試し実行の記録です。たとえばクラスの係決めで「絵が得意な人」「計算が速い人」と条件を出すと、ぴったりの人が点数つきで上に並ぶようなイメージです。下の表は、その点数の高い順に選ばれた結果を示しています。
>
> 用語の意味は [用語集（GLOSSARY.md）](../GLOSSARY.md) を参照してください。

# Capability Clustering Demo (RFC Phase 2a)

> `scripts/demo_clustering.py` の実行スナップショット。`POST /registry/query`
> 経由で **capability-aware ピア（peer） matching** が動くことを確認するための
> end-to-end demo。FastAPI TestClient で in-process HTTP を回すので外部
> process は不要。

## 動かし方

```bash
# 標準出力に整形して表示
py -3.11 scripts/demo_clustering.py

# JSON dump 付き (CI / agent 渡し用)
py -3.11 scripts/demo_clustering.py --json
```

## Registered Peers

| node_id          | langs       | domains       | model | tools             | data levels |
|------------------|-------------|---------------|-------|-------------------|-------------|
| ja-code-7B       | ja          | code          | 7B    | chat+embed        | 0, 1        |
| en-code-7B       | en          | code          | 7B    | chat              | 0, 1, 2     |
| en-math-13B      | en          | math          | 13B   | chat+math         | 0, 1, 2     |
| multi-lang-7B    | ja+en+zh    | code+math     | 7B    | chat+embed+math   | 0, 1, 2     |
| private-only-7B  | ja          | legal         | 7B    | chat              | 0           |

## Query Results (期待される挙動)

### Q1: "Japanese coding assistance" — `preferred_domains=[code]`, `preferred_languages=[ja]`

| score | node_id        | 理由                                       |
|-------|----------------|--------------------------------------------|
| 1.00  | ja-code-7B     | ja ✓ + code ✓ (両 preferred を完全に満たす) |
| 1.00  | multi-lang-7B  | ja を含む ✓ + code ✓                       |
| 0.50  | en-code-7B     | code ✓ のみ (ja に該当なし)                |

### Q2: "Math + English" — `required_tools=[math]`, `preferred_languages=[en]`

| score | node_id        | 理由                                  |
|-------|----------------|---------------------------------------|
| 1.00  | en-math-13B    | math tool ✓ + en ✓                    |
| 1.00  | multi-lang-7B  | math tool ✓ + en を含む ✓             |

`math` tool を持たない peer (ja-code / en-code / private-only) は hard filter で除外。

### Q3: "High data sensitivity (level >= 2)" — `min_data_level=2`, `preferred_domains=[code, math]`

| score | node_id        | 理由                              |
|-------|----------------|-----------------------------------|
| 1.00  | multi-lang-7B  | level 2 ✓ + code+math 両方 ✓     |
| 0.50  | en-code-7B     | level 2 ✓ + code のみ            |
| 0.50  | en-math-13B    | level 2 ✓ + math のみ            |

`ja-code-7B` (level 0,1) と `private-only-7B` (level 0) は data level filter で除外。

### Q4: "Embedding tool required"

| score | node_id        |
|-------|----------------|
| 1.00  | ja-code-7B     |
| 1.00  | multi-lang-7B  |

`embed` tool を持つ peer のみ。

### Q5: "Anything goes" — `{}`

全 5 peer が score 1.0 で返る (no preferences → filter 全 pass)。

## 完全な実行ログ

`docs/demos/clustering_demo_run.txt` に最新の実行結果を保管 (再生成は
`py -3.11 scripts/demo_clustering.py > docs/demos/clustering_demo_run.txt`)。

## CI への組込み (任意)

```yaml
- name: Run clustering demo
  run: py -3.11 scripts/demo_clustering.py --json > demo-clustering.json
- uses: actions/upload-artifact@v4
  with:
    name: clustering-demo
    path: demo-clustering.json
```

## 関連

- `llmesh/discovery/clustering.py` — pure-function 抽象
- `llmesh/discovery/registry.py` — `NodeRegistry.find_matching()`
- `llmesh/discovery/router.py` — `POST /registry/query`
- `llive/docs/llmesh_p2p_mesh_rfc.md` — Phase 2a 設計 (FullSense umbrella)
