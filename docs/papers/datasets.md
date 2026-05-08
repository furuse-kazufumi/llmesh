# 論文素材データセット入手・準備手順

各論文（P1–P4）で使用する公開データセットの入手方法と、ライセンス・
ダウンロードスクリプト一式。

> **注意**: 公開データセットの一部は研究目的限定。商用利用可否はそれぞれ
> ライセンスを確認すること。本リポジトリはダウンロードキャッシュを
> 含めない（`D:/datasets/llmesh/` へ配置することを想定）。

## P1 — SpatialSummarizer

### MVTec AD（AOI 異常検知ベンチマーク）
- ライセンス: CC BY-NC-SA 4.0（非商用）
- サイズ: ~5 GB
- URL: https://www.mvtec.com/company/research/datasets/mvtec-ad
- 入手:
  ```bash
  mkdir -p D:/datasets/llmesh/mvtec_ad
  # フォーム送信後にメールで届く DL リンクから取得
  ```

### NYU Depth V2（深度カメラ評価）
- ライセンス: 研究目的フリー
- サイズ: ~2.8 GB（labeled subset）
- URL: https://cs.nyu.edu/~silberman/datasets/nyu_depth_v2.html
- 入手:
  ```bash
  curl -L -o D:/datasets/llmesh/nyu_depth_v2_labeled.mat \
       https://cs.nyu.edu/~silberman/datasets/nyu_depth_v2_labeled.mat
  ```

## P2 — ImageFirewall

### CelebA（顔検出ベンチマーク）
- ライセンス: 研究目的フリー
- URL: http://mmlab.ie.cuhk.edu.hk/projects/CelebA.html

### CCPD（中国ナンバープレート）
- ライセンス: MIT
- URL: https://github.com/detectRecog/CCPD

### 合成 PII 文書
- 自作: `tools/synth_pii_docs.py` で生成（PII を再現性ある形で埋め込み）

## P3 — AOI-LLM Diagnostic

### MVTec AD（再利用 — P1 と共通）

### DAGM 2007 — 表面検査ベンチマーク
- ライセンス: 研究目的フリー
- URL: https://hci.iwr.uni-heidelberg.de/content/weakly-supervised-learning-industrial-optical-inspection
- サイズ: ~3 GB

### NEU surface defect database
- ライセンス: 研究目的
- URL: http://faculty.neu.edu.cn/songkechen/zh_CN/zhym/263269/list/

## P4 — DVS Industrial

### DSEC（Davis Stereo Event Camera）
- ライセンス: CC BY-SA 4.0
- サイズ: ~150 GB（フル）/ サブセットあり
- URL: https://dsec.ifi.uzh.ch/

### N-MNIST / N-Caltech101
- ライセンス: 研究目的フリー
- URL: https://www.garrickorchard.com/datasets/

### 自作データ（Prophesee EVK4 録画）
- 録画スクリプト: `tools/prophesee_record.py`（要 Metavision SDK）
- 出力: `.raw` → `tools/prophesee_to_dvs_bin.py` で本リポジトリ形式に変換

## 合成データ生成（環境依存ゼロ、CI でも動作）

完全公開データセットを使えない CI / レビュアー向けに、
小規模な合成データを生成するスクリプトを同梱する：

```bash
python tools/gen_synthetic_dataset.py --type aoi --count 100 --out D:/datasets/llmesh/synth_aoi/
python tools/gen_synthetic_dataset.py --type depth --count 50 --out D:/datasets/llmesh/synth_depth/
python tools/gen_synthetic_dataset.py --type dvs   --count 200 --out D:/datasets/llmesh/synth_dvs/
```

合成データは固定シード（42）で再現可能。論文のリプロダクション節で、
公開データなしでも一部の数値を確認できるようにすることが目的。

## ストレージレイアウト（推奨）

```
D:/datasets/llmesh/
├── mvtec_ad/                # P1, P3
├── nyu_depth_v2_labeled.mat # P1
├── celeba/                  # P2
├── ccpd/                    # P2
├── dagm_2007/               # P3
├── neu_defect/              # P3
├── dsec/                    # P4
├── n_mnist/                 # P4
├── prophesee_local/         # P4 (自作)
└── synth/                   # 合成データ（CI 用）
    ├── synth_aoi/
    ├── synth_depth/
    └── synth_dvs/
```

## ライセンス遵守チェックリスト
- [ ] 各データセットが研究目的限定 / 商用可 のどちらかを論文に明記
- [ ] 配布物には公開データを含めない（`.gitignore` に登録済み）
- [ ] 引用形式は各データセットの推奨に従う
