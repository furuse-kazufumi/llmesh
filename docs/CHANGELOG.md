# LLMesh Changelog

## [Unreleased]

### Added — 10-peer skill chunk replication demo + KPI 測定 (Phase 3.7)

`scripts/demo_skill_sync.py` で N virtual peer × M chunk の sync を
in-process で回し、RFC §評価指標 を自動判定。

- `--peers 10 --chunks 20 --chunk-size 51200 --rounds 5` defaults
- `InMemoryTransport` で `HTTPTransport` Protocol を満たし、socket 不要
- 各 round で全 peer pair 間 `sync_with` を実行、wall-clock 計測
- Coverage / 平均 storage / convergence round / total replication time
- 閾値判定 (PASS / FAIL):
  - replication round time (10 peer) < 60 s
  - final coverage > 0.9
  - max storage / peer < 2 GB
- `--json` で機械可読出力
- **Measured (10 peer × 20 chunk × 50 KB × 5 round)**: 1 round で
  coverage 1.000、total 843 ms、avg storage 1000 KB / peer、Overall PASS
- **Measured (12 peer × 30 chunk × 50 KB × 4 round)**: 1 round で
  coverage 1.000、total 1.5 s、avg storage 1500 KB / peer、Overall PASS

### Added — Router glue + rate limit + sync hook (Phase 3.6c)

`router.py` の write endpoints (`/skills/notify`, `/skills/<id>/report-corrupt`)
に PeerReputation 連携と per-peer RateLimiter を追加。`SkillSyncClient`
は pull 成功時に `reputation.record_transfer` を自動呼出。

- `router.set_reputation(rep)` — singleton 注入。report-corrupt body の
  `against` を `record_corruption(against, reporter=by, skill_id=…)` に転送
- `router.RateLimiter` — sliding-window (`max_events` / `window_s`)、stdlib
  `deque` + `threading.RLock` + 注入可能 `clock`。超過で `HTTP 429
  rate_limited`。peer 識別は `X-Peer-Id` → `X-Forwarded-For` → `client.host`
  の優先順 (fairness control、security boundary ではない)
- `SkillSyncClient(reputation=...)` — sync_with の pull 成功毎に
  `record_transfer(peer_url)`。skip / deny / fail はカウントしない
- backward-compat: `against` が無い場合は in-memory queue にだけ記録
  (既存 contract 維持)、`reputation_updated: false` を返す
- 8 new tests、47/47 関連 tests PASS、ruff clean

### Added — PeerReputation (Phase 3.6b)

SQLite-backed rolling-window reputation tracker for skill chunk peers.
RFC §Security 「Malicious peer 検出」準拠。

- `PeerReputation(db_path, *, window_s=30d, warn_threshold=0.7, block_threshold=0.5)`
- `record_transfer(peer_id)` / `record_corruption(peer_id, reporter, skill_id)`
- `score(peer_id) -> float` (1.0 が完璧、0.0 が最悪)
  - score = `1 - corruption_count / max(1, transfer_count)` clamped to [0,1]
- `verdict(peer_id) -> "trusted" | "warn" | "blocked"`
- `reputation_filtered(peers)` — blocked のみ除外、warn は保持して log
- `prune()` で window 外 row を削除
- Thread-safe (`RLock`)、テスト用 `clock=` 注入で時間進行を直接制御
- Unknown peer はデフォルト trusted (block-on-no-data だと mesh 参加不可)
- 10 new tests、ruff clean

### Added — License filter on SkillSyncClient (Phase 3.6a)

`SkillSyncClient(license_filter=...)` を追加。pull 後 / `replica.put` 前
に `chunk.license` を gate。RFC §License filter 準拠の AllowList ヘルパ
`allow_licenses(...)` + `DEFAULT_ALLOWED_LICENSES`
(`Apache-2.0`, `MIT`, `BSD-3-Clause`, `BSD-2-Clause`, `CC0-1.0`, `CC-BY-4.0`)
を提供。

- `LicenseFilter = Callable[[SkillChunk], bool]`
- 空 license / 未登録 license は reject
- filter 例外も reject 扱い (trust boundary を弱めない)
- `SyncResult.denied_license: tuple[str, ...]` 追加 (Phase 3.5 `denied`
  と区別)
- 4 new tests (allow / reject / default-set / exception-as-reject)
- 53/53 skill 関連 tests PASS、ruff clean

### Added — Approval gate (`policy=`) on SkillSyncClient (Phase 3.5)

`SkillSyncClient` に `policy: PullPolicyCheck | None = None` を追加。
`sync_with` の各 chunk pull 直前に `policy(peer_url, skill_id)` を呼び、
返値が `"approved"` でなければ pull を skip して `SyncResult.denied` に
記録する。

- `PullPolicyCheck = Callable[[str, str], "approved" | "denied"]` —
  llive `@govern` / `ApprovalBus` を **caller 側で wrap** すれば自動 gate
  になる Callable インタフェース (llmesh は llive に依存しない)
- policy callable が例外を投げた場合は **deny として扱う** —
  buggy gate が trust boundary を弱めない設計
- `SyncResult.denied: tuple[str, ...]` 追加 (failed と区別)
- 3 new tests (allow + deny + exception)、48 skill 関連 tests PASS

### Added — Skill chunk Pull / Push / Gossip protocol (Phase 3.4)

Phase 3.3 で立てた `/skills/*` HTTP router の **クライアント側** を実装。
peer 間で skill chunk を pull / push / gossip するプロトコル。

- `llmesh.skills.SkillSyncClient` — stdlib `urllib` ベースの HTTP client
  - `pull_chunk(peer_url, skill_id)` — `/skills/<id>` から取得
  - `pull_index(peer_url)` — `/skills/index` 一覧
  - `notify(peer_url, skill_id, ...)` — `/skills/notify` push
  - `sync_with(peer_url, replica, max_pulls=8)` — index diff → 欠損 chunk pull
- `llmesh.skills.GossipScheduler` — `threading.Thread` daemon で `tick()` を
  周期実行 (default 30 s)。`peer_provider: Callable[[], Iterable[str]]` で
  TrustedPeers / NodeRegistry と疎結合
- `HTTPTransport` Protocol で `urllib` を差し替え可能 → fastapi
  TestClient adapter で in-process テスト
- 12 new tests, 46/46 skill 関連 tests PASS, ruff clean

approval gate (Phase 3.5) は外側に置く方針 (本 client は policy-agnostic)。

### Added — `/timeline/ingest` endpoint (F25 f, llive bridge)

外部プロデューサ (主に llive、将来は MQTT bridge 等) が TimelineStore
に event を push できる HTTP endpoint を追加。読み出し側 (`GET
/timeline/recent` / `/timeline/task/{id}`) は既存のまま、ingest した
event を透過的に取り扱う。

llove リポジトリの `docs/llove_llive_bridge.md` v1 仕様 (2026-05-14
凍結) に準拠。Phase 2 OBS-03 の 3 種データを受け付ける。

#### 新規 endpoint

- `POST /timeline/ingest` — body schema:
  ```json
  {
    "task_id":   "<UUID v4>",
    "node_id":   "<= 128 chars, optional (defaults to X-Node-Id header)>",
    "event_type": "route_trace | concept_update | bwt_summary",
    "metadata":  { ... }
  }
  ```
  Response 200: `{"stored": true}`

#### バリデーション

- **task_id**: UUID v4 厳密検証。`uuid.UUID(s)` で parse 後 `version == 4`
  チェック (`uuid.UUID(s, version=4)` は version bit を上書きしてしまう
  ため、より厳しい validation を実装)
- **event_type**: `_ALLOWED_INGEST_EVENT_TYPES` allow-list で許可された
  3 種のみ受理。内部 event 名 (`completed` 等) は ingest 不可
- **metadata**: object のみ。`_RESERVED_METADATA_KEYS`
  (`task_id` / `node_id` / `event_type` / `timestamp_utc`) を含むと 422
  (TimelineStore の positional 引数と衝突を防ぐ)
- **node_id**: body の `node_id` を優先、無ければ `X-Node-Id` ヘッダ。
  128 文字上限 (header / body 双方)

#### セキュリティ

- 既存の `_security_headers` / `_body_size_limit` (64 KB) /
  `_json_only` middleware を継承
- 既存 `_rate_limiter` (per-node, 10 req/s burst 20) を継承
- Trusted Peers / mTLS 認証も既存 middleware 経由でそのまま適用

#### バグ修正 (派生)

- UUID v4 検証ロジックの不備を ingest endpoint で明示的に修正。
  既存 `/tools/{tool_name}` の同種ロジックは互換性のため変更なし。

#### テスト

`tests/test_timeline.py::TestTimelineIngest{Disabled,Enabled}` で 20 件
追加 (timeline 503 / happy path 3 種 event / round-trip / node_id
header/body 優先 / task_id 4 種 validation / event_type 2 種 / metadata
3 種 / node_id 2 種 / body 3 種)。フルスイート 42 件 PASS。

---

## [3.1.0] — 2026-05-09

### Added — クラウド / ホステッド LLM バックエンド（F-6 — Volume F 拡張）

LLMesh は当初「Secure Local LLM Swarm」として位置づけられたが、運用
現場ではローカル LLM とクラウド LLM の混合構成が要件となるため、主要
プロバイダの公式サポートを追加。`LLMBackend` ABC は不変で、既存の
`OllamaBackend` / `LlamaCppBackend` と完全互換。

#### 新規モジュール
- `llmesh/llm/openai_compatible.py` — OpenAI v1 chat-completions 互換
  バックエンド（`OpenAICompatibleBackend`）
  - 単一クラスで OpenAI / Azure OpenAI / OpenRouter / Together /
    Groq / Mistral / DeepSeek / vLLM / TGI 等を吸収
  - 認証ヘッダ柔軟化: `Authorization: Bearer ...` / `api-key: ...`
    両対応（Azure 用）
  - `response_format={"type":"json_object"}` 対応プロバイダで JSON
    モード自動指定（`response_format_json` パラメータ）
  - プロバイダ別 factory 関数: `openai_backend()` /
    `azure_openai_backend(resource, deployment)` /
    `openrouter_backend()` / `groq_backend()` / `together_backend()` /
    `deepseek_backend()` / `mistral_backend()`

- `llmesh/llm/anthropic_backend.py` — Anthropic Messages API 専用
  バックエンド（`AnthropicBackend`）
  - `x-api-key` + `anthropic-version` ヘッダ
  - `content` 配列の最初の `text` ブロックを抽出
  - `claude-haiku-4-5` / `claude-sonnet-4-6` / `claude-opus-4-7` 対応

#### セキュリティ不変条件
- レスポンスサイズは `read_capped(max_bytes=DEFAULT_LLM_RESPONSE_BYTES)`
  で 16 MiB 上限（v2.17 hardening を継承）
- API キーは環境変数（`OPENAI_API_KEY` / `AZURE_OPENAI_API_KEY` /
  `ANTHROPIC_API_KEY` 等）または引数で受領、ログ出力なし
- HTTPError / URLError / TimeoutError を `BackendError` に統一変換
- `OutputValidator` / `PromptFirewall` / `AuditTrace` 統合は既存パス
  のまま（プロンプトはクラウドへ届く前に L4 BLOCK / L3 SUMMARIZE）

#### `llmesh/llm/__init__.py` 公開 API
- 新規 export: `OpenAICompatibleBackend`, `AnthropicBackend`,
  `openai_backend`, `azure_openai_backend`, `openrouter_backend`,
  `groq_backend`, `together_backend`, `deepseek_backend`,
  `mistral_backend`
- `__all__` 更新（v3.0.0 SemVer 契約に従い、新規追加のみで minor バンプ）

#### テスト
- `tests/test_openai_compatible_backend.py` — 27 件（construct /
  headers / invoke / factories / error paths）
- `tests/test_anthropic_backend.py` — 16 件（construct / headers /
  invoke / health / error paths）
- 計 43 件全 PASS、追加依存ゼロ（urllib のみ）

### Added — 要件定義書 Volume F-6 セクション
- `docs/REQUIREMENTS.md` に **F-6 クラウド / ホステッド LLM 統合**
  セクションを追加（F-6.1 OpenAI 互換、F-6.2 Anthropic、F-6.3
  セキュリティ不変条件、F-6.4 拡張プロセス、F-6.5 受入基準、F-6.6
  Optional extras / 依存）
