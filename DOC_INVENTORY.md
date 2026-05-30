# 📚 ドキュメント目録 — llmesh

> 自動生成 (`py -3.11 D:\tools\gen_doc_inventory.py <repo>`)。ファイル追加後に再実行で更新。
> **公開/内部フラグはヒューリスティックの仮判定**。公開前に必ず人手で確認すること。

- 総ドキュメント数: **61** （🌐 公開候補 7 / 🔒 内部? 10 / ❓ 要判断 44）
- コーパス・依存・仮想環境・.git は除外。

## 目次

- [(ルート)](#g0) (3)
- [docs](#g1) (34)
- [docs/demos](#g2) (1)
- [docs/linkedin](#g3) (3)
- [docs/market](#g4) (7)
- [docs/papers](#g5) (10)
- [docs/perf_comparison](#g6) (1)
- [out/research_e2e_demo/paper](#g7) (1)
- [tests/fixtures](#g8) (1)

<a id="g0"></a>

## (ルート) (3)

| ファイル | タイトル | 説明 | 更新 | 区分 |
|---|---|---|---|---|
| [CLAUDE.md](CLAUDE.md) | llmesh — Project Instructions | このファイルは Claude Code 等の AI 実装支援環境に対する指示書。 | 2026-05-23 | 🔒 内部? |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contributing to LLMesh | LLMesh への貢献を歓迎します。本ドキュメントは開発フローと品質基準を | 2026-05-09 | 🌐 公開候補 |
| [README.md](README.md) | LLMesh | Secure LLM Mesh over MCP — v3.1.0 | 2026-05-19 | 🌐 公開候補 |

<a id="g1"></a>

## docs (34)

| ファイル | タイトル | 説明 | 更新 | 区分 |
|---|---|---|---|---|
| [API_STABILITY.md](docs/API_STABILITY.md) | LLMesh API Stability Policy | LLMesh は v2.13 以降、Public API と Internal API を明確に分離し、 | 2026-05-08 | 🔒 内部? |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | LLMesh — アーキテクチャ概要 | Secure Local LLM Swarm over MCP | 2026-05-11 | ❓ 要判断 |
| [CHANGELOG.md](docs/CHANGELOG.md) | LLMesh Changelog | llmesh/speculative/ — メイン推論中に予測した分岐を idle peer へ Ed25519 署名付きで | 2026-05-25 | 🌐 公開候補 |
| [DEBUG_REPORT_2026_05_10.md](docs/DEBUG_REPORT_2026_05_10.md) | llmesh デバッグ手法多角適用レポート (2026-05-10) | llove で展開したデバッグパイプライン (coverage / mypy / bandit / hypothesis / | 2026-05-10 | ❓ 要判断 |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Deployment Guide | LLMesh の本番デプロイガイド。エッジ単機から複数ノード swarm までを | 2026-05-09 | ❓ 要判断 |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | Developer Guide | LLMesh の内部構造、開発フロー、新規モジュール追加手順、CI 構成を | 2026-05-09 | ❓ 要判断 |
| [GLOSSARY.md](docs/GLOSSARY.md) | Glossary | LLMesh に登場する用語集。LLM / セキュリティ / 産業用語を一元化します。 | 2026-05-09 | 🔒 内部? |
| [index.md](docs/index.md) | FullSense ™ — llmesh | Secure LLM Mesh over MCP | 2026-05-16 | 🌐 公開候補 |
| [INDUSTRIAL_GUIDE.md](docs/INDUSTRIAL_GUIDE.md) | LLMesh Industrial Guide (v2.0.0) | このガイドは LLMesh Industrial（Phase A〜G）の機能と使い方を網羅的にまとめた資料です。 | 2026-05-08 | ❓ 要判断 |
| [MIGRATION.md](docs/MIGRATION.md) | Migration Guide | LLMesh のバージョン間移行ガイドです。SemVer 開始（v3.0.0 予定）以降、 | 2026-05-09 | ❓ 要判断 |
| [OBSERVABILITY.md](docs/OBSERVABILITY.md) | Observability Guide | LLMesh の観測性（監視 / ログ / トレース / 監査）の構成と運用です。 | 2026-05-09 | ❓ 要判断 |
| [PEERING.md](docs/PEERING.md) | LLMesh — 複数PC間接続ガイド | PC-A (admin)            PC-B                    PC-C | 2026-05-05 | ❓ 要判断 |
| [PERFORMANCE.md](docs/PERFORMANCE.md) | LLMesh Performance Characteristics — v2.14+ | LLMesh の主要モジュールの計算・メモリ特性をまとめたリファレンスです。 | 2026-05-08 | ❓ 要判断 |
| [PLATFORMS.md](docs/PLATFORMS.md) | LLMesh 対応プラットフォーム一覧 | 本ドキュメントは LLMesh が公式サポートするプラットフォームの完全な | 2026-05-07 | ❓ 要判断 |
| [qiita-index.md](docs/qiita-index.md) | <!-- | LLMesh は 117 章 / 500+ 要件項目 / 2300+ テスト全 PASS の Python 統合フレームワークで、 | 2026-05-09 | ❓ 要判断 |
| [qiita-industrial.md](docs/qiita-industrial.md) | <!-- | pip install "llmesh-mcpindustrial" | 2026-05-09 | ❓ 要判断 |
| [qiita-intro.md](docs/qiita-intro.md) | <!-- | - LLMesh は、ローカル LLM（Ollama / llama.cpp）とクラウド LLM（OpenAI / Azure / Anthropic / OpenRouter / Groq / Together / Mistral / DeepSeek）を 同一 ABC で透過運用 できる Python 統合フレームワークです。 | 2026-05-09 | ❓ 要判断 |
| [qiita-llm-platform.md](docs/qiita-llm-platform.md) | <!-- | pip install llmesh-mcp | 2026-05-09 | ❓ 要判断 |
| [qiita-performance.md](docs/qiita-performance.md) | <!-- | ポイントは 「Rust が無くても動く」。Rust 拡張は import に失敗したら 静かに Pure Python にフォールバック します（明示的に環境チェックをかけたいなら python -m llmesh.cli.doctor）。 | 2026-05-09 | ❓ 要判断 |
| [qiita-security.md](docs/qiita-security.md) | <!-- | pip install "llmesh-mcppresidio" | 2026-05-09 | ❓ 要判断 |
| [REQUIREMENTS.md](docs/REQUIREMENTS.md) | LLMesh Edge Layer — 要件定義 (カテゴリB) | - llmeshmsgpack optional extra として msgpack=1.0 を追加（済み） | 2026-05-11 | ❓ 要判断 |
| [requirements_speculative_mesh.md](docs/requirements_speculative_mesh.md) | Speculative Mesh Execution — 要件定義 (本格導入に向けて) | docs/perfcomparison/speculativemesh.md の simulation より: | 2026-05-25 | ❓ 要判断 |
| [ROADMAP.md](docs/ROADMAP.md) | LLMesh Roadmap — v0.2.0 → v2.0.0 | LLMesh evolves from an HTTP/MCP-only local LLM mesh into a multi-protocol LLM | 2026-05-09 | ❓ 要判断 |
| [SECURITY.md](docs/SECURITY.md) | Security Policy — LLMesh v0.2.0 | Report vulnerabilities by opening a GitHub Security Advisory (private disclosure). | 2026-05-09 | 🌐 公開候補 |
| [SESSION_SUMMARY.md](docs/SESSION_SUMMARY.md) | Session Summary (auto-generated) | 8fcefe6 feat(core): research-orchestration primitives (Phase 0a) | 2026-05-11 | 🔒 内部? |
| [SESSION_SUMMARY_2026-05-05.md](docs/SESSION_SUMMARY_2026-05-05.md) | LLMesh セッションサマリー (2026-05-05) | LLMesh は Ed25519 署名認証付きのセキュアなローカル LLM スウォームフレームワーク。前セッションで LlamaCppBackend を実装済みだったが、セットアップウィザードと MCP サーバーには未統合だった。本セッションでその統合・動作確認を完了した。 | 2026-05-05 | 🔒 内部? |
| [SESSION_SUMMARY_2026-05-06.md](docs/SESSION_SUMMARY_2026-05-06.md) | LLMesh セッションサマリー (2026-05-06) | v0.9.0（Telnet + Cross-protocol hardening）が完了しており、v1.0.0 の実装を継続。 | 2026-05-06 | 🔒 内部? |
| [SETUP.md](docs/SETUP.md) | LLMesh — 環境構築ガイド | pip install -e ".dev" | 2026-05-05 | 🔒 内部? |
| [SETUP_GUIDE.md](docs/SETUP_GUIDE.md) | LLMesh 環境構築ガイド（AI/開発者共通） | このドキュメントは AI エージェント・新規開発者の両方 が読み取って | 2026-05-08 | ❓ 要判断 |
| [SPECIFICATION.md](docs/SPECIFICATION.md) | LLMesh Specification (v2.0.1) | LLMesh の正式仕様書 — Industrial Phase A〜G およびそれ以前の全機能の仕様を網羅。 | 2026-05-08 | ❓ 要判断 |
| [TESTING.md](docs/TESTING.md) | Testing Guide | LLMesh のテスト戦略・書き方・実行方法。新規テスト追加時の指針です。 | 2026-05-09 | ❓ 要判断 |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Troubleshooting | LLMesh のトラブルシューティング集。新着問題は GitHub Issues で報告 | 2026-05-09 | ❓ 要判断 |
| [USAGE.md](docs/USAGE.md) | LLMesh 使い方ガイド | LLMesh の 5 分クイックスタート から 本番運用 までを 1 ページにまとめた実践ガイドです。 | 2026-05-08 | 🌐 公開候補 |
| [v1.5.0-session-notes.md](docs/v1.5.0-session-notes.md) | v1.5.0 実装セッションノート — 2026-05-07 | - MT法（マハラノビス-タグチ法）の核心: 正常データの相関行列逆行列でスケールした距離 | 2026-05-07 | ❓ 要判断 |

<a id="g2"></a>

## docs/demos (1)

| ファイル | タイトル | 説明 | 更新 | 区分 |
|---|---|---|---|---|
| [clustering_demo.md](docs/demos/clustering_demo.md) | Capability Clustering Demo (RFC Phase 2a) | py -3.11 scripts/democlustering.py | 2026-05-16 | ❓ 要判断 |

<a id="g3"></a>

## docs/linkedin (3)

| ファイル | タイトル | 説明 | 更新 | 区分 |
|---|---|---|---|---|
| [post_2026-05-14_overview.en.md](docs/linkedin/post_2026-05-14_overview.en.md) | Running industrial IoT and LLMs in the same frame — llmesh | Most LLM conversations assume cloud-first, chat-first. The shop floor, the substation, the hospital, and the trading desk live under a completely different set of constraints: | 2026-05-14 | ❓ 要判断 |
| [post_2026-05-14_overview.ja.md](docs/linkedin/post_2026-05-14_overview.ja.md) | 産業 IoT と LLM を、同じフレームで動かす ― llmesh | LLM の議論の多くは クラウド前提・チャット前提 で進みます。一方、製造・電力・医療・金融といった現場では、 | 2026-05-14 | ❓ 要判断 |
| [post_2026-05-14_overview.zh.md](docs/linkedin/post_2026-05-14_overview.zh.md) | 让工业 IoT 和 LLM 在同一个框架里跑起来 — llmesh | 关于 LLM 的讨论，绝大多数默认了云端为先、对话为先。但车间、变电站、医院、交易席的现场，活在一套完全不同的约束里： | 2026-05-14 | ❓ 要判断 |

<a id="g4"></a>

## docs/market (7)

| ファイル | タイトル | 説明 | 更新 | 区分 |
|---|---|---|---|---|
| [competitor-matrix.md](docs/market/competitor-matrix.md) | 競合機能比較 matrix (Day 3) | — | 2026-05-18 | 🔒 内部? |
| [customer-personas.md](docs/market/customer-personas.md) | llmesh 想定顧客プロファイル (Day 2) | (教育 / 法務 / 建設 / 小売 / メディア等は将来 phase 拡張) | 2026-05-18 | ❓ 要判断 |
| [feature-pruning.md](docs/market/feature-pruning.md) | llmesh 機能 prune リスト — 不要 / 過剰 / 不足 (Day 6) | 戦略思索で「需要未定量」と判定された機能、または「他社が圧倒的に強い」領域: | 2026-05-18 | ❓ 要判断 |
| [fit-gap.md](docs/market/fit-gap.md) | llmesh 機能 fit gap 分析 (Day 5) | 戦略思索 PART 6 で Core / Extras 分割を提案. 既存機能の優先度を 4 段階で: | 2026-05-18 | ❓ 要判断 |
| [gap-analysis.md](docs/market/gap-analysis.md) | LiteLLM が届かない 3 領域 — gap analysis (Day 4) | LiteLLM 単体で行えること + LiteLLM が補完できる範囲を除外し、構造的に 不可能 な | 2026-05-18 | ❓ 要判断 |
| [reports-2026-05.md](docs/market/reports-2026-05.md) | llmesh 業界レポート収集 — 2026-05 (需要定量化スプリント Day 1) | 複数ソースで規模感を triangulate: | 2026-05-18 | ❓ 要判断 |
| [roadmap-v4-draft.md](docs/market/roadmap-v4-draft.md) | llmesh Roadmap 再構築 v3.2.0 → v4.0.0 draft (Day 7) | 1. Core を LiteLLM と勝負できる軽さに: Phase 3.6/3.7 + 産業 IoT + フェアネスを | 2026-05-18 | 🔒 内部? |

<a id="g5"></a>

## docs/papers (10)

| ファイル | タイトル | 説明 | 更新 | 区分 |
|---|---|---|---|---|
| [_bench_results.md](docs/papers/_bench_results.md) | LLMesh Industrial — Serialization & Pipeline Benchmarks | Repeats:  5  \| Workload sizes:  (1000, 10000, 100000, 1000000) | 2026-05-07 | ❓ 要判断 |
| [BULK_COLLECTION_GUIDE.md](docs/papers/BULK_COLLECTION_GUIDE.md) | 大量論文コーパス収集ガイド（v2.8 — 各分野 10,000+ 件目標） | LLMesh の RAD（分野別論文コーパス）は arXiv 単独では足りないため、 | 2026-05-07 | ❓ 要判断 |
| [CORPUS_INDEX.md](docs/papers/CORPUS_INDEX.md) | LLMesh 分野別論文コーパス（RAD: Research Aggregation Directory） | LLMesh では tools/collectimagepapers.py（汎用クローラ）を使って | 2026-05-07 | ❓ 要判断 |
| [datasets.md](docs/papers/datasets.md) | 論文素材データセット入手・準備手順 | 各論文（P1–P4）で使用する公開データセットの入手方法と、ライセンス・ | 2026-05-07 | 🔒 内部? |
| [paper1_spatial_summarizer.md](docs/papers/paper1_spatial_summarizer.md) | Paper 1 — SpatialSummarizer: Privacy-Preserving 3D-Sensor Description for Edge LLMs | - 公益社団法人 精密工学会（JSPE） | 2026-05-07 | ❓ 要判断 |
| [paper2_image_firewall.md](docs/papers/paper2_image_firewall.md) | Paper 2 — ImageFirewall: Multi-Stage Privacy Filtering for Industrial Vision Inputs | - 公益社団法人 精密工学会（JSPE） | 2026-05-07 | ❓ 要判断 |
| [paper3_aoi_llm_diagnostic.md](docs/papers/paper3_aoi_llm_diagnostic.md) | Paper 3 — AOI-LLM: Natural-Language Diagnostic Reasoning over AOI Defect Inspection | - 公益社団法人 精密工学会（JSPE） | 2026-05-07 | ❓ 要判断 |
| [paper4_dvs_industrial.md](docs/papers/paper4_dvs_industrial.md) | Paper 4 — DVS-LLM: Event-Camera Streams as Linguistic Inputs for High-Speed Precision Inspection | - 公益社団法人 精密工学会（JSPE） | 2026-05-07 | ❓ 要判断 |
| [RAD_RESEARCH_GUIDE.md](docs/papers/RAD_RESEARCH_GUIDE.md) | RAD（Research Aggregation Directory）運用ガイド — アイデア出し・調査支援 | LLMesh の RAD（21 分野・最大 21 万論文）を アイデア出し や 調査の補助資料 | 2026-05-16 | ❓ 要判断 |
| [README.md](docs/papers/README.md) | LLMesh 論文素材集 | 本ディレクトリは 公益社団法人 精密工学会（JSPE） へ投稿予定の 4 本の論文の | 2026-05-07 | 🌐 公開候補 |

<a id="g6"></a>

## docs/perf_comparison (1)

| ファイル | タイトル | 説明 | 更新 | 区分 |
|---|---|---|---|---|
| [speculative_mesh.md](docs/perf_comparison/speculative_mesh.md) | Speculative Mesh Execution — perf comparison (honest disclosure) | 本ファイルは 計測の方法論と記録様式を定義する。実測値は run ごとに追記する | 2026-05-25 | ❓ 要判断 |

<a id="g7"></a>

## out/research_e2e_demo/paper (1)

| ファイル | タイトル | 説明 | 更新 | 区分 |
|---|---|---|---|---|
| [paper_bundle.md](out/research_e2e_demo/paper/paper_bundle.md) | Research run bundle | - entries: 9 | 2026-05-12 | ❓ 要判断 |

<a id="g8"></a>

## tests/fixtures (1)

| ファイル | タイトル | 説明 | 更新 | 区分 |
|---|---|---|---|---|
| [dummy_paper.md](tests/fixtures/dummy_paper.md) | Toward Reproducible LLM Evaluation under Resource Constraints | We investigate how to evaluate the relative contribution of architectural | 2026-05-11 | ❓ 要判断 |
