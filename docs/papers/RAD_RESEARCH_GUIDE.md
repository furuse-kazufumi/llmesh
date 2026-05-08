# RAD（Research Aggregation Directory）運用ガイド — アイデア出し・調査支援

LLMesh の RAD（21 分野・最大 21 万論文）を **アイデア出し** や **調査の補助資料**
として活用するための実践ガイド。

> **AI 向け要約**: RAD は "論文メタデータの分野別 JSONL コーパス"。
> 21 分野 (応用 9 + 先端 AI 7 + 数学 5) を `docs/papers/<分野>_corpus/` に
> 蓄積し、`bulk_corpus_collector.py --all` で月次更新する。
> アイデア出し時は `grep -i "<keyword>" docs/papers/*/*.jsonl | head` で
> 既存論文の重複を確認、 `corpus2skill` でスキル化して `/sourcehunt`
> 等に流すか、RAG で LLM に直接食わせる。

---

## 1. 21 分野の全体マップ

```
応用 (9)              先端 AI / 量子 (7)        数学・統計 (5)
─────────────────     ──────────────────       ────────────────
image                 deep_learning             multivariate_analysis
security              neural_network            statistics
industrial_iot        llm                       optimization
mlops                 vllm                      numerical_methods
game_dev              quantum_computing         information_theory
medical               diffusion
automotive            agents
infrastructure
robotics
```

各分野には `queries.md`（標準クエリ）と将来の収集物 JSONL が入ります。

---

## 2. 補助資料としての利用パターン

### 2-1. **アイデア出し**（既存研究との差別化確認）

新機能の構想時に、関連分野の既存研究を即座にスキャン:

```bash
# キーワードで横断検索
grep -hi "spatial summarizer" docs/papers/*/*.jsonl | head -20

# 過去 3 年の論文だけ
python -c "
import json, sys
from pathlib import Path
for f in Path('docs/papers').rglob('*.jsonl'):
    with open(f, encoding='utf-8') as fp:
        for line in fp:
            d = json.loads(line)
            if d.get('year', 0) >= 2024 and 'AOI' in (d.get('title') or '').upper():
                print(f'{d[\"year\"]} | {d[\"title\"][:80]} | {d[\"source\"]}')
"
```

→ 「同じアイデアが既出か？」「どこを差別化するか」を 30 秒で確認。

### 2-2. **調査レポート作成**（分野横断サーベイ）

例: 「画像 + 産業 IoT で LLM を使った異常検知」というテーマで
3 分野からピックアップ:

```bash
for dom in image industrial_iot llm; do
    grep -hi "anomaly\|defect\|outlier" docs/papers/${dom}_corpus/*.jsonl | head -10
done > survey_anomaly_3domains.txt
```

### 2-3. **論文執筆の Related Work 自動生成**

```bash
# 該当分野の上位 50 論文を BibTeX 風に出力
python -c "
import json
from pathlib import Path
seen = set()
for f in Path('docs/papers/llm_corpus').glob('*.jsonl'):
    with open(f, encoding='utf-8') as fp:
        for line in fp:
            d = json.loads(line)
            key = d.get('title_hash')
            if key in seen:
                continue
            seen.add(key)
            print(f'@misc{{{d[\"id\"].replace(\":\", \"_\")}, title={{{d[\"title\"]}}}, year={{{d[\"year\"]}}}, url={{{d[\"url\"]}}}}}')
            if len(seen) >= 50:
                break
" > llm_related_work.bib
```

### 2-4. **LLM 連携**（RAG 投入で対話的調査）

```python
from pathlib import Path
import json

# 1 分野を 1 つのテキストに結合（RAG にロード）
def domain_to_corpus_text(domain: str) -> str:
    parts = []
    for f in Path(f"docs/papers/{domain}_corpus").glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            d = json.loads(line)
            parts.append(f"# {d['title']} ({d['year']}, {d['source']})\n"
                         f"{d.get('abstract','')}\n"
                         f"URL: {d['url']}\n")
    return "\n---\n".join(parts)

# Ollama / LlamaCpp 経由で対話的に問い合わせ
# 例: "MT 法と GMM の組合せ事例を 5 件挙げて" 等
```

### 2-5. **スキル化**（LLMesh 内で自動的に活用）

```bash
python -m llmesh corpus2skill \
    --source docs/papers/multivariate_analysis_corpus/ \
    --name multivariate \
    --hierarchy true
```

これで `/sourcehunt` などのコマンドが多変量解析の既知手法をヒントとして自動利用します。

---

## 3. 整理・更新ポリシー

### 3-1. **更新頻度**