- Volume F 優先順位を「v3.1+ 反映版」として更新（F-1 / F-6 完了
  マーク）

### Use cases unlocked
- **オンプレ機密処理 → クラウド高度推論** のハイブリッド構成
- **回線断時のフォールバック**（プライマリ Ollama、フォールバック OpenAI）
- **マルチモデル A/B 評価**（同一プロンプトを異なる backend に投げて比較）
- **OpenRouter 経由のマルチプロバイダルーティング**

### Version
- `pyproject.toml` を v3.0.0 → v3.1.0 に昇格（minor: 公開 API 追加のみ、
  破壊的変更なし — SemVer 準拠）。

---

## [3.0.0] — 2026-05-09 — **API Stability Release**

### Major — SemVer 正式適用開始

LLMesh は v3.0.0 で **API 安定保証** を正式に開始します。本リリースは
**機能上の破壊的変更を含みません**。v2.18 からの移行はパッケージ
バージョンを上げるだけで完了します（`pip install --upgrade llmesh`）。

### What changes

| 観点 | v2.x まで | **v3.0.0 以降** |
|------|---------|--------------|
| バージョン規約 | 実質 minor 相当（互換維持の努力ベース）| **SemVer 厳密適用**（major.minor.patch）|
| 公開 API の境界 | 暗黙的 | **`__all__` + `docs/API_STABILITY.md` で契約化** |
| Deprecation プロセス | アドホック | **最低 1 minor の警告期間 → 次 major で削除** |
| Internal API の保証 | なし | **明示的に「保証なし」**（`_` 始まり / 未エクスポート）|
| ABI / wire-protocol | 規格依存 | **規格依存のまま（RFC / IEEE / IEC 等）** |

### What's the same

- すべての公開クラス・関数のシグネチャ
- 既存の Optional extras（`industrial` / `vision` / `presidio` / `rag` 他）
- 環境変数名（`LLMESH_*`）
- 設定ファイル（`llmesh.toml`）
- 監査ログ / NonceStore / TimelineStore のフォーマット
- wire-protocol（HTTP / TCP / UDP / SSH / SMTP / Modbus / OPC-UA / DNP3 / GOOSE 等）

### Compatibility statement

v2.x からのアップグレードはコード変更不要です。**ただし**、
v3.0.0 以降は `docs/API_STABILITY.md` の「公開シンボル一覧」が**契約**
となるため、Internal API（プライベートサブモジュールへの直接 import）
を使用しているコードは将来の minor / patch でも壊れる可能性があります。

詳細な移行ガイドは `docs/MIGRATION.md` を参照してください。

### Audit at v3.0.0 release

- **テスト**: 2275 passed / 30 skipped / 0 failed（v2.17.0 → v3.0.0 完全互換）
- **OWASP 静的監査**: クリーン（`shell=True` / `pickle` / `eval` / SQL 注入 / 弱暗号 / 無制限 `resp.read()` ゼロ）
- **公開 API smoke**: `tests/test_public_api.py` 10 件全 PASS
- **ドキュメント**: 全 19 種（README + `docs/*.md` × 16 + `CONTRIBUTING.md`）が v3.0 整合

### Version
- `pyproject.toml` を v2.18.0 → **v3.0.0** に昇格。
- `llmesh/__init__.__version__` のフォールバック値も 3.0.0 に更新。

### What's next

v3.x の minor リリースで段階的に追加予定:

- **v3.1+**: 実機 wire-protocol 統合（pydnp3 driver / libiec61850 / Ollama LLaVA）
- **v3.2+**: ANN ベクトルストア追加バックエンド（sqlite-vec / chromadb 統合）
- **v3.x**: Volume N の B 分類テーマ実装（外部要件成立時）
- **v3.x**: Volume C–M の残仕様（DNP3 / Siemens S7 / Allen-Bradley / IPMI/Redfish 等）

---

## [2.18.0] — 2026-05-09

### Added — ドキュメント大幅充実（8 種新規）
本リリースは機能追加なし、**ドキュメント整備のみ**。次フェーズ（v3.0
SemVer 切替 + 実機統合）に向けた読み手別ナビゲーションを完成。

| 新規ドキュメント | 対象読者 | 内容 |
|----------------|---------|------|
| `CONTRIBUTING.md` | 貢献者 | コミット規約 / PR チェックリスト / セキュリティ報告 |
| `docs/DEVELOPMENT.md` | 開発者 | 環境 / 内部構造 / 新規モジュール追加手順 / リリース手順 / よくある落とし穴 |
| `docs/TROUBLESHOOTING.md` | 運用者 + 開発者 | インストール / 起動 / パフォーマンス / セキュリティ / FAQ |
| `docs/MIGRATION.md` | 既存ユーザー | v2.x → v3.0 移行ガイド + 互換性マトリクス |
| `docs/DEPLOYMENT.md` | 運用者 | Docker / systemd / k8s / 環境変数 / シークレット / バックアップ |
| `docs/OBSERVABILITY.md` | SRE | Prometheus / OTel / AuditTrace / TimelineStore / SLO / Grafana |
| `docs/TESTING.md` | 開発者 | テスト戦略 / Hypothesis / カバレッジ目標 / Flaky 対策 |
| `docs/GLOSSARY.md` | 全員 | LLM / セキュリティ / 産業用語集（A–X） + 略語表 |

### Updated — README.md
- Documentation セクションを 4 グループ（概要・仕様・利用・運用・開発）に再構成
- 新規 8 ドキュメントへのリンクを追加

### Version
- `pyproject.toml` を v2.17.0 → v2.18.0 に昇格。

---

## [2.17.0] — 2026-05-09

### Added — `llmesh.security.http_limits` 共通モジュール
- 新規モジュール `llmesh/security/http_limits.py`:
  - `read_capped(resp, *, max_bytes)` — `urllib.request.urlopen` 系の
    レスポンスから安全にバイト列を取得。`max_bytes + 1` を読みに行き、
    超過時は `ResponseTooLargeError(IOError)` を送出。
  - 用途別デフォルト定数: `DEFAULT_MAX_RESPONSE_BYTES`（1 MiB / 一般 JSON）、
    `DEFAULT_LLM_RESPONSE_BYTES`（16 MiB / LLM 推論）、
    `DEFAULT_GOSSIP_RESPONSE_BYTES`（256 KiB / peer discovery）、
    `DEFAULT_DISCOVERY_RESPONSE_BYTES`（256 KiB / registry）、
    `DEFAULT_RENDEZVOUS_RESPONSE_BYTES`（64 KiB / DID lookup）、
    `DEFAULT_HTTP_ADAPTER_BYTES`（4 MiB / 一般メッセージ）。
- テスト 7 件追加（`tests/test_http_limits.py`）— bytes/正常 / cap 境界 /
  オーバーフロー / 不正型 / デフォルト値検証。

### Fixed — 全 HTTP クライアントにレスポンスサイズ上限を導入
本リリースで `resp.read()` 無制限読込パターンを排除し、すべてのアウト
バウンド HTTP 呼び出しが明示的なバイト数キャップを経由するよう変更:

| ファイル | 用途 | キャップ |
|---------|------|---------|
| `rag/embedder.py::OllamaEmbedder.embed` | embedding 取得 | 1 MiB |
| `llm/ollama.py::OllamaBackend.invoke` | LLM 推論 | 16 MiB |
| `llm/llamacpp.py::LlamaCppBackend.{health,invoke}` | health + LLM 推論 | 1 MiB / 16 MiB |
| `privacy/image_summarizer.py::summarize` | Vision LLM | 16 MiB |
| `discovery/gossip.py::_pull_from` | peer pull | 256 KiB |
| `mcp/sca_gate.py::query_osv` | OSV 脆弱性 DB | 4 MiB |
| `discovery/client.py::_send` | registry CRUD | 256 KiB |
| `rendezvous/client.py::lookup` | DID lookup | 64 KiB |
| `protocol/http_adapter.py` | 既に独自実装済 | （変更なし） |

各呼び出しは `ResponseTooLargeError` を捕捉し、用途別の例外クラス
（`BackendError` / `EmbeddingError` / `ImageSummarizationError` /
`OsvQueryError` / `DiscoveryError` / `LookupError`）に変換。
ログ出力（gossip）またはエラー伝播（同期 RPC）で運用に可視化。

### Security — STRIDE I（Information Disclosure / DoS）緩和
- 攻撃者が制御する upstream（または運用ミスで暴走した upstream）から
  GiB 級レスポンスを送られても、LLMesh ノードの常駐メモリは用途別
  キャップで bounded。OOM kill によるサービス停止リスクが大幅に低下。
- 本対策は `docs/SECURITY.md` の Forbidden Patterns 表に v2.16+ で既に
  記載済（"Unbounded `resp.read()` from external HTTP"）。本リリースで
  全箇所を完全に該当パターンから除外。

### Version
- `pyproject.toml` を v2.16.0 → v2.17.0 に昇格。

---

## [2.16.0] — 2026-05-09

### Security — `.npz` 信頼境界の RCE リスク除去
- `llmesh/rag/numpy_store.py` `save/load` を **pickle-free** 化:
  - 文字列カラム（`doc_ids` / `texts` / `metadata`）を `dtype=object`
    （pickle 必須）から、UTF-8 JSON ペイロードを `np.frombuffer(payload,
    dtype=np.uint8)` で保存する形式に置換。
  - `np.load(path, allow_pickle=False)` で読み込み可能 — 信頼できない
    `.npz` を開いてもピクル経由の任意コード実行リスクなし。
- `llmesh/rag/lsh_store.py` `save/load` も同様に pickle-free 化
  （planes / vectors はそのまま保存、文字列のみ JSON 化）。
- 既存テスト 17 件（`test_rag_store.py` の `TestPersistence`）は完全互換、
  追加修正不要。

### Fixed — オペレーション可視性とロバスト性
- `llmesh/industrial/dnp3_adapter.py`:
  - callback 例外を `pass` で握り潰す挙動を `logger.warning(exc_info=True)`
    に置換。loop 継続性は維持しつつ運用ログに痕跡が残る。
- `llmesh/industrial/goose_adapter.py`:
  - 同上の callback 例外ログ化。
  - クラス docstring に **Thread safety: Not thread-safe** を明記
    （`_last_st_num` カウンターが per-`goCBRef` 単独 dict のため、
    並列呼び出しでリプレイが通る race condition を文書化）。
- `llmesh/rag/sqlite_store.py`:
  - `__init__` で `:memory:` 以外のパスは親ディレクトリを自動作成
    （`OperationalError: unable to open database file` を防止）。
- `llmesh/industrial/vlm_feature_extractor.py`:
  - `MockVisionCaptioner` の Pillow 経路で `thumbnail((32, 32))` 化。
    4K 画像で輝度サンプリング前のメモリ消費を 100× 削減。
- `llmesh/rag/embedder.py`:
  - `OllamaEmbedder.embed` のレスポンスサイズ上限 1 MiB を導入
    （`_MAX_EMBED_RESPONSE_BYTES`）。悪意ある / 暴走した backend の
    OOM を緩和（dim ≤ 4096 の正常応答は ≤ 64 KiB なので十分な余裕）。

### Fixed — テスト整合性
- `tests/test_rag_embedder.py` の `_FakeResp.read()` を `read(size=-1)`
  対応に修正（`OllamaEmbedder` の新 API に追従）。

### Audit — 全体静的セキュリティスキャン結果
LLMesh 配下 150 ソースファイル + 125 テストファイルを横断スキャンし、
以下の **OWASP Top 10 / 産業ソフト固有脅威** に該当する実コード使用がない
ことを確認:

| 観点 | 結果 |
|------|------|
| `shell=True` / `pickle` / `eval` / `exec` / `yaml.load` | ✅ 実コード使用ゼロ |
| `subprocess` / `os.system` / `os.popen` | ✅ ゼロ |
| SQL injection（f-string / 文字列連結 SQL）| ✅ ゼロ |
| TLS 検証無効化 / 古い SSL 利用 | ✅ ゼロ |
| `tarfile.extractall` / `zipfile.extractall` | ✅ ゼロ |
| ハードコード API key / password / token | ✅ ゼロ |
| 弱乱数 `random.*`（暗号用途）/ 弱ハッシュ MD5 | ✅ ゼロ |
| ReDoS 候補 regex | ✅ ゼロ |
| `allow_pickle=True` を信頼境界で使用 | ✅ **本リリースで完全除去** |

