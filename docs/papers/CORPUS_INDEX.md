# LLMesh 分野別論文コーパス（RAD: Research Aggregation Directory）

LLMesh では `tools/collect_image_papers.py`（汎用クローラ）を使って
複数分野の論文メタデータを **JSONL コーパス** として収集します。
精密工学会向け 4 論文以外に、以下 8 分野のサブコーパスを管理します。

## 分野別コーパス一覧（**16 分野**）

### 応用分野（9 分野）

| 分野 | ディレクトリ | LLMesh 関連機能 |
|------|------------|---------------|
| **画像処理** | `image_corpus/` | SpatialSummarizer / ImageFirewall |
| **セキュリティ** | `security_corpus/` | PromptFirewall / 監査チェーン |
| **産業 IoT** | `industrial_iot_corpus/` | Modbus / OPC-UA / EtherCAT / BACnet |
| **MLOps / エッジ AI** | `mlops_corpus/` | EdgeProfile / ONNX バックエンド |
| **ゲーム開発** | `game_dev_corpus/` | NPC AI / Telemetry / Anti-cheat |
| **医療画像** | `medical_corpus/` | DICOM / FHIR / HIPAA |
| **車載 / ADAS** | `automotive_corpus/` | CAN / AUTOSAR / OBD-II |
| **重要インフラ** | `infrastructure_corpus/` | DNP3 / IEC 61850 / SCADA |
| **ロボティクス** | `robotics_corpus/` | ROS 2 / SLAM / 3D Sensor |

### 先端 AI / 量子分野（7 分野、v2.9 追加）

| 分野 | ディレクトリ | 重点トピック |
|------|------------|------------|
| **Deep Learning** | `deep_learning_corpus/` | 最適化 / 自己教師あり / scaling laws / 蒸留 |
| **Neural Networks** | `neural_network_corpus/` | SNN / GNN / Mamba / NeRF / 圧縮 |
| **LLM** | `llm_corpus/` | RLHF / DPO / MoE / 長文脈 / 推論連鎖 |
| **VLM / vLLM** | `vllm_corpus/` | CLIP / LLaVA / PagedAttention / 投機的復号 |
| **Quantum Computing** | `quantum_computing_corpus/` | QML / VQE / QEC / NISQ / QKD / 量子センサー |
| **Diffusion Models** | `diffusion_corpus/` | DDPM / Flow Matching / ControlNet / 3D / 音響 |
| **AI Agents** | `agents_corpus/` | ReAct / 関数呼出 / multi-agent / SWE-Agent |

### 数学・統計分野（5 分野、v2.10 追加）

| 分野 | ディレクトリ | 重点トピック |
|------|------------|------------|
| **多変量解析** | `multivariate_analysis_corpus/` | マハラノビス / PCA / 因子分析 / 判別 / Hotelling T² / MANOVA / MT 法 |
| **統計学・SPC** | `statistics_corpus/` | SPC (Xbar-R/CUSUM/EWMA) / ベイズ / MCMC / 仮説検定 / EVT / 因果推論 |
| **最適化** | `optimization_corpus/` | 凸最適化 / SGD 系 / LBFGS / LP/IP/MIP / メタヒューリスティクス / Bayesian optimization |
| **数値解析・線形代数** | `numerical_methods_corpus/` | SVD / QR / Krylov / テンソル分解 / FFT / FEM / 自動微分 |
| **情報理論** | `information_theory_corpus/` | Shannon / 相互情報量 / KL / ECC (LDPC/Polar) / レート歪み / 量子情報 |

これら数学分野は LLMesh の MTEngine（多変量）/ XbarRChart / CUSUMChart
（統計）/ numpy/scipy（数値）/ PromptFirewall（情報理論）の理論的基礎を支えます。

## 取得スクリプト（共通）

```bash
python tools/collect_image_papers.py \
    --source arxiv \
    --query "<query>" \
    --max-results 100 \
    --out docs/papers/<分野>/arxiv_<topic>.jsonl
```

> **注意**: ツール名は `collect_image_papers.py` ですが、汎用 arXiv /
> Semantic Scholar クローラとして任意分野で再利用できます。
> 自動トピック分類器も全分野共通です。

## レコードスキーマ（全分野共通）

```json
{
  "id": "arxiv:2401.12345",
  "title": "...",
  "abstract": "...",
  "authors": ["..."],
  "year": 2024,
  "categories": ["cs.CV", "cs.LG"],
  "url": "https://...",
  "source": "arxiv | semantic_scholar",
  "topics": ["AOI", "anomaly_detection"],
  "fetched_at": "..."
}
```

## 分野別キーワード（自動分類で付与されるトピックタグ抜粋）

すべて `tools/collect_image_papers.py` の `_TOPIC_RULES` で付与されます。

- `AOI` / `DVS` / `depth` — 画像処理
- `privacy` / `face_anon` / `pii` — セキュリティ・プライバシー
- `manufacturing` / `industrial` / `iot` — 産業 IoT
- `edge` / `mlops` / `quantization` — MLOps
- `npc_ai` / `procedural` / `telemetry` — ゲーム開発
- `medical` / `dicom` / `fhir` — 医療
- `can` / `autosar` / `adas` / `obd` — 車載
- `scada` / `dnp3` / `iec61850` — 重要インフラ
- `slam` / `ros` / `manipulation` — ロボティクス

## 統合運用フロー

1. 各分野 `queries.md` の標準クエリを実行 → 分野別 JSONL 取得
2. `corpus2skill` で階層スキル化（任意）:
   ```bash
   python -m llmesh corpus2skill \
       --source docs/papers/<分野>/ \
       --name <分野>_corpus \
       --hierarchy true
   ```
3. `/sourcehunt` 等で関連分野ヒントとして自動利用

## 倫理・ライセンス

- arXiv API: 無料、レート 3 秒/req
- Semantic Scholar: 無料、API キー任意
- 収集対象は **メタデータ（タイトル + アブストラクト）のみ**
- フルテキスト PDF は配布物に含めない（必要時 arXiv 直接取得）
- 引用時は各論文の推奨形式に従う

## CI 自動更新（オプション）

各分野週次クローラ案：
```yaml
schedule:
  - cron: "0 3 * * 1"   # 毎週月曜 03:00 UTC
jobs:
  refresh:
    steps:
      - run: python tools/collect_image_papers.py ... # 各分野
      - run: git commit -m "auto: refresh corpus"
```

頻繁な API 呼び出しは控え、週次〜月次を推奨。