| 分野グループ | 推奨頻度 |
|-----------|--------|
| LLM / Agents / Diffusion / VLM | 週次（変化が激しい） |
| 画像 / Robotics / mlops | 隔週 |
| 産業 IoT / 重要インフラ / 車載 | 月次 |
| 数学・統計 / 量子 | 月次〜四半期 |

### 3-2. **重複除去の運用**

蓄積された JSONL は時間と共に重複が増えるため、**月次クリーンアップ**を推奨:

```bash
python -c "
import json
from pathlib import Path
from tools.bulk_corpus_collector import dedupe_records

for dom_dir in Path('docs/papers').iterdir():
    if not dom_dir.is_dir() or not dom_dir.name.endswith('_corpus'):
        continue
    records = []
    for f in dom_dir.glob('*.jsonl'):
        records.extend(json.loads(line) for line in f.read_text(encoding='utf-8').splitlines() if line)
    unique = dedupe_records(records)
    out = dom_dir / 'consolidated.jsonl'
    with out.open('w', encoding='utf-8') as fp:
        for r in unique:
            fp.write(json.dumps(r, ensure_ascii=False) + '\\n')
    print(f'{dom_dir.name}: {len(records)} → {len(unique)} ({out})')
"
```

### 3-3. **分野再編・分割**

トピック量が偏ってきたら、分野を細分化:

| 旧 | 新 |
|----|----|
| `llm_corpus` | `llm_alignment_corpus` + `llm_efficiency_corpus` + `llm_eval_corpus` |
| `quantum_computing_corpus` | `quantum_algo_corpus` + `quantum_hardware_corpus` |

CHANGELOG に分割を記録しつつ、既存ディレクトリは symlink で互換維持。

---

## 4. 横断クエリ集

### 4-1. **テーマ別**: AI×産業

```bash
for dom in industrial_iot infrastructure automotive medical robotics; do
    python tools/bulk_corpus_collector.py --domain $dom --target 5000 \
        --queries "large language model deployment"
done
```

### 4-2. **理論×実装**: 多変量×LLM

```bash
python tools/bulk_corpus_collector.py --domain multivariate --target 5000 \
    --queries "Mahalanobis llm anomaly explanation"

python tools/bulk_corpus_collector.py --domain llm --target 5000 \
    --queries "tabular numerical reasoning"
```

### 4-3. **学際的**: 量子×医療

```bash
python tools/bulk_corpus_collector.py --domain quantum --target 3000 \
    --queries "quantum machine learning healthcare drug discovery"
```

---

## 5. アイデア発掘ヒューリスティクス

| 観点 | 操作 |
|------|------|
| **未踏領域**：論文数が少ないキーワード | `grep -c "<kw>" docs/papers/*/*.jsonl` で件数集計 |
| **トレンド**：直近 1 年の急増 | year >= 2025 でフィルタ → 件数推移 |
| **クロスドメイン**：複数分野で頻出 | 同じ keyword が複数ディレクトリにヒット |
| **手法移植**：A 分野の手法が B 分野では未試行 | A=多変量・B=ゲームでクエリ |
| **権威の発見**：頻出著者 | `authors` フィールドを集計 |

---

## 6. 注意・倫理

- **メタデータのみ**（タイトル + アブストラクト）を保管
- **フルテキスト PDF は配布物に含めない**（必要時 arXiv 等から直接取得）
- 引用時は各論文の推奨形式に従う
- 商用利用時は CrossRef polite pool / Semantic Scholar API key を取得

---

## 7. AI エージェント向けクイックレシピ

```bash
# 1. 全 21 分野の現在の状況を確認
for d in docs/papers/*_corpus/; do
    echo -n "$(basename $d): "
    cat $d/*.jsonl 2>/dev/null | wc -l
done

# 2. 特定分野の最新 10 件
ls -t docs/papers/llm_corpus/*.jsonl | head -1 | xargs head -10

# 3. アイデア候補を生成（LLM に投入）
python -c "
import json, glob, random
records = []
for f in glob.glob('docs/papers/*/consolidated.jsonl'):
    records.extend(json.loads(l) for l in open(f, encoding='utf-8'))
random.shuffle(records)
for r in records[:20]:
    print(f'- [{r[\"source\"]}] {r[\"title\"][:80]}')
" > ideas_seed.txt
```

---

## 8. 関連ドキュメント

- 21 分野インデックス: [`CORPUS_INDEX.md`](CORPUS_INDEX.md)
- 大量収集手順: [`BULK_COLLECTION_GUIDE.md`](BULK_COLLECTION_GUIDE.md)
- 個別分野クエリ: 各 `*_corpus/queries.md`
- 精密工学会 4 論文: [`paper1`〜`paper4`](README.md)