### Version
- `pyproject.toml` を v2.15.0 → v2.16.0 に昇格。

---

## [2.15.0] — 2026-05-08

### Added — F-1.2 LSHVectorStore（ANN ベクトルストア）
- `llmesh/rag/lsh_store.py` 新規追加。
  - 純 numpy 実装の Random-Hyperplane LSH（locality-sensitive hashing）。
  - 既定パラメータ: `n_planes=12`, `n_tables=8`, `seed=0`, `rerank_factor=4`。
  - Index: 各テーブルで `bits = (planes @ vec > 0)` を整数キーに集約。
  - Search: 各テーブルの bucket を OR 合成 → 上位 `rerank_factor*k`
    候補を exact cosine で再ランク。
  - 永続化: `.npz` アトミック保存、planes も保存して save/load 後の
    bucket 整合性を維持。
  - `VectorStore` インターフェース完全準拠（NumpyVectorStore /
    SqliteVectorStore と相互置換可能）。
- `llmesh/rag/__init__.py` の lazy `__getattr__` に LSHVectorStore を
  追加（numpy 不在環境では遅延 import）。
- 推奨規模: **≥ 10⁶ docs**。recall@10 ≥ 0.92 の実測（500 docs / 64 dim
  + 0.05σ ノイズの probe 100 件）。
- テスト 13 件（numpy 環境で PASS、不在環境では collection-skip）。

### Added — v3 横断統合 E2E テスト
- `tests/test_v3_integration_e2e.py` 新規追加（5 件 PASS）。
  1. DNP3Adapter → ExplainedCUSUM → IncidentReport
  2. VLMFeatureExtractor → UnifiedSPC（multimodal verdict）
  3. VideoCUSUM フレーム + センサー時刻同期ペア化
  4. RAG with PromptFirewall + SqliteVectorStore（block / clean / 注入耐性）
  5. GOOSE PDU → SensorEvent → ExplainedCUSUM
- 各シナリオは v2.13/v2.14 で追加した 6 モジュールを 1 パスで横断、
  公開 API のみで構成（外部依存ゼロ）。

### Added — 公開 API レイヤー（API stability）
- `llmesh/__init__.py` を整理:
  - `__version__` を `importlib.metadata` から動的解決（fallback "2.15.0"）。
  - `__all__` で公開シンボルを明示
    （DataLevel / ClassifiedPayload / PromptFirewall / FirewallDecision /
    PresidioDetector / PresidioResult / PrivacySummarizer / SensorEvent /
    Priority）。
- `docs/API_STABILITY.md` 新規追加。
  - Public / Internal API の境界定義
  - SemVer ポリシー（v3.0.0 から正式適用）
  - Deprecation プロセス（minor 警告 → 1 minor 猶予 → major 削除）
  - 公開シンボル一覧（v2.14 時点）
  - ABI / wire-protocol 互換性ガイド
- `tests/test_public_api.py` 新規追加（10 件 PASS）。
  - 全公開シンボルの import 動作確認
  - `__all__` 整合性チェック
  - 未知属性の AttributeError チェック（lazy `__getattr__`）

### Added — `docs/PERFORMANCE.md`
- v2.14 時点の主要モジュールの計算量 / メモリ / 推奨スケールを表形式で記載
  （Privacy Pipeline、RAG 3 バックエンド、v3 Industrial エンジン群、
  Adapter、Rust 拡張、メモリプロファイル、CI 性能、推奨パラメータ）。

### Version
- `pyproject.toml` を v2.14.0 → v2.15.0 に昇格。

---

## [2.14.0] — 2026-05-08

### Added — v3-N7 ExplainedCUSUM（自己説明 CUSUM 管理図）
- `llmesh/industrial/explained_cusum.py` 新規追加。
  - 既存 `CUSUMChart` をラップし、各 alarm 発生時に `LLMExplainer` で
    `IncidentReport`（Markdown + JSON）を生成。
  - `ExplainedSPCResult` は `spc_result` / `report` / `incident_id` を保持、
    `in_control` プロパティで透過呼び出し可能。
  - clock / incident_id_factory を依存性注入できるためテストは決定論的。
- テスト 10 件 PASS。

### Added — v3-N15 VideoCUSUM（動画 + センサー時刻同期 CUSUM）
- `llmesh/industrial/video_cusum.py` 新規追加。
  - 動画フレーム由来特徴 + センサー時系列の 2 チャネル CUSUM を、
    `sync_window_s`（既定 1.0 秒）でペアリング。
  - 各チャネルに独立な `CUSUMChart` を割当て、`bounded deque` で
    pending alarm を保持（`buffer_size`、既定 128）。
  - `ingest_frame(t, value)` / `ingest_sensor(t, value)` が
    `VideoCUSUMResult` を返し、`synced_alarm` と `paired_with` で同期判定。
- テスト 12 件 PASS。

### Added — v3-N15 VLMFeatureExtractor（画像 → 数値特徴）
- `llmesh/industrial/vlm_feature_extractor.py` 新規追加。
  - 2 段階パイプライン: ImageFirewall ゲート → Vision LLM caption →
    数値ベクトル化。
  - `VisionCaptioner` Protocol、`MockVisionCaptioner`（Pillow 検出 + SHA-256
    フォールバック）、デフォルトパーサ（数値抽出 + 欠陥キーワード集計 +
    文字統計）を同梱。
  - ImageFirewall 例外 / captioner 例外 / 非文字列応答は **fail-closed BLOCK**。
- pyproject.toml に optional extras `vlm` 追加（`Pillow>=10.0`）。
- テスト 18 件 PASS。

### Added — F-1.1 SqliteVectorStore（純 sqlite3 永続ベクトルストア）
- `llmesh/rag/sqlite_store.py` 新規追加。
  - `VectorStore` インターフェース実装、純 sqlite3（stdlib）依存。
  - WAL モード、`PRAGMA synchronous=NORMAL`、UPSERT による同 ID 上書き、
    sqlite3 native backup API による save/load。
  - `meta_kv` テーブルで dimension / schema_version / created_at を管理。
  - O(n) cosine スキャン（≤10⁶ 件で実用、ANN 拡張は別バックエンドで提供）。
- `llmesh/rag/__init__.py` の export に追加（lazy import 不要、純 stdlib）。
- テスト 17 件 PASS。

### Added — v3-N7 / K-1.1 DNP3Adapter（SCADA outstation client、スケルトン）
- `llmesh/industrial/dnp3_adapter.py` 新規追加。
  - DNP3 group code → `sensor_type` マッピング（binary_input / counter /
    analog_input / analog_output / time）。
  - `DNP3Point` データクラス、`point_to_event()` 変換ヘルパー。
  - allow-list（`(master_addr, outstation_addr)` ペア）で接続元検証。
  - `pydnp3` 不在時は `connect()` で `RuntimeError`、driver 注入で
    ユニットテスト可能（fake driver で 21 件 PASS）。
- pyproject.toml に optional extras `dnp3` 追加（`pydnp3>=0.1`）。

### Added — v3-N7 GOOSEAdapter（IEC 61850 GOOSE subscriber、スケルトン）
- `llmesh/industrial/goose_adapter.py` 新規追加。
  - `GoosePDU` / `GooseTransport` Protocol を定義。
  - `pdu_to_events()` でデータセット要素を `SensorEvent`（`Priority.HIGH`）に
    展開、`MAX_DATASET_VALUES=256` でオーバーサイズ拒否。
  - `allow_iedids` ホワイトリストで `goCBRef` 検証、`stNum` リプレイ防御
    （per-ref 単独カウンター、equal は許可、後退は drop）。
  - 純 stdlib 実装、テスト 21 件 PASS。

### Version
- `pyproject.toml` を v2.13.0 → v2.14.0 に昇格。

---

## [2.13.0] — 2026-05-08

### Added — E-2.1 Microsoft Presidio 統合（PII 検出強化）
- `llmesh/privacy/presidio_detector.py` 新規追加。
  - `PresidioDetector` クラスは presidio-analyzer 不在でも no-op で動作
    （fail-safe — `presidio_unavailable` で ALLOW、Presidio 例外時は L4 BLOCK）。
  - 既定 BLOCK エンティティ（CREDIT_CARD / US_SSN / IBAN / MEDICAL_LICENSE 等
    8 種の規制 PII）と SUMMARIZE エンティティ（PERSON / EMAIL / PHONE 等
    8 種の識別子）を分離設計。両者は disjoint。
  - `PresidioResult` データクラスで action/reason/level/entities を返す。
- `llmesh/privacy/firewall.py` に **Layer 1.5: Presidio PII 検出** フックを追加。
  - 既存 Layer 0/1/2 を破らない後方互換設計（`presidio=None` がデフォルト）。
  - 既存 380+ テスト 0 回帰。
- `pyproject.toml` に optional extras `presidio` を追加
  （`presidio-analyzer>=2.2`、`spacy>=3.7`）。
- テスト 19 件追加（`tests/test_presidio_detector.py`）+ firewall 統合 6 件
  （`tests/test_firewall.py::TestLayer15Presidio`）。

### Added — F-1 RAG（ローカルベクトル DB 統合 — MVP）
- 新規パッケージ `llmesh/rag/`:
  - `embedder.py` — `Embedder` ABC、`MockEmbedder`（決定論的 hash 埋め込み、
    test/offline 用、依存ゼロ）、`OllamaEmbedder`（urllib のみ、Ollama
    `/api/embeddings` クライアント、L2 正規化）。
  - `store.py` — `VectorStore` ABC、`Document` / `RetrievedDocument` 型。
  - `numpy_store.py` — 純 numpy `NumpyVectorStore`（in-memory、cosine 類似度、
    `.npz` 永続化、書込みは tmp→rename でアトミック）。
  - `retriever.py` — Embedder + VectorStore + 任意の PromptFirewall を結合した
    `Retriever`。インデックス時に L4 ドキュメント拒否、検索時にクエリ
    検査 + 結果ごとに firewall 判定（drop_blocked オプション）。
- `__init__.py` は numpy を lazy import（`__getattr__` 経由）— 軽量 import
  パスで他モジュールに影響しない。
- `pyproject.toml` に optional extras `rag = ["numpy>=1.26"]` を追加。
- テスト 36 件追加（embedder 13、store 11、retriever 12）。
  numpy 不在環境では store/retriever テストは collection-skip。

### Added — v3-N11 µs 異常検知（DVS+MT）コアモジュール
- `llmesh/industrial/mt_online.py` — `OnlineMTEngine` を新規追加。
  - 既存 `MTEngine` をラップしたストリーミング推論ラッパー。
  - 環境変数 `LLMESH_MT_ONLINE_MAX_BATCH_BYTES`（デフォルト 16 MiB）で
    バッチサイズ上限を制御、内部チャンキングで OOM を回避。
  - `score_batch()` は einsum ベースのベクトル化 Mahalanobis 計算。
- `llmesh/industrial/hotelling_t2.py` — `HotellingT2Chart` 多変量管理図。
  - 共分散行列ベースの Hotelling T² 統計。
  - UCL は明示指定または `α` から χ² 漸近近似で自動算出。
  - Tikhonov 正則化で rank-deficient 参照データにも対応。
- `llmesh/industrial/event_density_map.py` — `EventDensityMap`。
  - DVS イベント `(t, x, y, polarity)` を粗いグリッド（既定 8×8）に
    投影し、SPC 入力に適した固定次元特徴ベクトルに変換。
  - 構造化配列 / `(n,3)` / `(n,4)` の複数入力形式を許容。
- テスト 27 件追加（mt_online 9、hotelling_t2 13、event_density_map 9）。
  numpy 必須なので numpy 不在環境では collection-skip。

### Added — v3-N15 統計 × VLM × IoT（マルチモーダル SPC）
- `llmesh/industrial/multimodal_spc.py` — `UnifiedSPC` を新規追加。
  - 既存 `XbarRChart` / `CUSUMChart` の任意組合せをセンサーチャネルと
    VLM テキスト特徴チャネルに割り当てた 2 系統 SPC モニタ。
  - 結合モード `and` / `or`（既定）/ `weighted`（閾値 + 重み付き投票）。
  - `UnifiedSPCResult` に両チャネルのサブ結果と違反タグを保持。
- 純 stdlib（numpy 不要）。テスト 14 件全 PASS。

