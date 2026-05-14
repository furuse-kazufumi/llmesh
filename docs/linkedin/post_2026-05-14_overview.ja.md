# 産業 IoT と LLM を、同じフレームで動かす ― llmesh

> セキュア LLM Mesh over MCP ― `llmesh-mcp` v3.1.0 を設計・実装している話。
> AI の使い方を理解する手段として、また自分のキャリアの軸を作る手段として、このプロジェクトを進めています。

## なぜ作ったか

LLM の議論の多くは **クラウド前提・チャット前提** で進みます。一方、製造・電力・医療・金融といった現場では、

- データを社外に出せない
- 監査証跡を残さなければならない
- 既存プロトコル (Modbus / OPC-UA / MQTT / Serial / EtherCAT / BACnet…) と話す必要がある

という、まるで違う制約が並びます。`llmesh` は、この溝を **同じフレームの中** で埋めることを目指した個人プロジェクトです。
クラウド LLM (OpenAI / Azure / Anthropic / OpenRouter / Groq / Together / Mistral / DeepSeek) と、ローカル LLM (Ollama / llama.cpp) を **同一 ABC で透過運用** し、産業現場のプロトコルを最初から first-class で扱います。

## 設計の核

1. **プロトコル横断** — Modbus / OPC-UA / MQTT / Serial / EtherCAT / BACnet / HTTP(S) / WebSocket / gRPC / Email / SSH / FTP / SNMP / NTP を統一 framework で扱う。
2. **ローカル ↔ クラウド LLM の同一 ABC** — どの LLM プロバイダにも、同じ呼び出し方で繋ぐ。チャットだけでなく、tool 呼び出し / streaming / JSON mode を統一抽象。
3. **MCP 仕様準拠** — Anthropic Model Context Protocol に乗り、エージェント側はベンダフリーで連携できる。
4. **プライバシーパイプライン** — 4 層フィルタ (PII detect / mask / consent / audit) を素通しできない構造。
5. **TimelineStore** — sensor / SPC / RAG / audit / trace を 5 つ組 (`task_id, node_id, event_type, timestamp_utc, metadata`) で時系列保存。可視化側 (llove) は読み出しに専念。
6. **Trusted Peers + mTLS** — peer 名指しの allow-list で、社内ネット内に閉じる前提。
7. **Rust 拡張で 6×** — 性能クリティカルなホットパスは Rust に逃して、Python の使い勝手を保ったまま実用速度を出す。
8. **OWASP 静的監査クリーン** — `shell=True` / `pickle` / `eval` / SQL 注入 / 弱暗号 ゼロ。HTTP クライアントには全てサイズ上限。SemVer 正式適用。

## なぜキャリアの観点で重要だったか

LLM ブームは「派手な使い方」が話題を独占しがちですが、**プロダクトを止めるのは地味な制約** ばかりです。`llmesh` を作る過程で残ったのは、次のような実装ベースの言葉でした。

- 「クラウド LLM が使えない現場」で **何を諦めず**、**どこを置き換えるか** を設計レベルで判断できる。
- 産業プロトコル × LLM を **同じイベント時系列に乗せる** という、観測しやすい統合パターンを確立した。
- Rust と Python を **責務分離して同居** させる経験 (PyO3 + ホットスポット計測 + 5× ゲート) を積んだ。
- LLM 系 OSS では珍しい **OWASP 静的解析クリーン** + SemVer 運用を最初から維持してきた。

これらは、規制業界 / インフラ系 / 製造系 / 大企業内 SI の AI チームで、必ず聞かれる種類のスキルです。

## 数字で見る現在地 (2026-05-14)

- **v3.1.0** — Secure LLM Mesh over MCP。117 章 / 500+ 要件、2300+ tests 全 PASS。
- 全 OWASP 静的監査クリーン、SemVer 正式適用 (`docs/API_STABILITY.md` が公開シンボル契約)。
- Rust 拡張で性能 **6×**。
- ファミリー: バックエンド `llmesh` / TUI dashboard `llove` / 自己進化 LLM `llive` / 一括インストール `pip install llmesh-suite` (準備中)。
- PyPI: `pip install llmesh-mcp`。

## どこに向かうか

`llmesh` は、規制業界の現場で AI 導入を進めたいエンジニアが「実装を見て議論できる雛形」になることを目指しています。`llove` (TUI dashboard) と `llive` (自己進化型モジュラー記憶 LLM) を組み合わせると、クラウドを使わず、監査証跡を残し、現場で観測できる **LLM × 産業 IoT** スタックになります。

> GitHub: <https://github.com/furuse-kazufumi/llmesh>
> PyPI: `pip install llmesh-mcp`

#AI #LLM #IndustrialIoT #MCP #ModelContextProtocol #MLOps #Rust #OpenSource #個人開発 #キャリア
