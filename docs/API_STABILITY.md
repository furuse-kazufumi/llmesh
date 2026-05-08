# LLMesh API Stability Policy

LLMesh は v2.13 以降、**Public API** と **Internal API** を明確に分離し、
v3.0.0 以降は SemVer に厳密に従います。本ドキュメントはダウンストリーム
ユーザーが安心して LLMesh に依存できるよう、ストック面と変更プロセスを
規定します。

---

## 1. 公開 API（Public API）

公開 API とは、以下のいずれかに**明示的に列挙されている**シンボルです:

- `llmesh/__init__.py` の `__all__`
- 各サブパッケージの `__init__.py` の `__all__`
- 本ドキュメントの「公開シンボル一覧」セクション

それ以外（プライベートサブモジュール、`_` で始まる名前、未エクスポートの
クラス/関数）は **Internal**（非公開）です。Internal を直接 import する
コードは、マイナー / パッチアップデートで予告なく壊れる可能性があります。

### 公開シンボル一覧（v2.14 時点）

#### `llmesh`（トップレベル）

```python
from llmesh import (
    __version__,
    DataLevel, ClassifiedPayload,
    PromptFirewall, FirewallDecision,
    PresidioDetector, PresidioResult,
    PrivacySummarizer,
    SensorEvent, Priority,
)
```

#### `llmesh.privacy`

`PromptFirewall`, `FirewallDecision`, `PresidioDetector`, `PresidioResult`,
`PrivacySummarizer`, `SummaryResult`, `SummarizationError`

#### `llmesh.rag`

`Embedder`, `MockEmbedder`, `OllamaEmbedder`, `EmbeddingError`,
`Document`, `RetrievedDocument`, `VectorStore`,
`NumpyVectorStore`, `SqliteVectorStore`, `LSHVectorStore`,
`Retriever`, `RetrievalResult`

#### `llmesh.industrial`（v3 関連）

| シンボル | 由来 |
|---------|------|
| `SensorEvent`, `Priority` | v1.3+（コア） |
| `MTEngine`, `XbarRChart`, `CUSUMChart`, `SPCResult` | v1.5+ |
| `OnlineMTEngine`, `HotellingT2Chart`, `EventDensityMap` | v2.13+ |
| `UnifiedSPC`, `UnifiedSPCResult` | v2.13+ |
| `LLMExplainer`, `AlarmEvent`, `IncidentReport` | v2.13+ |
| `ExplainedCUSUM`, `ExplainedSPCResult` | v2.14+ |
| `VideoCUSUM`, `VideoCUSUMResult` | v2.14+ |
| `VLMFeatureExtractor`, `VLMFeature`, `MockVisionCaptioner` | v2.14+ |
| `DNP3Adapter`, `DNP3Point` | v2.14+（skeleton） |
| `GOOSEAdapter`, `GoosePDU`, `GooseTransport` | v2.14+ |

#### `llmesh.protocol`

各 Adapter（`HTTPAdapter`, `TCPAdapter`, `UDPAdapter`, `SSHAdapter`,
`SFTPAdapter`, `SMTPAdapter`, `IMAPAdapter`, `POP3Adapter`, `FTPAdapter`,
`SNMPAdapter`, `TelnetAdapter`, `ROS2Adapter`, `ROS1Adapter`）+
`ProtocolAdapter`, `AdapterRegistry`, `UnifiedMessage`, `MessageType`,
`TaskRequest`, `TaskResponse`, `TransportError`

---

## 2. SemVer の適用範囲

**v3.0.0 以降、公開 API は SemVer（major.minor.patch）に従います。**

| 変更タイプ | バンプ | 例 |
|-----------|------:|----|
| 公開 API のシグネチャ変更 / 削除 | **major** | `PromptFirewall.classify` の引数追加（必須）|
| 新規公開 API 追加（既存非破壊）| **minor** | `LSHVectorStore` 追加 |
| バグ修正 / 内部最適化 / ドキュメント | **patch** | RFC 6455 準拠修正 |

**v3 以前（v0.x – v2.x）**: 全てのリリースは「実質 minor / patch」相当の
変更として扱い、互換性を可能な限り維持してきました。v3.0.0 で SemVer
保証を正式に開始します。

---

## 3. Deprecation プロセス

公開 API を破棄する場合は、以下の手順で段階的に移行します:

1. **Deprecation 宣言（minor リリース）**: 該当シンボルに
   `warnings.warn(DeprecationWarning, stacklevel=2)` を仕込み、
   `docs/CHANGELOG.md` の `### Deprecated` セクションに記載。
   後継 API が存在する場合はそれも提示します。
2. **最低 1 つの minor リリース猶予**: ダウンストリームが移行する時間を
   確保。Deprecation 期間中は機能は維持されます。
3. **削除（次の major リリース）**: 該当シンボルを削除し、
   `docs/CHANGELOG.md` の `### Removed` セクションに記載。

## 4. ABI / wire-protocol の安定性

LLMesh は **Python パッケージ** として提供されます。Rust 拡張
（`llmesh_rust`）は CPython ABI に依存しますが、ABI 互換性は
`maturin` のターゲット指定で保証されます。

外部プロトコル（HTTP / MCP / DNP3 / GOOSE 等）の wire-protocol 互換性は
**それぞれの規格仕様**（RFC、IEEE 1815、IEC 61850）に従います。LLMesh の
内部実装が変更されても、相手機器との通信互換は維持されます。

---

## 5. 後方互換性ガイド

| 状況 | 推奨対応 |
|------|----------|
| Pin したい | `pip install "llmesh~=2.14"`（`>=2.14, <3.0`） |
| 新機能を試したい | `pip install --pre llmesh` でプレリリース取得 |
| 公開 API のみ使いたい | `from llmesh import …` または `from llmesh.<sub> import …`（`__all__` のみ） |
| Internal も使う必要 | リスクを承知の上で。CHANGELOG をパッチごとに確認 |

---

## 6. 互換性破壊時の通知チャネル

- `docs/CHANGELOG.md` の `### Breaking` セクション
- GitHub Releases ノート
- Deprecation 警告は `warnings` 経由で実行時に通知

---

## 7. テストポリシー

公開 API は **必ずユニットテストカバレッジ ≥ 95%**。Internal は
強制しませんが、ファイル単位で 80% 以上を推奨します。

カバレッジは `coverage run -m pytest && coverage report` で計測可能。