### Added — v3-N7 説明可能 SCADA（LLMExplainer）
- `llmesh/industrial/explainer.py` — `LLMExplainer` を新規追加。
  - SPC / MT-method の `AlarmEvent` を `IncidentReport`（Markdown +
    JSON）に変換するルートコーズ説明レイヤー。
  - LLM オプショナル設計：LLM 未配線時はテンプレート出力、LLM 失敗時も
    テンプレートにフォールバック（fail-safe）。
  - severity_map で deviation 比率に応じた `info/warn/critical` 分類。
  - LLM 応答は 1024 文字に bound、空応答はテンプレート復帰。
- 純 stdlib。テスト 15 件全 PASS。

### Pipeline integration ready
- v3-N7 / v3-N11 / v3-N15 の各コアコンポーネントは完成。
  外部依存（pydnp3 for v3-N7, Vision LLM for v3-N15）が必要な
  Adapter / Extractor は v2.14+ 以降で追加予定。

### Version
- `pyproject.toml` を v2.12.0 → v2.13.0 に昇格。

---

## [2.12.0] — 2026-05-08

### Fixed — WebSocketAdapter handshake 検証
- `llmesh/industrial/websocket_adapter.py` の `_handshake` で `Connection`
  ヘッダ検証が `"websocket"` を期待していたバグを修正。RFC 6455 § 4.1 に従い
  `"upgrade"` を検査するよう変更（テスト 3 件 — `test_valid_handshake` /
  `test_auth_token_required_when_set` / `test_loopback_allowed_when_listed` —
  が PASS）。
- 全テストスイート（380+ 件）回帰確認 PASS。

### Changed — Volume N の Research Backlog 化（2026-05-07 確定方針の反映）
- `docs/REQUIREMENTS.md` Volume N を「Research Backlog」と明記（要件定義書には
  15 テーマ全て残す）。
- 優先順位表を A/B/C/D 分類に再構成:
  - **A 採用**（v3 ROADMAP 正式昇格）: N-7 説明可能 SCADA / N-11 µs 異常検知（DVS+MT）
    / N-15 統計 × VLM × IoT
  - B 条件付き: N-2 / N-3
  - C 研究テーマ: N-4 / N-5 / N-9 / N-13
  - D 組込価値薄: N-1 / N-6 / N-8 / N-10 / N-12 / N-14
- `docs/ROADMAP.md` v3 残ロードマップから旧優先 3 テーマ（N-2 / N-3 / N-7）と
  ★長期研究行を削除し、A 分類 3 テーマ（N-7 / N-11 / N-15）の正式昇格行に置換。

### Added — v3 Implementation Plan（A 分類 3 テーマの実装計画）
- `docs/REQUIREMENTS.md` に新セクション **"v3 Implementation Plan"** を追加
  し、ROADMAP に正式昇格した A 分類 3 テーマ（N-7 / N-11 / N-15）の
  実装計画を別建てで詳述:
  - **N-7 説明可能 SCADA**: DNP3 Adapter（K-1.1 と統合）+ CUSUM 拡張 +
    `LLMExplainer` モジュール（既存 MTEngine / SPC を流入路に）
  - **N-11 µs 異常検知（DVS+MT）**: DVSAdapter（v1.7.0 既存）+ MTEngine
    オンライン推論パイプライン + Hotelling T² モジュール
  - **N-15 統計 × VLM × IoT**: VLM 経路（既存 ImageFirewall +
    PrivacySummarizer）と XbarRChart の統合 SPC、CUSUM の動画/センサー
    ハイブリッド入力対応
- 各テーマの **依存関係 / 既存モジュール再利用 / 新規モジュール / セキュリティ
  不変条件 / テスト戦略 / 受入基準** を要件項目として明文化。

### Version
- `pyproject.toml` を v2.11.0 → v2.12.0 に昇格。

---

## [2.11.0] — 2026-05-07

### Added — WebSocketAdapter (J-4.3) + Volume N（学際横断融合テーマ）

#### `WebSocketAdapter`（J-4.3 — リアルタイムイベント配信）
- `llmesh/industrial/websocket_adapter.py` — 純 stdlib RFC 6455 実装
  - 外部依存ゼロ（`websockets` パッケージ不要）
  - HTTP handshake → frame loop の WebSocket フルプロトコル
  - Per-message size cap（1 MiB）、CIDR allowlist、shared-secret auth
  - TLS 対応（`ssl.SSLContext` 渡し）
  - ping/pong keepalive、`asyncio` ベース
  - JSON テキスト → SensorEvent / バイナリは `ws_binary` payload

#### Volume N — 学際横断融合テーマ（Raptor RAD 駆動）
- 22 分野コーパス（`C:/Users/puruy/raptor/.claude/skills/corpus/`）から
  抽出した **15 の未踏融合テーマ**:
  - N-1 Quantum × LLM × NN（量子強化 LLM）
  - N-2 Edge AI × Security × Medical（医療エッジ AI）
  - N-3 自動運転 × LLM Agent
  - N-4 産業センサー × Diffusion
  - N-5 ゲーム × LLM × VLM
  - N-6 ロボティクス × 最適化 × 数値解析
  - N-7 重要インフラ × 統計 SPC × LLM
  - N-8 量子センサー × 情報理論 × 防災
  - N-9 画像処理 × 数値解析 × 拡散モデル
  - N-10 AI Agents × 重要インフラ × Compliance
  - N-11 多変量解析 × DVS × 予知（µs 異常検知）
  - N-12 フェデレーション × 量子 × 医療
  - N-13 ゲーム × 強化学習 × 産業デジタルツイン
  - N-14 数値最適化 × LLM × Compliance
  - N-15 統計 × VLM × IoT

#### LLMesh→Raptor 論文コーパス全コピー
- `docs/papers/` 配下の 21 分野 + 4 論文素材を
  `C:/Users/puruy/raptor/.claude/skills/corpus/` へ移行
- Raptor 配下では計 **25 分野**を `rad-research` スキルから一元アクセス可能

### 累計 Volume

A-M (91 章) + N (15 章) = **117 章 / 500+ 個別要件項目**

---

## [2.10.0] — 2026-05-07

### Added — 数学・統計分野 RAD コーパス 5 分野（合計 21 分野へ）

#### 追加分野（v2.10）

| 分野 | ディレクトリ | 既存 LLMesh 機能との対応 |
|------|------------|-----------------------|
| **多変量解析** | `multivariate_analysis_corpus/` | MTEngine（マハラノビス・タグチ法） |
| **統計学・SPC** | `statistics_corpus/` | XbarRChart / CUSUMChart |
| **最適化** | `optimization_corpus/` | （pipeline チューニング、ベイズ最適化） |
| **数値解析・線形代数** | `numerical_methods_corpus/` | numpy / scipy / PointCloud SVD |
| **情報理論** | `information_theory_corpus/` | PromptFirewall / 圧縮 / 量子情報 |

各分野で 5+ 標準クエリ × 3 ソース（OpenAlex / arXiv / CrossRef）を登録、
各 10,000+ 件収集を目標。

#### `tools/bulk_corpus_collector.py` 拡張
- 16 → **21 分野**（数学 5 分野追加）
- `_DOMAIN_QUERIES` に各 5 クエリ既定登録

#### `docs/papers/CORPUS_INDEX.md` 更新
- 三段構成: 応用 9 + 先端 AI / 量子 7 + 数学・統計 5

#### `docs/papers/RAD_RESEARCH_GUIDE.md` 新規（補助資料運用）
- アイデア出し・調査の利用パターン
- 分野横断クエリ例
- LLM 連携（RAG への投入手順）

### 想定収集量（v2.10 時点）

| カテゴリ | 想定 |
|---------|---:|
| 応用 9 分野 | ≈ 95,900 |
| 先端 AI / 量子 7 分野 | ≈ 70,000 |
| 数学・統計 5 分野 | ≈ 50,000 |
| **合計** | **≈ 215,900 ユニーク論文** |

---

## [2.9.0] — 2026-05-07

### Added — 先端 AI / 量子コンピューティング 7 分野 RAD コーパス

#### 追加された 7 分野（合計 **16 分野**へ拡大）

| 分野 | ディレクトリ | 重点トピック |
|------|------------|------------|
| **Deep Learning** | `deep_learning_corpus/` | 最適化 / 自己教師あり / scaling laws / 蒸留 |
| **Neural Networks** | `neural_network_corpus/` | SNN / GNN / Mamba / S4/S6 / NeRF / 圧縮 |
| **LLM** | `llm_corpus/` | RLHF / DPO / MoE / 長文脈 / CoT / 関数呼出 |
| **VLM / vLLM** | `vllm_corpus/` | CLIP / LLaVA / PagedAttention / 投機的復号 |
| **Quantum Computing** | `quantum_computing_corpus/` | QML / VQE / QEC / NISQ / QKD / 量子センサー |
| **Diffusion Models** | `diffusion_corpus/` | DDPM / Flow Matching / ControlNet / 3D / 音響 |
| **AI Agents** | `agents_corpus/` | ReAct / Reflexion / multi-agent / SWE-Agent |

#### `tools/bulk_corpus_collector.py` の `_DOMAIN_QUERIES` 拡張
- 9 → **16 分野**、各分野 5 クエリ既定登録
- `--all` フラグで 16 分野一気収集

#### `docs/papers/CORPUS_INDEX.md` 更新
- 応用 9 分野 + 先端 AI / 量子 7 分野の二段構成

### 想定収集量（v2.9 時点）

| カテゴリ | 想定 |
|---------|---:|
| 応用 9 分野 | ≈ 95,900 |
| 先端 AI / 量子 7 分野 | ≈ 70,000 |
| **合計** | **≈ 165,900 ユニーク論文** |

---

## [2.8.0] — 2026-05-07

### Added — 大量論文コーパス収集（各分野 10,000+ 件目標）

#### 新ツール
- **`tools/bulk_corpus_collector.py`** — マルチソース大量取得
  - **OpenAlex**（245M 論文、無料）— カーソルページネーション
  - **arXiv** — オフセットページネーション、3秒レート制限
  - **Semantic Scholar** — オフセット、1秒レート
  - DOI / arXiv ID / title-hash による重複除去
  - 9 分野標準クエリバンドル組込み
  - `--all` で 9 分野一括実行
- **`tools/community_corpus_collector.py`** — コミュニティ・専門ソース
  - **CrossRef**（145M DOI、無料）
  - **DBLP**（6M CS 論文、無料）
  - **PubMed E-utilities**（医療専門、3req/s）
  - **HackerNews via Algolia**（実践記事補完）
  - **Papers With Code**（実装付き論文）
  - **OpenReview**（査読論文）

#### 大量収集ガイド
- **`docs/papers/BULK_COLLECTION_GUIDE.md`**:
  - 6 ソース能力比較表（合計 600M+ ユニーク論文）
  - 各分野で 10,000+ 件達成のためのクエリレシピ
  - 9 分野合計目標 **~95,900 ユニーク論文**
  - GitHub Actions 週次リフレッシュ サンプル
  - ストレージ見積（gzip で ~30 MB）
  - ライセンス・倫理ガイドライン

### 設計思想

- 単一ソースに依存せず、**6 種類のソースを統合**（フォールトトレラント）
- 全 source 共通スキーマ（`bulk_corpus_collector.py` の dedupe_records と互換）
- 各 source 独立にレート制限・retry/backoff
- HTTPS GET only、認証なし、stdlib のみで実装（追加依存ゼロ）

### Tests
- 既存 380 件 + debug 検証 19 件全 PASS

---

## [2.7.0] — 2026-05-07

### Added — CLI 強化（G-1, G-4）+ SBOM（H-3.3）+ デバッグ機能 + 8 分野 RAD

#### `llmesh.cli` パッケージ
- **`doctor.py`** — `llmesh doctor`: 環境健全性チェック（G-4.1）
  - Python 版・必須/任意パッケージ・Rust 拡張・port probe・NTP drift
  - JSON/Text 出力切替、CI 連携可
- **`status.py`** — `llmesh status`: ランタイムスナップショット（G-1.1）
  - バージョン・プラットフォーム・Rust 拡張・アダプター・edge tier
- **`sbom.py`** — CycloneDX 1.5 SBOM 自動生成（H-3.3）
  - PyPI / EU CRA / US EO 14028 / ISO 27001 対応
  - 全パッケージの purl + license 抽出、決定論的出力

#### デバッグ機能（DevEx）
- **`industrial/debug.py`**:
  - `DebugRecorder` — JSONL での SensorEvent + DiagnosisResult 記録
  - `DebugReplayer` — 再生機能、deterministic
  - `PipelineProfiler` — process() のレイテンシ測定（p50/p95/p99）
  - `describe_event()` — pretty-print（payload を float64/32 解釈ヒント付き）
- 18 + 18 件のテスト全 PASS

#### 分野別 RAD 論文コーパス（8 分野）
- `docs/papers/CORPUS_INDEX.md` — 9 分野インデックス
- `security_corpus/` — ICS / prompt injection / DP / SBOM
- `industrial_iot_corpus/` — PdM / MT 法 / OPC-UA / fieldbus
- `mlops_corpus/` — エッジ LLM / ONNX / 量子化 / drift
- `game_dev_corpus/` — NPC LLM / procedural / anti-cheat
- `medical_corpus/` — 医療 LLM / DICOM / FHIR / HIPAA
- `automotive_corpus/` — CAN / AUTOSAR / OBD / V2X
- `infrastructure_corpus/` — DNP3 / IEC 61850 / Smart Grid / BACnet
- `robotics_corpus/` — ROS 2 LLM / SLAM / manipulation / DVS

各コーパスに標準クエリセット（5 クエリ × 8 分野 = 40 クエリ）。

### Tests — v2.7 で +36 件
- doctor/status/sbom: 18 件
- debug recorder/replayer/profiler: 18 件

### 累計
- **380 件全 PASS**（v1.6〜v2.7、exit code 0）
- 純粋なテストカバレッジに加え、実コマンドの実行系テストも統合

---

## [2.6.0] — 2026-05-07

### Added — マルチプラットフォーム / エッジ / RTOS 対応

#### マルチプラットフォーム（Volume L 一部実装）
- **`.github/workflows/build-wheels.yml`** — 8 ターゲット wheel 自動ビルド
  - Linux x86_64 / aarch64 (manylinux + musl)
  - Windows x86_64 / aarch64
  - macOS x86_64 / Apple Silicon
  - sdist + Trusted Publishing PyPI release
- **`docs/PLATFORMS.md`** — 対応プラットフォーム完全マトリクス（11 章）

#### エッジコンピュータ向け（v2.6 — Volume L）
- **`llmesh/industrial/edge_profile.py`** — `EdgePreset` 5 段階リソース制約
  - MICRO（256MB / Pi Zero 2 W）/ NANO（512MB）/ SMALL（1GB）/ MEDIUM（4GB）/ WORKSTATION
  - `apply_profile(preset)` で各モジュール定数を自動調整
  - `detect_recommended_preset()` で psutil から自動推定
  - 安全な最小値クランプ
- **`tests/test_edge_profile.py`** — 8 件全 PASS

#### RTOS 統合（Volume L 完成版）
- **`c_bindings/llmesh_event.h`** — SensorEvent C ABI v1 ヘッダ単独ライブラリ
  - 44 バイト packed header、UTF-8 可変長フィールド
  - TRON / Zephyr / FreeRTOS / VxWorks / QNX / NuttX / Mbed OS / AUTOSAR 対応
  - 静的メモリ前提、reentrant、malloc 不使用
  - 14 プロトコル ID（Modbus / OPC-UA / MQTT / CAN / BACnet / DVS など）
- **`llmesh/industrial/c_abi.py`** — Python 側デコーダ
  - `encode(SensorEvent) -> bytes` / `decode(bytes) -> SensorEvent`
  - サイズ上限・Magic / Version 検証
  - エラー時は `CABIError`（`ValueError`派生）
- **`tests/test_c_abi.py`** — 18 件（property-based 含む）全 PASS

#### 互換性テスト強化
- **`tests/test_platform_compat.py`** — 31 件
  - 全コアモジュールの import 確認
  - Pure Python / Rust の **byte-identical 検証**（hypothesis）
  - エンディアン不変条件
  - リソース上限の境界テスト
  - 合成データの再現性

### REQUIREMENTS.md Volume L + Volume M 追加
- L-1〜L-8 RTOS 統合 8 章（μITRON / TOPPERS / T-Kernel / Zephyr / FreeRTOS / NuttX / Mbed / VxWorks / QNX / INTEGRITY / AUTOSAR Classic）
- M-1〜M-3 量子・先進計算 3 章（Qiskit / Cirq / Loihi 2 / SpiNNaker など）

### 累計
- v2.6 で +57 件（edge: 8 + c_abi: 18 + platform_compat: 31）
- **343 件全 PASS**（v1.6〜v2.6 関連、exit code 0）

---

## [2.5.0] — 2026-05-07

### Added — Rust 拡張（C-12）+ AI 向け環境構築ガイド

#### `rust_ext/` — Rust 拡張モジュール（PyO3 + maturin）
- **`Cargo.toml`** — pyo3 0.22 + abi3-py311（単一 wheel で全 Python バージョン対応）
- **`src/lib.rs`** — 5 つの公開関数、~200 行
  - `pc_to_bytes(points)` / `pc_from_bytes(data)` — PointCloud
  - `dvs_encode(events)` / `dvs_decode(data)` — DVS
  - `dvs_batch_stats(data, n)` — バッチ統計（fast path）
- **ワイヤフォーマット完全互換**: Python 実装とバイト完全一致

#### Python 側統合
- `llmesh/industrial/sensor_3d/point_cloud.py` — try-import で Rust 自動利用
- `llmesh/industrial/sensor_3d/event_adapter.py` — encode/decode を Rust 化
- **フォールバック保証**: Rust 未ビルド環境でも pure-Python で動作

#### 性能改善（実測）

| 操作 | Pure Python | Rust | 倍率 |
|------|-----------:|-----:|----:|
| PointCloud encode (1M) | 4.0M pts/s | **24.1M pts/s** | **6.0×** |
| PointCloud decode (1M) | 3.7M pts/s | 5.9M pts/s | 1.6× |
| DVS encode (1M) | 3.4M evt/s | 5.5M evt/s | 1.6× |
| DVS decode (1M) | 695K evt/s | 720K evt/s | 1.0× |

#### `docs/SETUP_GUIDE.md` — AI/開発者共通環境構築ガイド
- 15 章構成、コピペ実行可能
- AI エージェント向け要約・ナビゲーション・落とし穴対応集を冒頭・末尾に
- Rust ビルド手順（Windows での python3.lib 問題対応含む）
- トラブルシューティング 7 件 + 性能比較表

### Changed
- `pyproject.toml` — version=2.5.0
- 全 286 件のテストが Rust 経由でも全 PASS（既存仕様維持を確認）

---

## [2.4.0] — 2026-05-07

### Added — Volume K（重要インフラ）+ BACnet 実装 + ロバスト性強化

#### Volume K — REQUIREMENTS.md に重要インフラ要件 20 章
- K-1 電力（DNP3 / IEC 60870 / 61850 / SunSpec / OpenADR / DLMS）
- K-2 水道 / K-3 ガス・石油 / K-4 鉄道 / K-5 道路交通 / K-6 空港 / K-7 港湾
- K-8 通信網（NETCONF / OpenConfig / gNMI / IPFIX）
- K-9 データセンター（IPMI / Redfish / DCIM）
- K-10 ビル管理（BACnet / KNX / LonWorks / DALI）
- K-11 スマートホーム（Matter / Zigbee / Z-Wave / Thread）
- K-12 環境計測 / K-13 廃棄物 / K-14 医療施設 / K-15 食品農業
- K-16 教育 / K-17 軍事（オプトイン専用） / K-18 災害対応
- K-19 スマートシティ / K-20 横断要件（IEC 62443 / NERC CIP / NIS2）

#### BACnetAdapter — K-10.1 実装
- `llmesh/industrial/bacnet_adapter.py` — bacpypes3 ベース BACnet/IP クライアント
- 9 種オブジェクト型対応（analog/binary/multi-state × input/output/value）
- IP CIDR 検証、device_id 範囲検証（0–4,194,303）
- 自動再接続、20 件のテスト全 PASS
- `pyproject.toml` に `bacnet` extra 追加

#### Property-based テスト拡張
- CAN FrameSpec / BACnet ObjectSpec / DvsEvent 不変条件
- IndustrialMetrics 単調増加 / TenantScope ID 検証
- サロゲート文字バグ修正（`Cs` カテゴリ除外）

### Tests — v2.4 で +20 件（BACnet）+ property-based +6 件

### 累計 — 286 件全 PASS（v1.6〜v2.4 関連、exit code 0）

---

## [2.3.0] — 2026-05-07

### Added — 論文化基盤 + 合成データ + 画像論文コーパス

#### 精密工学会 4 論文（画像処理関連）
- **`docs/papers/README.md`** — 論文インデックス・進捗マトリクス・スタイル指針
- **`docs/papers/paper1_spatial_summarizer.md`** — 3D センサー要約 → LLM
- **`docs/papers/paper2_image_firewall.md`** — 画像入力プライバシー
- **`docs/papers/paper3_aoi_llm_diagnostic.md`** — AOI + LLM 診断
- **`docs/papers/paper4_dvs_industrial.md`** — DVS 産業応用
- **`docs/papers/datasets.md`** — MVTec AD / NYU Depth / DSEC 等の入手手順
- **`docs/papers/_bench_results.md`** — 実測ベンチマーク（PointCloud 4M pts/s 等）

#### 合成データ生成（再現性確保）
- **`tools/gen_synthetic_dataset.py`** — AOI / Depth / DVS 合成データ生成
  - 固定シード（42）でバイト再現可能
  - AOI: JPEG (SOI/EOI) + JSON サイドカー（ok/ng/defects）
  - Depth: uint32 width/height + float32 grid LE
  - DVS: 9 byte/event（uint16 x/y, uint32 t_us, uint8 polarity）
- **`tests/test_synthetic_dataset.py`** — 11 件 E2E（合成データ → 各 Adapter）

#### 画像論文コーパス（RAD 形式）
- **`tools/collect_image_papers.py`** — arXiv / Semantic Scholar から論文メタデータ収集
  - 35+ キーワードルールで自動トピック分類（AOI / DVS / depth / privacy / 等）
  - JSONL 形式・1 record ≤ 16 KiB のサイズ guard
  - HTTPS GET のみ・認証なし・stdlib のみで実装
- **`docs/papers/image_corpus/README.md`** — コーパス設計・統合方針
- **`docs/papers/image_corpus/queries.md`** — 論文ごとの標準クエリセット
- corpus2skill との統合により `/sourcehunt` ヒントとして再利用可能

#### CI 強化
- **`.github/workflows/ci.yml`** — マトリクス（Linux/macOS/Windows × Python 3.11/3.12）
- ruff lint ステップ追加
- build ジョブで wheel + sdist を生成・成果物アップロード

### Tests — Phase 2 で +11 件
- 合成データ E2E: 11 件全 PASS（AOI/Depth/DVS の adapter 連携）

---

## [2.2.0] — 2026-05-07

### Added — 品質 10/10 ロードマップ Phase 1

- **`llmesh/industrial/adapter_protocol.py`** — `IndustrialAdapter` 構造的 Protocol
  - PEP 544 `runtime_checkable` Protocol
  - `start()` / `stop()` / `on_event(callback)` の正式契約
  - 既存 9 種類のアダプター全てが準拠を実証（`test_adapter_protocol.py` 10 件）

- **`tests/test_industrial_e2e.py`** — 統合 E2E テスト 9 シナリオ
  - Pipeline + Tenant + Metrics の完全チェーン
  - TenantRegistry fanout
  - Pipeline + Tracer の親子 span 連携
  - 複数 Analyzer の最高 severity 選択
  - Xbar-R subgroup バッファリング
  - Metrics HTTP scrape のリアルソケット検証
  - Analyzer 例外時の他 Analyzer 継続性
  - Tracer の例外捕捉

- **`docs/REQUIREMENTS.md` Volume D** — 画像処理拡張要件 9 章
  - D-1 入力（RTSP / GenICam / V4L2 / ONVIF / RealSense）
  - D-2 前処理パイプライン（ROI / GPU 加速）
  - D-3 解析（ONNX / OCR / バーコード / Blob / Edge）
  - D-4 プライバシー強化（Face / Plate / Screen / EXIF）
  - D-5 動画 / D-6 3D 拡張 / D-7 医用 / D-8 共通仕様 / D-9 観測性

- **`docs/REQUIREMENTS.md` Volume E** — PyPI/GitHub 人気ライブラリ統合計画 13 カテゴリ
  - E-1 PLC（Siemens S7 / Allen-Bradley / Beckhoff ADS / pyads / pylogix）
  - E-2 PII（Microsoft Presidio / DataFog / piiranha）
  - E-3 観測性（prometheus_client / opentelemetry / statsd）
  - E-4 時系列 DB（InfluxDB / TimescaleDB / VictoriaMetrics / DuckDB）
  - E-5 クラウド IoT（Azure / AWS / GCP / ThingsBoard、明示 opt-in）
  - E-6 画像（OpenCV / Pillow / scikit-image / imageio）
  - E-7 ML 推論（ONNX / ctranslate2 / TensorRT / TFLite / mlc-llm）
  - E-8 LLM フレームワーク（LangChain / LlamaIndex / Haystack / Pydantic）
  - E-9 メッセージング（Kafka / RabbitMQ / NATS / Redis Streams / Pulsar）
  - E-10 異常検知 / 予知保全（PyOD / Prophet / River / tsfresh / Merlion / Kats）
  - E-11 ロボティクス・3D（rclpy / open3d / trimesh / pybullet）
  - E-12 セキュリティ（pynacl / authlib / argon2-cffi / pyjwt）
  - E-13 設定・データ（pydantic-settings / dynaconf / polars / duckdb）

### Tests — Phase 1 で +29 件
- adapter_protocol: 10 / industrial_e2e: 9 / 既存 fix: 10

### Improved
- `tests/test_industrial_e2e.py` の TenantScope+Pipeline 連携設計修正
- E2E テストを正しい層（SensorEvent ingest stage）でテナント分離

---

## [2.1.0] — 2026-05-07

### Added — v3 Phase 1（観測性 / マルチテナント / 自動車）

#### C-2 自動車 — `CANAdapter`
- **`llmesh/industrial/can_adapter.py`** — `python-can` 経由の汎用CAN-busアダプター
  - SocketCAN / Vector / PCAN / Kvaser / IXXAT / virtual バックエンド対応
  - CAN 2.0（11-bit, 0-0x7FF）/ 拡張（29-bit, 0-0x1FFFFFFF）両対応
  - CAN-FD 対応（DLC 64バイト）
  - `FrameSpec`: can_id / data_type（10種）/ byte_offset / scale / offset / extended
  - 1フレームから複数値を抽出可能（同一can_idに複数FrameSpec登録）
  - チャンネル名の入力サニタイズ（`[a-zA-Z0-9_\\-.:/]`）
  - 25 件のテスト、python-can 完全モック

#### C-13.1 観測性 — `IndustrialTracer`
- **`llmesh/industrial/tracing.py`** — W3C Trace Context 互換の純 stdlib トレーサー
  - `Span`: trace_id (16B) / span_id (8B) / parent_span_id / 属性 / ステータス
  - `IndustrialTracer.span()`: コンテキストマネージャ、自動タイミング・例外捕捉
  - `contextvars.ContextVar` による asyncio タスク間自動継承
  - `secrets.token_hex` で暗号論的に強い ID（`random` 使用なし）
  - 属性キャップ（64/span）・スパンキャップ（10K、FIFO eviction）
  - `export_jsonl()` / `export_otlp_payload()` で OTLP 互換出力
  - 20 件のテスト

#### v2.1.0-preview の機能（v2.1.0 として正式リリース）
- IndustrialMetrics（純 stdlib Prometheus + 非同期 `/metrics`）
- TenantScope / TenantRegistry（マルチテナント名前空間）

### Changed
- **pyproject.toml** — version=2.1.0、`can` extra と entry-point 追加
- **共通 conftest.py** — `make_sensor_event` / `industrial_pipeline` fixture 追加

### Tests — v3 Phase 1 で +84 件
- CAN: 25 / Tracing: 20 / Metrics: 17 / Tenant: 22

---

## [2.1.0-preview] — 2026-05-07（v2.1.0 として統合）

### Added — v3 Preview Modules（観測性 / マルチテナント）

- **`llmesh/industrial/metrics.py`** — `IndustrialMetrics`
  - 純 stdlib 実装、`prometheus_client` 不要
  - `increment(name, amount, labels)` / `set_gauge(name, value, labels)`
  - `render()` で Prometheus text exposition format（v0.0.4）出力
  - `serve_http(host, port)` でビルトイン非同期 HTTP `/metrics` エンドポイント
  - 入力検証: `_METRIC_NAME_RE` / `_LABEL_NAME_RE` / ラベル値エスケープ
  - 上限 `_MAX_SERIES = 100,000` でカーディナリティ爆発防止
  - 17 件のテスト

- **`llmesh/industrial/tenant.py`** — `TenantScope` / `TenantRegistry`
  - マルチテナント名前空間分離
  - `tenant_event(event, tid)` — `sensor_id` / `device_id` を `<tenant>/...` で前置
  - `TenantScope.wrap_callback(cb)` — 不許可テナントの自動ドロップ
  - `TenantRegistry.fanout(event)` — 全テナントへの並列配信
  - 22 件のテスト

### Tools / Quality

- 開発用依存に `hypothesis>=6.0` `ruff>=0.5` `coverage>=7.0` を追加
- bandit セキュリティスキャン: HIGH=0 / MEDIUM=0（産業コード 2,859 行対象）

---

## [2.0.1] — 2026-05-07

### Improved — Robustness, Optimization, Documentation

- **メモリリーク対策**: `AoiAdapter` / `DepthCameraAdapter` / `EventCameraAdapter` の
  `_seen` セットを `_SEEN_SET_MAX = 10_000` でキャップ、超過時に FIFO 半分削除
- **Atomic write 検出**: 3 アダプターに `_is_size_stable()` を追加。連続 2 ポーリングで
  サイズが変わらないファイルのみ処理（書き込み途中ファイルの誤読を防止）
- **定数化リファクタ**: マジックナンバーを名前付き定数化、用途コメント付与
  - `_SIDECAR_MAX_BYTES` / `_SUPPORTED_EXTENSIONS` / `_SEEN_SET_MAX`
  - `_STABILITY_TOLERANCE_BYTES` / `_EVENT_STRUCT_FMT` / `_MAX_EVENTS_PER_BATCH` 等
- **Property-based testing 導入** (`hypothesis>=6.0`):
  - `tests/test_property_based.py` — 12 関数 × 各 50 ランダム入力 = 600 ケース
  - PointCloud encode/decode roundtrip / DVS encode/decode roundtrip
  - MQTT topic matcher / TopicSpec validation / SlaveSpec validation
  - IndustrialPipeline value extraction（payload float64 / metadata physical_value）
- **静的解析クリーンアップ**: ruff 自動修正 27 件
  - `F401` 未使用 import 削除（mqtt_adapter.py の fnmatch / time / struct 等）
  - `UP035` 古い typing import を `collections.abc` へ
  - `UP037` クォート型ヒント簡略化
- **ドキュメント追加**: `docs/INDUSTRIAL_GUIDE.md` — Phase A〜G 全機能カタログ

---

## [2.0.0] — 2026-05-07

### Added — Full Industrial Integration (Phase G)

- **`llmesh/industrial/pipeline.py`** — `IndustrialPipeline`: 全Phase A〜F統合
  - `attach_mt(device_id, engine, threshold, ...)` — MT法エンジン登録（device単位）
  - `attach_cusum(sensor_id, target, k, h, sigma, ...)` — CUSUM管理図登録
  - `attach_xbar_r(sensor_id, chart, subgroup_size, ...)` — Xbar-R管理図登録（バッファ式）
  - `process(event)` → `DiagnosisResult` — 全アナライザを並走、最高severity診断を返却
  - `on_diagnosis(callback)` — 診断結果のサブスクリプション
  - 各アナライザ例外は分離（1つの失敗が他を壊さない）
- **`DiagnosisResult`** — frozen dataclass: status / severity / summary / evidence
  - `to_prompt_text()` — privacy-pipeline 入力形式へのフォーマット
- **`DiagnosisStatus`** — NORMAL / WARNING / ANOMALY / CRITICAL / UNKNOWN
- **デフォルト feature/value extractor** — payload float64/float32 自動デコード、
  EtherCATの `metadata["physical_value"]` も自動利用

### Changed
- **pyproject.toml** — version=2.0.0（PyPIリリース準備完了）

### Tests — 17件
- `tests/test_industrial_pipeline.py`: MT/CUSUM/Xbar-R統合・コールバック・extractor

### Industrial Phase A〜G 完了サマリー
- Phase A (v1.3.0): SensorEvent基盤
- Phase B (v1.4.0): Modbus + Serial
- Phase C (v1.5.0): MT法 + SPC(Xbar-R/CUSUM)
- Phase D (v1.6.0): OPC-UA + MQTT
- Phase E (v1.7.0): 3D Sensor (AOI/Depth/DVS) + SpatialSummarizer
- Phase F (v1.8.0): EtherCAT (10種PDOデータ型)
- Phase G (v2.0.0): IndustrialPipeline 全統合

---

## [1.8.0] — 2026-05-07

### Added — EtherCAT Adapter (Phase F)

- **`llmesh/industrial/ethercat_adapter.py`** — `EtherCATAdapter`（pysoem / Linux専用）
  - `SlaveSpec` dataclass: slave_pos / data_type（int8〜float64 10種）/ byte_offset / scale / offset
  - `add_slave(slave_pos, sensor_id, ...)` / `on_event(cb)` / `start()` / `stop()`
  - INIT→PRE-OP→SAFE-OP→OPERATIONAL 状態遷移を自動実行
  - `_do_cycle()`: `send_processdata()` / `recv_processdata()` → PDO読み取り → SensorEvent
  - scale/offset 変換で物理値を算出、payload = float64 LE bytes
  - 接続失敗・バスエラー時の自動再接続（`reconnect_delay_s`）
  - インターフェース名バリデーション（`[a-zA-Z0-9_\\-.]{1,15}`）
  - pysoem 未インストール時は明確な `RuntimeError`（Linux専用である旨を明示）
- **`pyproject.toml`** — `ethercat` extra（`pysoem>=1.1`）、エントリポイント追加、version=1.8.0
- **`llmesh/industrial/__init__.py`** — `EtherCATAdapter`, `SlaveSpec` を re-export

### Tests — 25件（pysoem完全モック）
- `tests/test_ethercat_adapter.py`: SlaveSpec検証 / 構築バリデーション / PDO解析 / ライフサイクル

---

## [1.7.0] — 2026-05-07

### Added — 3D Sensor Integration (Phase E)

- **`llmesh/industrial/sensor_3d/point_cloud.py`** — `PointCloud`: pure-stdlib 3D点群型
  - `PointCloud(points)` — N×3 float32タプルリスト
  - `to_bytes()` / `from_bytes()` — little-endian 12バイト/点 ワイヤフォーマット
  - `stats()` — count / x/y/z_range / centroid（numpy不要）
  - `from_iterable()` — 任意イテラブルからの生成

- **`llmesh/industrial/sensor_3d/aoi_adapter.py`** — `AoiAdapter`: AOI外観検査カメラ
  - `.jpg` / `.png` / `.bmp` ファイルのディレクトリ監視
  - `AoiResult` — JSON サイドカー（`.aoi.json`）パース（ok/ng/defects/board_id）
  - NG → `Priority.HIGH` 自動昇格、`priority_fn` でカスタマイズ可能
  - `move_processed_to` / `delete_after` でファイルライフサイクル管理
  - `SensorEvent(sensor_type="aoi_image")` を生成

- **`llmesh/industrial/sensor_3d/depth_adapter.py`** — `DepthCameraAdapter`: RGB-D深度カメラ
  - `.depth.bin`（raw uint32 header + float32 grid）および `.depth.npy`（numpy）対応
  - 有効深度フィルタリング（`max_range_m`、0以下除外）
  - PixelPosition + 深度値 → PointCloud 変換
  - `SensorEvent(sensor_type="depth_frame")` を生成、payload = PointCloud bytes

- **`llmesh/industrial/sensor_3d/event_adapter.py`** — `EventCameraAdapter`: DVSイベントカメラ
  - `.dvs.bin` ファイル監視（9バイト/イベント: x/y/t_us/polarity）
  - `DvsEvent` dataclass / `encode_dvs_events` / `decode_dvs_events`
  - `_batch_stats()` — フル decode なしでサマリー統計計算
  - イベント数/極性/時刻レンジをメタデータに付与

- **`llmesh/industrial/sensor_3d/spatial_summarizer.py`** — `SpatialSummarizer`: LLM向け3D要約
  - `summarize(event)` — sensor_type に応じて自動ディスパッチ
  - `aoi_image` → "AOI [board_id] OK/NG — N defects detected."
  - `depth_frame` → "Depth frame [device] N points; z min–max m, centroid"
  - `dvs_events` → "DVS [device] N events; +pos / -neg; Δt µs"
  - `max_defects_shown` で AOI 欠陥表示数を制限

- **`llmesh/industrial/sensor_3d/__init__.py`** — sensor_3d パッケージ初期化

### Tests

- `tests/test_sensor_3d_point_cloud.py` — 9件
- `tests/test_sensor_3d_aoi.py` — 13件
- `tests/test_sensor_3d_depth.py` — 7件
- `tests/test_sensor_3d_event.py` — 12件
- `tests/test_spatial_summarizer.py` — 10件
- 合計: **51件**新規

---

## [1.6.0] — 2026-05-07

### Added — OPC-UA + MQTT Adapters (Phase D)

- **`llmesh/industrial/opcua_adapter.py`** — `OPCUAAdapter`: OPC-UA クライアント（asyncua）
  - `OPCUAAdapter(endpoint_url, ...)` — `"opc.tcp://host:port"` 形式のエンドポイントに接続
  - `add_node(node_id, sensor_id, ...)` — OPC-UA ノードID を購読対象に追加
  - `on_event(callback)` — SensorEvent コールバック登録
  - `start()` / `stop()` — asyncio タスクとして購読ループを管理
  - サブスクリプション方式（ポーリングではなくプッシュ）でデータ変更通知を受信
  - `_SubHandler.datachange_notification()` で SensorEvent を生成
  - 接続失敗時の自動再接続ループ（`reconnect_delay_s` 設定可能）
  - asyncua 未インストール時は明確な `RuntimeError`

- **`llmesh/industrial/mqtt_adapter.py`** — `MQTTAdapter`: MQTT ブローカークライアント（paho-mqtt）
  - `MQTTAdapter(host, port, ...)` — MQTT v3.1.1 / v5.0 対応
  - `add_topic(topic, sensor_id, ...)` — MQTT トピックパターンを購読対象に追加
    - `+`（単一レベル）および `#`（複数レベル）ワイルドカード対応
  - `on_event(callback)` — SensorEvent コールバック登録
  - `start()` / `stop()` — paho スレッドループ + asyncio 統合
  - `_mqtt_topic_match(pattern, topic)` — MQTT §4.7 準拠のパターンマッチング
  - TLS 対応: `ssl.SSLContext` を `tls_context` パラメータで渡すだけ
  - username/password 認証対応
  - paho-mqtt 2.0 (`CallbackAPIVersion.VERSION2`) / 1.x 自動判別
  - 接続失敗時の自動再接続ループ（`reconnect_delay_s` 設定可能）
  - paho-mqtt 未インストール時は明確な `RuntimeError`

- **`llmesh/industrial/__init__.py`** 更新 — `OPCUAAdapter`, `NodeSpec`, `MQTTAdapter`, `TopicSpec` を re-export
- **`pyproject.toml`** — `opcua` / `mqtt` エントリポイントを `llmesh.industrial.*` に統一

### Tests

- `tests/test_opcua_adapter.py` — 17 件（asyncua モック）
  - NodeSpec 検証、構築バリデーション、add_node/on_event、\_SubHandler、ライフサイクル
- `tests/test_mqtt_adapter.py` — 25 件（paho-mqtt モック）
  - TopicSpec 検証、パターンマッチング、構築バリデーション、メッセージ処理、ライフサイクル

---

## [1.5.0] — 2026-05-07

### Added — Industrial Analysis Engines (Phase C)

- **`llmesh/industrial/mt_engine.py`** — `MTEngine`: Mahalanobis-Taguchi（MT法）エンジン
  - `MTEngine.fit(data)` — 正常データ（N×p 配列）からユニットスペース計算（平均・標準偏差・逆相関行列）
  - `MTEngine.md(sample)` — マハラノビス距離計算 `sqrt(z^T R^{-1} z / p)`
  - `MTEngine.is_anomaly(sample, threshold=3.0)` — 閾値判定
  - `MTEngine.md_batch(data)` — バッチ推論（shape: N → N,）
  - `MTEngine.save(path)` / `MTEngine.load(path)` — `.npz` による永続化
  - 零分散特徴量の graceful 処理、特異相関行列は擬似逆行列にフォールバック
  - numpy/scipy 未インストール時は明確な `RuntimeError`

- **`llmesh/industrial/spc_engine.py`** — SPCエンジン（純stdlib、追加依存なし）
  - `XbarRChart` — Shewhart Xbar-R 管理図（サブグループサイズ 2–10）
    - `fit(subgroups)` — UCL/LCL を ASTM 係数テーブル（A2, D3, D4）から計算
    - `check(subgroup)` → `SPCResult` — Xbar・R の管理外判定、違反メッセージ付き
  - `CUSUMChart` — 二方向 CUSUM 管理図（個別測定値対応）
    - `CUSUMChart(target, k, h, sigma=None)` — `k` は許容量、`h` は決定インターバル
    - `update(value)` → `SPCResult` — 累積和更新（`S+` / `S-`）・管理外フラグ
    - `is_out_of_control()` — 現在の累積和状態確認
    - `reset()` — 累積和クリア
  - `SPCResult` — frozen dataclass: `in_control`, `value`, `ucl`, `lcl`, `violations`, `extra`

- **`llmesh/industrial/__init__.py`** 更新 — `MTEngine`, `XbarRChart`, `CUSUMChart`, `SPCResult` を re-export

- **`llmesh/__main__.py`** — MT法 CLI コマンド追加
  - `llmesh mt-collect --device <id> --duration <sec> --output <file.npz>` — stdin からセンサーデータ収集
  - `llmesh mt-train --input <normal_data.npz> --device <id> [--output <unit_space.npz>]` — ユニットスペース学習
  - `llmesh mt-infer --model <unit_space.npz> [--threshold <float>]` — リアルタイム MD 推論（stdin）

### Tests

- `tests/test_mt_engine.py` — 24 件 (fit バリデーション・MD計算・is_anomaly・md_batch・save/load・単一特徴量)
- `tests/test_spc_engine.py` — 18 件 (XbarRChart fit/check・CUSUMChart init/update/reset・SPCResult)

### Bug Fix

- `CUSUMChart.update()`: S- 累積和の式 `- (x - target - k)` → `+ (target - x - k)` に修正（下方シフト検出ロジック）

### Verified

- 42 件全 PASS（numpy/scipy の graceful skip 1 件含む）
- 全スイート 1714 件収集予定・全 PASS

---

## [1.4.0] — 2026-05-06

### Added — Industrial Field Protocols (Phase B)

- **`llmesh/industrial/modbus_adapter.py`** — `ModbusAdapter`: Modbus TCP / RTU センサーポーリング
  - `ModbusAdapter.tcp(host, port)` / `ModbusAdapter.rtu(serial_port, baud_rate)` ファクトリ
  - `add_register()` で HOLDING / INPUT / COIL / DISCRETE を任意数登録
  - 非同期ポーリングループ（`asyncio.Task`）、再接続バックオフ対応
  - レジスタ値を big-endian bytes ペイロードに変換し `SensorEvent` として emit
  - `RegisterSpec` バリデーション: slave_id=1–247, address=0–65535, count=1–125
  - pymodbus 未インストール時に明確な `RuntimeError` を送出（graceful degradation）
- **`llmesh/industrial/serial_adapter.py`** — `SerialAdapter`: RS-232 / RS-485 シリアルセンサー入力
  - フレームモード 3 種: `line`（readline）/ `fixed`（固定バイト長）/ `delimited`（カスタム区切り）
  - バックグラウンドスレッドで受信、`threading.Event` でクリーンシャットダウン
  - `encoding` 指定時はデコードテキストを `metadata["text"]` に格納
  - ポートパス検証（`/dev/tty*`, `COM*` のみ許可、シェル注入防止）
  - pyserial 未インストール時に明確な `RuntimeError` を送出
- **`llmesh/industrial/__init__.py`** 更新 — `ModbusAdapter`, `SerialAdapter` 他を re-export
- **`pyproject.toml`** — version=1.4.0、entry-points `modbus`/`serial` を `llmesh.industrial.*` に修正

### Tests

- `tests/test_modbus_adapter.py` — 24 件 (RegisterSpec バリデーション・ファクトリ・ポーリング・エラー処理・ライフサイクル)
- `tests/test_serial_adapter.py` — 22 件 (ポートバリデーション・line/fixed/delimited モード・エンコーディング・Priority・コールバック分離・ライフサイクル)

### Verified

- 46 件全 PASS（pymodbus / pyserial モック使用）
- 全スイート 1672 件収集・全 PASS（v1.3.0 比 +93 件）

---

## [1.3.0] — 2026-05-06

### Added — Industrial Foundation (Phase A)

- **`llmesh/industrial/sensor_event.py`** — `SensorEvent` 統一センサーデータ型 (frozen dataclass)
  - `Priority` enum (CRITICAL / HIGH / NORMAL)
  - プロトコル非依存: `sensor_id`, `protocol`, `timestamp_ns`, `payload`, `device_id`, `sensor_type`, `unit`, `metadata`
  - `create()` ファクトリ、`with_priority()` コピー、`timestamp_s` プロパティ
- **`llmesh/config/industrial_config.py`** — `IndustrialConfig` dataclass
  - 語彙セット: `SUPPORTED_PROTOCOLS`, `SUPPORTED_DEVICE_TYPES`, `SUPPORTED_ANALYSIS_METHODS`, `NETWORK_POLICIES`
  - `from_dict()` / `to_dict()` / `is_configured()` / `uses_protocol()` / `uses_analysis()`
- **`llmesh/config/toml_config.py`** 更新 — `[industrial]` セクション対応
  - `LLMeshTomlConfig.industrial: IndustrialConfig` フィールド追加
  - 未設定時は `to_dict()` から省略（既存設定との後方互換）
- **`llmesh/__main__.py`** — `llmesh configure` コマンド追加 (Industry Setup Wizard)
  - 5ステップ対話形式: domain / device_types / protocols / analysis_methods / network policy
  - `tomli-w` 利用可能時はバイナリ書き出し、未インストール時はフォールバック実装
  - `--show` オプションで現在設定を表示、`--file` でカスタムパス指定
- **`pyproject.toml`** — `industrial` optional-dependencies グループ追加
  - `pymodbus>=3.6`, `pyserial>=3.5`, `asyncua>=1.0`, `paho-mqtt>=2.0`
  - `tomli-w>=1.0`, `numpy>=1.26`, `scipy>=1.12`
  - entry-points: `modbus`, `serial`, `opcua`, `mqtt` アダプター予約

### Tests

- `tests/test_industrial_sensor_event.py` — 23件
- `tests/test_industrial_config.py` — 24件

### Verified

- `llmesh configure` 動作確認済み: 5ステップ対話 Wizard、番号選択、tomli-w によるTOML書き出し、`--show`、既存設定の引き継ぎ — すべて正常

---

## [1.2.1] — 2026-05-06

### Security

- **`challenge/protocol.py`** — `random.choice()` を `secrets.choice()` に変更 (HIGH: 予測可能なチャレンジ選択を修正)
- **`fairness/witness.py`** — `random.sample()` を `secrets.SystemRandom().sample()` に変更 (MEDIUM: 共謀防止目的に不適切な乱数生成を修正)
- **`privacy/image_summarizer.py`** — captioner URL に SSRF ガード追加 (`_validate_captioner_url`): localhost / RFC 1918 アドレスのみ許可 (MEDIUM)
- **`.gitignore` 追加** — `certs/*.key` / `nodes/**/certs/*.key` を除外してプライベートキーの誤コミットを防止 (HIGH)

---

## [1.2.0] — 2026-05-06

### Added
- **ImageFirewall** (`llmesh/privacy/image_firewall.py`) — classify images before LLM ingestion
  - L4 images (face/ID document filename patterns, EXIF face tags) → BLOCK (fail-closed)
  - L3 images (screenshot filename patterns, wide aspect ratio ≥ 2.5×) → SUMMARIZE
  - L0/L1 images (diagrams, charts, code) → ALLOW (pass to LLM as placeholder)
  - Size gate: images > 10 MiB rejected before decode
  - EXIF metadata stripped; raw pixels never stored after classification
  - Graceful degradation when Pillow is unavailable (filename + magic-byte checks)
  - `classify_bytes(data, filename)` and `classify_path(path)` API

- **ImageSummarizer** (`llmesh/privacy/image_summarizer.py`) — L3 image → privacy-safe text
  - Strips EXIF and re-encodes image (no raw pixels forwarded)
  - Calls local Vision LLM (default: `ollama/llava`) to generate a text caption
  - Caption (not pixels) passed to main LLM backend
  - Configurable: `LLMESH_IMAGE_CAPTIONER`, `LLMESH_CAPTIONER_URL`, `LLMESH_CAPTIONER_TIMEOUT`
  - All failures return `blocked=True` (fail-closed)

- **LocalFileAdapter image support** (`llmesh/protocol/local_file_adapter.py`)
  - Accepts `*.prompt.png`, `*.prompt.jpg`, `*.prompt.jpeg`, `*.prompt.webp` in drop folder
  - Optional sidecar `*.prompt.txt` combined with image into multimodal prompt
  - File naming: `task.review_code.prompt.png` → tool `review_code` with image input
  - Full pipeline: ImageFirewall → ImageSummarizer (L3) → PromptFirewall → LLM → OutputValidator

- **MCP stdio server image support** (`llmesh/mcp/stdio_server.py`)
  - `tools/call` accepts optional `arguments.image_base64` (base64 PNG/JPEG)
  - Image routed through ImageFirewall → ImageSummarizer before LLM invocation
  - L4 images blocked; L3 images summarised to text description; L0/L1 as placeholder
  - Claude Code can send screenshots directly via the MCP tool call

### Changed
- `pyproject.toml`: version `1.1.0` → `1.2.0`; added `vision = ["Pillow>=10.0"]` optional extra
- MCP server version string updated to `"1.2.0"`

### Tests
- 71 new tests: `test_image_firewall.py` (35) + `test_image_summarizer.py` (22) + additions to `test_mcp_stdio_server.py` (14)
- 15 skipped (Pillow-dependent — install `llmesh[vision]` to enable)
- **Total: 1579 collected, all passing**

---

## [1.1.0] — 2026-05-06

### Added
- **SensorSummarizer** (`llmesh/privacy/sensor_summarizer.py`) — privacy-safe ROS sensor payload condensation
  - L4 sensors (face recognition, ID documents) → BLOCK (fail-closed)
  - L3 sensors (camera, depth images) → metadata description only, pixel data withheld
  - L2 sensors (GPS) → anonymised to ~1° grid cell (≈111 km resolution)
  - L1 sensors (LiDAR, IMU, sonar) → compact numeric summary (mean, std, min, max)
  - L0 sensors (temperature, diagnostics) → pass-through
  - Auto-classification from ROS topic name; sensor_type override supported
  - EXIF / ROS header timestamps stripped; no raw pixel data ever in output

- **ROS2Adapter** (`llmesh/protocol/ros2_adapter.py`) — ROS 2 topic/service bridge
  - Subscribe `/llmesh/request` (std_msgs/String) → LLM pipeline → publish `/llmesh/response`
  - Opt-in: `LLMESH_ENABLE_ROS2=1`
  - L3/L4 messages rejected unconditionally at adapter boundary
  - Node authentication via explicit `node_allowlist` or DDS Security (SROS2)
  - Sensor payload pre-processing via `SensorSummarizer` (pass `sensor_topic` + `sensor_data`)
  - Nonce derived from ROS header stamp; falls back to `uuid4`
  - Registered as `"ros2"` in `AdapterRegistry`

- **ROS1Adapter** (`llmesh/protocol/ros1_adapter.py`) — ROS 1 Noetic bridge (opt-in, legacy)
  - Same privacy pipeline and L3/L4 restrictions as ROS2Adapter
  - Double opt-in: `LLMESH_ENABLE_ROS1=1` AND `LLMESH_ROS1_LEGACY_ACK=1`
  - Deprecation warning logged every time adapter starts (ROS 1 EOL May 2025)
  - Will be removed in LLMesh v2.0
  - Registered as `"ros1"` in `AdapterRegistry`

### Changed
- `pyproject.toml`: version `1.0.1` → `1.1.0`; added `ros2` and `ros1` entry-points
- ROS dependencies (`rclpy`, `rospy`) are system packages — not added to PyPI extras

### Tests
- 90 new tests: `test_sensor_summarizer.py` (44) + `test_ros2_adapter.py` (26) + `test_ros1_adapter.py` (20)
- **Total: 1508 passed, 8 skipped**

---

## [1.0.1] — 2026-05-06

### Added
- **LocalFileAdapter** (`llmesh/protocol/local_file_adapter.py`) — drop-folder LLM task processing
  - Drop `*.prompt.txt` in `in_dir/` → result appears as `*.result.txt` in `out_dir/`
  - Tool name encoded in filename: `task.review_code.prompt.txt` → `review_code`
  - Processed prompts archived to `in_dir/processed/` (originals preserved)
  - Pre-existing files in `in_dir/` processed at startup
  - Prompt size capped at 256 KiB; oversized prompts write `{"error": "prompt_too_large:..."}`
  - Full privacy pipeline per file (PromptFirewall → PrivacySummarizer → LLM → OutputValidator)
  - Errors (blocked / backend / validation) written as `{"error": "..."}` to result file
  - `pip install llmesh[localfile]` adds `watchdog>=3.0`
  - Registered as `"localfile"` in `AdapterRegistry`
  - 24 new tests

### Changed
- `pyproject.toml`: added `localfile` entry-point and `localfile = ["watchdog>=3.0"]` optional dep
- `all` extra now includes `watchdog>=3.0`

### Tests
- **Total: 1418 passed, 8 skipped**

---

## [1.0.0] — 2026-05-06

### Added
- **Unified TOML configuration** (`llmesh/config/toml_config.py`)
  - `LLMeshTomlConfig.load()` reads `llmesh.toml` (stdlib `tomllib`; Python 3.11+)
  - Sections: `[node]`, `[adapters]`, `[security]`, `[circuit_breaker]`
  - Falls back to environment variables when file is absent (fully backwards-compatible)
  - `AdapterConfig`, `SecurityConfig`, `CircuitBreakerConfig` dataclasses

- **Entry-points adapter auto-discovery** (`llmesh/protocol/registry.py`)
  - `AdapterRegistry.load_entrypoints()` — loads adapters declared under
    `[project.entry-points."llmesh.adapters"]` in third-party `pyproject.toml` files
  - Uses `importlib.metadata.entry_points`; failures skipped silently
  - Built-in adapters (http, tcp, udp, ssh, sftp, smtp, imap, pop3, ftp, snmp)
    now declared as entry-points in `pyproject.toml`

- **MCP stdio server** (`llmesh/mcp/stdio_server.py`)
  - MCP JSON-RPC 2.0 over stdin/stdout with Content-Length framing
  - Launched via `python -m llmesh serve-mcp`
  - Implements: `initialize`, `tools/list`, `tools/call`, `ping`
  - Full privacy pipeline per call (PromptFirewall → PrivacySummarizer → LLM → OutputValidator)
  - Server-side nonce generation (Claude Code callers need not supply one)
  - Configurable via `LLMESH_BACKEND`, `LLMESH_MODEL`, `LLMESH_BACKEND_URL`
  - Claude Code config:
    ```json
    {"mcpServers": {"llmesh": {"command": "python", "args": ["-m", "llmesh", "serve-mcp"], "env": {"LLMESH_BACKEND": "ollama"}}}}
    ```

- **`claude` optional dependency**: `pip install llmesh[claude]` adds `mcp>=1.0`

### Changed
- `pyproject.toml` version bumped to `1.0.0`
- `python -m llmesh` now accepts `serve-mcp` subcommand
- Help text updated to include `serve-mcp`

### Tests
- 56 new tests: `test_toml_config.py` (22), `test_mcp_stdio_server.py` (25), `test_registry_entrypoints.py` (9)
- **Total: 1394 passed, 8 skipped**

---

## [0.9.0] — 2026-05-06

### Added
- **TelnetAdapter** (`llmesh/protocol/telnet_adapter.py`)
  - asyncio-based Telnet server, explicitly deprecated at implementation
  - Double opt-in required: `LLMESH_ENABLE_TELNET=1` AND `LLMESH_UNSAFE_TELNET_NO_TLS=1`
  - Startup warning: "TELNET IS UNENCRYPTED — NOT FOR PRODUCTION USE"
  - L3/L4 prompts rejected unconditionally at the Telnet boundary
  - Minimal IAC option negotiation (DONT/WONT for all client requests)
  - Message size capped at 1 MiB; each connection is isolated (no auth state)

- **Cross-protocol security hardening** (`llmesh/security/cross_protocol.py`)
  - `CrossProtocolNonceGuard` — wraps any NonceStore; nonce deduplication is
    protocol-transparent (HTTP nonce cannot be replayed via SMTP)
  - `UnifiedRateLimiter` — single PerNodeRateLimiter shared across all adapters;
    keys are `"<protocol>:<node_id>"` for per-protocol budgets
  - `AdapterCircuitBreakerRegistry` — one CircuitBreaker per `(adapter, node_id)`;
    shared registry means cross-adapter quarantine of misbehaving nodes

### Tests
- `tests/test_telnet_adapter.py` — 29 tests (opt-in guard, option stripping,
  L3/L4 rejection, echo, oversized drop, option negotiation)
- `tests/test_cross_protocol_security.py` — 25 tests (nonce guard, unified
  rate limiter, adapter circuit breaker registry)
- **Total: 1338 passed, 8 skipped**

---

## [0.8.0] — 2026-05-06

### Added
- **SNMPv3 agent** (`llmesh/protocol/snmp_adapter.py`) — pysnmp 7.x; OID tree
  under `enterprises.llmesh (1.3.6.1.4.1.99999).*`; read-only
- **NTP clock sync** (`llmesh/security/clock.py`) — raises `ClockDriftError`
  if drift exceeds threshold; configurable via env vars
- **DNS-SD v2** (`llmesh/discovery/dns_sd.py`) — enhanced TXT + SRV records
  via zeroconf 0.148.x; `DnsSdAnnouncer` + `DnsSdConfig`
- **Tests:** 66 new (clock:17 + snmp:30 + dns_sd:19) — 1284 total

---

## [0.7.0] — 2026-05-05

### Added
- **FTP/FTPS adapter** (`llmesh/protocol/ftp_adapter.py`) — pyftpdlib 2.x;
  FTPS by default; self-signed cert auto-generated; per-user isolated dirs
- **Tests:** 23 new — 1218 total

---

## [0.6.0] — 2026-05-05

### Added
- **SMTP intake** (`llmesh/protocol/smtp_adapter.py`) — aiosmtpd; trusted
  sender allowlist; text/plain attachments only
- **IMAP poller** (`llmesh/protocol/imap_adapter.py`) — imaplib; configurable
  poll interval; marks processed emails `\Seen`
- **POP3 poller** (`llmesh/protocol/pop3_adapter.py`) — poplib; retrieve and
  delete; sequential processing
- **Tests:** 74 new (smtp:24 + imap:24 + pop3:26) — 1187 total

---

## [0.5.0] — 2026-05-04

### Added
- **SSH adapter** (`llmesh/protocol/ssh_adapter.py`) — paramiko 4.x;
  Ed25519 public-key auth; exec channel; replay via session-ID nonce seed
- **SFTP adapter** (`llmesh/protocol/sftp_adapter.py`) — paramiko SFTP
  subsystem; `<task_id>.prompt.txt` → `<task_id>.result.txt` convention
- **Fairness system** — ServiceReceipt / Ledger / Policy / Witness (83 tests)
- **Tests:** 40 new SSH/SFTP — 1113 total

---

## [0.4.0] — 2026-05-03

### Added
- UDP adapter, DNS-SD v1, NTP pre-check foundation

---

## [0.3.0] — 2026-05-02

### Added
- ProtocolAdapter ABC, HttpAdapter, TCPAdapter, AdapterRegistry

---

## [0.2.0] — 2026-05-01

### Added
- SQLite NonceStore, multi-process AuditTrace locking, TrustedPeers TTL,
  CapabilityManifest signing, STRIDE threat model
