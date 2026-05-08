# LLMesh Edge Layer — 要件定義 (カテゴリB)

**対象バージョン:** v0.4.x → v0.5.0  
**目的:** IoTエッジ・制約デバイス対応と転送信頼性向上

---

## B-2: MessagePack バイナリシリアライゼーション

**状態:** 実装完了

**要件:**
- `llmesh[msgpack]` optional extra として `msgpack>=1.0` を追加（済み）
- `UnifiedMessage.to_bytes(codec="json"|"msgpack")` でコーデック選択
- `UnifiedMessage.from_bytes()` が first-byte auto-detect（`{` = JSON, その他 = msgpack）
- 全アダプタ（TCP / UDP / TCPStream）がコンストラクタで `codec` パラメータを受付
- msgpack 未インストール時は `RuntimeError` + インストールヒント

**実装ファイル:**
- `llmesh/protocol/codec.py` — encode/decode/is_msgpack_available
- `llmesh/protocol/message.py` — to_bytes(codec)/from_bytes 更新
- `llmesh/protocol/tcp_adapter.py` — codec param 追加
- `llmesh/protocol/udp_adapter.py` — codec param 追加
- `llmesh/protocol/tcp_stream_adapter.py` — codec param 追加（_ConnAdapter含む）
- `tests/test_codec.py` — JSON/msgpack ラウンドトリップ等

---

## B-1: QoS / Deadline

**状態:** 実装完了（2026-05-06）

**要件:**
- `UnifiedMessage` に `priority: int = 0`（高いほど優先）と `deadline: float | None = None`（Unix timestamp）フィールド追加
- `FanoutExecutor` / `NodeClient` が deadline 超過メッセージを送信前にドロップ
- UDP アダプタの送信キューで priority ソート（高優先を先送り）
- deadline / priority は `to_dict()` / `from_dict()` でシリアライズ

---

## B-3: Store-Forward (OutboxQueue)

**状態:** 実装完了（2026-05-06）

**要件:**
- `llmesh/protocol/outbox.py` — SQLite バックd OutboxQueue
- DBパスは設定ファイル（`llmesh.toml` または env var `LLMESH_OUTBOX_PATH`）で指定
- 送信失敗時にメッセージをDBへ保存、再接続後に自動再送
- TTL 切れメッセージは自動削除（`deadline` フィールドを利用）
- 対象アダプタ: TCPStreamAdapter（永続接続前提）

---

## B-4: DeviceProfile / NANOプロファイル

**状態:** 実装完了（2026-05-06）

**要件:**
- `llmesh/protocol/device_profile.py` — DeviceProfile dataclass
- プロファイル種別: `FULL`（デフォルト）/ `NANO`（制約デバイス向け）
- `NANO` プロファイルでは Ed25519 署名を省略可（`LLMESH_NANO_NO_CRYPTO=1`）
- アダプタが `device_profile` パラメータを受け付け、NANO 時はペイロードサイズ上限を 1 KB に制限
- NANOノードはTCPStream非対応（UDPのみ）

---

## B-5: Routing拡張

**状態:** 実装完了（2026-05-06）

**要件:**
- `UnifiedMessage` に `route: list[str] = []` フィールド追加（通過ノードIDのリスト）
- `SmartNodeSelector` が route 情報を参照して最短パスを選択
- ループ防止: 自ノードIDが route に含まれる場合はドロップ
- 最大ホップ数: `ttl` フィールドを流用（デクリメントして 0 でドロップ）

---

## B-6: テスト

**状態:** 実装完了（2026-05-06）

**要件:**
- B-1: deadline 超過ドロップ、priority ソートの単体テスト
- B-3: OutboxQueue のSQLite永続化・再送・TTL削除テスト
- B-4: NANOプロファイルで署名省略・サイズ制限動作確認テスト
- B-5: routeループ防止・TTLデクリメントの単体テスト
- 統合テスト: B-2+B-3+B-4 を組み合わせたエンドツーエンドシナリオ

---

# Volume C: 広範囲適用 v3 要件（2026-05-07 計画策定）

LLMesh は v2.0.1 で産業 IoT 用途を完全カバー。v3 では下記分野へ適用範囲を拡大する。

## C-1: 医療機器統合（Medical IoMT）

- **C-1.1** HL7 FHIR R5 アダプター — 検査結果の REST 受信
- **C-1.2** DICOM 受信アダプター — 医用画像（MRI/CT）の C-STORE
- **C-1.3** HIPAA 準拠監査 — `audit/trace.py` を BAA 対応に拡張
  - PHI（個人健康情報）自動検出 → L4 分類で BLOCK
- **C-1.4** 心電図 (ECG) ストリーム要約器 — `MedicalSummarizer`

## C-2: 自動車・モビリティ（Automotive）

- **C-2.1** CAN-bus アダプター — `python-can` 経由（SocketCAN / Vector / PCAN）
- **C-2.2** AUTOSAR SOME/IP アダプター — VSomeIP バインディング
- **C-2.3** OBD-II 診断ツール — DTC（Diagnostic Trouble Code）の自動説明生成
- **C-2.4** UDS (ISO 14229) サービス — フリーズフレーム要約

## C-3: 航空・防衛（Aerospace / Defense）

- **C-3.1** ARINC 429 アダプター — 航空電子機器バス
- **C-3.2** MIL-STD-1553 アダプター — 軍用航空バス
- **C-3.3** SCAP / STIG 対応セキュアモード — フェイルクローズ強化

## C-4: 金融取引（FinTech）

- **C-4.1** FIX 4.4 / 5.0 アダプター — quickfix-python 経由
- **C-4.2** ISO 20022 メッセージング — XML/JSON 受信
- **C-4.3** マーケットデータプライバシーゲート — 注文情報の匿名化要約

## C-5: スマートシティ・LPWA

- **C-5.1** LoRaWAN アダプター — ChirpStack ブリッジ
- **C-5.2** Sigfox アダプター — Backend Cloud REST
- **C-5.3** NB-IoT アダプター — 3GPP CoAP

## C-6: エネルギー・ユーティリティ

- **C-6.1** IEC 61850 GOOSE / MMS アダプター — 変電所制御
- **C-6.2** DLMS/COSEM スマートメーター — 電力量検針
- **C-6.3** SunSpec モデル対応 — 太陽光発電インバータ

## C-7: 農業・FieldBus

- **C-7.1** ISOBUS (ISO 11783) アダプター — トラクター・農機データ
- **C-7.2** 衛星画像ベースの NDVI 計算 → SpatialSummarizer 拡張
- **C-7.3** 気象 API ブリッジ（オプトイン LAN ローカル）

## C-8: 防災・観測

- **C-8.1** 地震計 SEED フォーマット入力アダプター
- **C-8.2** 気象観測 (METAR / SYNOP) アダプター
- **C-8.3** 早期警報メッセージ翻訳器 — LLM による多言語化

## C-9: ロボティクス拡張

- **C-9.1** ROS 2 Action server / client — `rclpy` 拡張
- **C-9.2** SLAM 結果サマリー — 占有グリッドの圧縮要約
- **C-9.3** AI モーションプランナーへの自然言語タスク変換

## C-10: バイオ・科学計測

- **C-10.1** SCPI 機器制御アダプター — オシロスコープ・電源
- **C-10.2** PCR / シーケンサーログ解析 — FASTQ サマライザー
- **C-10.3** LIMS（実験室情報管理）統合 — REST 受信

## C-11: 横断要件 — マルチテナント分離

- **C-11.1** テナント単位の名前空間（プロトコル / トピック / Modbus スレーブ）
- **C-11.2** RBAC（Role-Based Access Control）— 役割別ツール公開制御
- **C-11.3** テナント別 fairness ledger — 公平性指標分離

## C-12: 横断要件 — 性能最適化（Rust 拡張）

- **C-12.1** PointCloud encode/decode の Rust 実装（PyO3 + maturin）
- **C-12.2** DVS encode/decode の Rust 実装
- **C-12.3** MTEngine の MD バッチ計算を Rust 化（numpy 依存廃止）
- **C-12.4** Python フォールバック維持（Rust 未ビルド環境で純 Python 動作）

## C-13: 横断要件 — 観測性 / SRE

- **C-13.1** OpenTelemetry トレース統合 — span propagation 標準化
- **C-13.2** Prometheus exporter — 各アダプターのメトリクス
- **C-13.3** SLI/SLO ダッシュボード自動生成（Grafana JSON）

## C-14: 横断要件 — エッジ AI 最適化

- **C-14.1** ONNX Runtime バックエンド — Ollama / LlamaCpp に並ぶ第 3 選択肢
- **C-14.2** TFLite Micro 連携 — マイコン推論結果の SensorEvent 化
- **C-14.3** スパース化・量子化済みモデルのプロビジョニング

## C-15: 横断要件 — フェデレーション学習（オプトイン）

- **C-15.1** FedAvg プロトコル — モデル重み平均化（生データ非送信）
- **C-15.2** Differential Privacy — 重み更新へのノイズ付与
- **C-15.3** Secure Aggregation — Threshold 暗号での平均化

---

## Volume C 優先順位（提案）

| 優先 | 項目 | 理由 |
|------|------|------|
| ★★★ | C-12（Rust 拡張） | 既存性能ボトルネック解消・即効性 |
| ★★★ | C-13（観測性） | 全機能横断・運用必須 |
| ★★ | C-2（自動車 CAN） | 産業 IoT に近い・需要大 |
| ★★ | C-1（医療 FHIR） | プライバシー優位性が活きる |
| ★★ | C-11（マルチテナント） | エンタープライズ需要 |
| ★ | C-3〜C-10 | 領域別ニッチ、需要に応じて段階展開 |
| ★ | C-14, C-15 | 研究フェーズ |

---

# Volume D: 画像処理拡張要件（2026-05-07 計画策定）

LLMesh は v1.7.0 で AOI / 深度 / DVS の 3 種類の画像系センサーをサポート。
v3 後半では汎用画像処理パイプラインへ拡張する。

## D-1: 画像入力アダプター拡張

- **D-1.1** RTSP / RTMP ストリーム受信アダプター — `av` (PyAV) 経由
- **D-1.2** GenICam (GigE Vision / USB3 Vision) アダプター — Harvester 経由
- **D-1.3** Video4Linux2 (V4L2) アダプター — Linux カメラデバイス
- **D-1.4** ONVIF Profile S/T プロトコル対応 — 監視カメラ標準
- **D-1.5** RealSense / Azure Kinect SDK 直接連携（depth_adapter 拡張）
- **D-1.6** 産業用ラインスキャンカメラ — Camera Link / CoaXPress

## D-2: 画像前処理パイプライン

- **D-2.1** `ImageProcessor` 抽象クラス — チェーン可能な前処理ステップ
  - `crop` / `resize` / `rotate` / `denoise` / `histogram_equalize`
  - `bayer_demosaic` / `gamma_correct` / `auto_white_balance`
- **D-2.2** ROI（Region Of Interest）マスキング — プライバシー領域の自動黒塗り
  - 顔検出（OpenCV Haar / YOLO-Face）→ 自動 BLOCK or BLUR
  - ナンバープレート検出 → 自動マスク
- **D-2.3** `ImagePipeline` — `SensorEvent → preprocess → analyzer → DiagnosisResult`
- **D-2.4** GPU 加速対応（CUDA / ROCm / Metal）— optional 実装

## D-3: 画像認識・解析エンジン

- **D-3.1** `OnnxRuntimeBackend` — ONNX モデルでの推論（C-14.1 連動）
  - 物体検出（YOLOv8 / YOLOX）/ セグメンテーション（SAM）
  - 異常検知（PaDiM / PatchCore）— 教師なし
- **D-3.2** `OpticalCharacterRecognizer` — Tesseract / PaddleOCR バックエンド
  - シリアル番号 / バーコード / 計器盤読み取り
- **D-3.3** `BarcodeReader` — pyzbar 経由（1D / 2D / DataMatrix / QR）
- **D-3.4** `ColorAnalyzer` — Lab / HSV ヒストグラム + 統計値
- **D-3.5** `BlobAnalyzer` — 連結成分（contours）と面積・周囲長・円形度
- **D-3.6** `EdgeAnalyzer` — Canny / Sobel エッジ密度

## D-4: 画像系プライバシー強化

- **D-4.1** `FaceFirewall` — 顔領域の自動 BLOCK（v1.2.0 ImageFirewall 拡張）
  - L4 強制：顔検出があれば LLM への送信を BLOCK
  - L3 オプトイン：顔をモザイク化して通過
- **D-4.2** `LicensePlateRedactor` — 自動車ナンバー自動マスク
- **D-4.3** `ScreenContentFirewall` — 画面キャプチャ内 PII 検出（OCR + PII）
- **D-4.4** `MetadataStripper` — EXIF / IPTC / XMP の完全削除（既存 v1.2.0 拡張）
  - GPS 座標 / カメラ シリアル / 撮影者名 を除去
- **D-4.5** Differential Privacy 画像生成（任意）— 統計集計でランダムノイズ付与

## D-5: 動画 / 時系列画像

- **D-5.1** `VideoAdapter` — フレーム連続入力としての SensorEvent ストリーム
- **D-5.2** `MotionDetector` — フレーム間差分で動体検知
- **D-5.3** `OpticalFlow` — Lucas-Kanade / Farneback
- **D-5.4** `FrameRateAdjuster` — 入力 FPS → 解析 FPS 動的調整（背圧制御）
- **D-5.5** タイムラプス・ハイパーラプス対応

## D-6: 3D 拡張

- **D-6.1** `MeshAdapter` — STL / PLY / OBJ / glTF ファイル受信
- **D-6.2** `PointCloudPlus` — 既存 PointCloud に色（RGB）/ 法線（normal）追加
- **D-6.3** `VoxelGrid` — 占有グリッド表現（OctoMap 互換）
- **D-6.4** ICP（Iterative Closest Point）位置合わせ
- **D-6.5** `MeshSummarizer` — メッシュ → LLM 向けテキスト統計

## D-7: 医用画像（HIPAA / DICOM 連携、C-1 と統合）

- **D-7.1** DICOM C-STORE 受信アダプター — pydicom / pynetdicom
- **D-7.2** DICOM PHI スクラビング — 患者氏名 / ID / 撮影日時の自動除去
- **D-7.3** ボクセル切片の `MedicalSummarizer`（CT / MRI 統計サマリー）

## D-8: 画像処理仕様（共通）

- **D-8.1** すべての画像処理は **入力サイズ上限** を強制（OOM 防止）
  - デフォルト: 8K × 8K × 4ch = 1 GB raw
  - 上限超過 → 即時 BLOCK
- **D-8.2** **タイムアウト**: 1 フレーム処理 ≤ 5 秒（設定可能）
- **D-8.3** **メモリプール再利用**: 同サイズフレームの ndarray 再利用で GC 圧軽減
- **D-8.4** **GPU メモリ枯渇対策**: OOM 検出 → CPU フォールバック → エラー
- **D-8.5** **モデルキャッシュ**: ONNX / OCR モデルの遅延ロード + LRU キャッシュ

## D-9: 開発・運用要件（共通）

- **D-9.1** `ImageMetrics` — IndustrialMetrics 拡張
  - フレーム処理レイテンシ / FPS / OCR 信頼度ヒストグラム
  - 顔検出ヒット数 / プライバシー BLOCK 件数
- **D-9.2** `ImageTracing` — 各処理ステップを Span で計測
- **D-9.3** ベンチマーク: `examples/image_pipeline_bench.py`
- **D-9.4** ハードウェアアクセラレータ自動検出（CPU / CUDA / Metal / ROCm）

## Volume D 優先順位

| 優先 | 項目 | 理由 |
|------|------|------|
| ★★★ | D-4（プライバシー強化） | LLMesh の差別化要因と直結 |
| ★★★ | D-3.1 ONNX | C-14.1 と統合・即効性 |
| ★★ | D-1.1 RTSP / D-1.3 V4L2 | カメラソース最も一般的 |
| ★★ | D-3.2 OCR / D-3.3 Barcode | 工場現場で需要大 |
| ★★ | D-2.1 ImageProcessor / D-2.3 ImagePipeline | 全機能の土台 |
| ★ | D-5 動画 / D-6 3D 拡張 / D-7 医用 | 領域別、段階展開 |

## 期待される依存関係（optional extras）

```toml
image_io  = ["opencv-python>=4.9", "Pillow>=10.0"]   # 既存 vision を拡張
image_ai  = ["onnxruntime>=1.17"]                     # CPU 推論
image_ocr = ["pytesseract>=0.3", "pyzbar>=0.1"]
image_3d  = ["trimesh>=4.0", "open3d>=0.18"]          # mesh / voxel
medical   = ["pydicom>=2.4", "pynetdicom>=2.0"]
genicam   = ["harvesters>=1.4"]
rtsp      = ["av>=12.0"]
```

---

# Volume E: 既存 PyPI / GitHub 人気ライブラリ統合計画（2026-05-07 策定）

LLMesh の差別化を維持しつつ、業界デファクトのライブラリと相互運用するための統合計画。
各項目は **既存 LLMesh 抽象（SensorEvent / IndustrialAdapter / Pipeline）に合わせて
ラップする** という原則で設計する（薄いアダプター層）。

## E-1: PLC / 産業デバイス通信（業界デファクト追加）

| ライブラリ | 主用途 | 統合方針 | 優先 |
|-----------|------|---------|------|
| `python-snap7` | Siemens S7-1200/1500/300/400 PLC | `S7Adapter`（ModbusAdapter と同パターン） | ★★★ |
| `pycomm3` | Allen-Bradley/Rockwell ControlLogix | `EtherNetIPAdapter`（CIP プロトコル） | ★★★ |
| `pylogix` | A-B ControlLogix（軽量代替） | pycomm3 のフォールバック | ★ |
| `pyads` | Beckhoff TwinCAT ADS | `ADSAdapter`（EtherCAT との並走可能） | ★★ |
| `minimalmodbus` | Modbus 軽量版 | 既存 ModbusAdapter にバックエンド切替で対応 | ★ |
| `pyhomie4` | Homie MQTT 規約 | MQTTAdapter にプロファイル追加 | ★ |
| `apache-plc4py` | マルチ PLC ゲートウェイ | optional / 包括対応 | ★ |

## E-2: PII / プライバシー検出（既存 PromptFirewall を強化）

| ライブラリ | 主用途 | 統合方針 | 優先 |
|-----------|------|---------|------|
| `microsoft/presidio-analyzer` | 業界デファクト PII 検出 | PromptFirewall の検出器プラグインとして統合 | ★★★ |
| `microsoft/presidio-anonymizer` | PII 匿名化 | PrivacySummarizer と二段構成 | ★★★ |
| `datafog-python` | LLM 特化 PII redaction | Presidio の代替バックエンド | ★★ |
| `piiranha-detector` | 軽量 PII 検出 | 軽量プロファイル（NANO）向け | ★ |
| `obsei` | テキスト分析 + PII | 統合候補 | ★ |

## E-3: 観測性デファクト（既存 Metrics/Tracer と相互運用）

| ライブラリ | 主用途 | 統合方針 | 優先 |
|-----------|------|---------|------|
| `prometheus_client`（公式） | Prometheus exporter | `IndustrialMetrics.export_to_prometheus_client()` メソッド追加 | ★★★ |
| `opentelemetry-api/sdk` | OTel 公式 | `IndustrialTracer.span()` を OTel ContextAPI と相互変換 | ★★★ |
| `opentelemetry-exporter-otlp` | OTLP 送信 | `IndustrialTracer.export_otlp_payload()` の実HTTP送信版 | ★★ |
| `statsd-py` | StatsD プロトコル | optional exporter として実装 | ★ |
| `datadog`（公式） | DataDog SaaS | optional exporter | ★ |

## E-4: 時系列・分析データ連携

| ライブラリ | 主用途 | 統合方針 | 優先 |
|-----------|------|---------|------|
| `influxdb-client`（公式） | InfluxDB 2.x / 3.x | `InfluxDBSink` — DiagnosisResult を Line Protocol で書き込み | ★★★ |
| `psycopg2 + timescale` | TimescaleDB | `TimescaleSink` — ハイパーテーブル | ★★ |
| `victoriametrics-client` | VictoriaMetrics | optional exporter | ★ |
| `duckdb` | embedded 分析 | ローカル軽量 sink | ★★ |
| `apache-iceberg / pyiceberg` | データレイク | 大規模履歴用 | ★ |

## E-5: クラウド IoT サービス連携（オプトイン；オフライン LAN は既定）

| ライブラリ | 主用途 | 統合方針 | 優先 |
|-----------|------|---------|------|
| `azure-iot-device` | Azure IoT Hub | `AzureIoTSink`（明示的 opt-in） | ★★ |
| `awsiotsdk` | AWS IoT Core | `AwsIoTSink` | ★★ |
| `google-cloud-iot`（v2 retired） | GCP IoT Core 後継 | `GCPPubSubSink` 経由で実装 | ★ |
| `thingsboard-gateway` | ThingsBoard 統合 | アダプター対応 | ★ |

> **注意**: クラウド連携は LLMesh の「ローカル LLM・クラウドゼロ」原則と相反するため、
> **明示的 opt-in + プライバシーパイプライン強制通過** を必須要件とする。

## E-6: 画像処理ライブラリ（Volume D-2 強化）

| ライブラリ | 主用途 | 統合方針 | 優先 |
|-----------|------|---------|------|
| `opencv-python` | 画像処理デファクト | `ImageProcessor` バックエンド | ★★★ |
| `Pillow / Pillow-SIMD` | PIL（既使用） | EXIF 除去 + フォーマット変換 | ★★★ |
| `scikit-image` | 学術系画像処理 | optional 高度処理 | ★★ |
| `imageio` | 多形式 I/O | 動画/画像読み込み | ★★ |
| `imutils` | OpenCV ヘルパ | 補助ユーティリティ | ★ |

## E-7: ML/AI 推論バックエンド

| ライブラリ | 主用途 | 統合方針 | 優先 |
|-----------|------|---------|------|
| `onnxruntime`（公式） | ONNX 推論（CPU/GPU） | `OnnxRuntimeBackend` — Ollama/LlamaCpp と同列 | ★★★ |
| `llama-cpp-python` | GGUF 推論（既使用） | 既存 LlamaCppBackend 維持 | ★★★ |
| `ctranslate2` | 高速 LLM 推論 | `CTranslate2Backend`（オプション） | ★★ |
| `tensorrt-cu12` | NVIDIA GPU 最適化 | optional GPU バックエンド | ★ |
| `tflite-runtime` | 軽量推論 | エッジデバイス向け | ★★ |
| `mlc-llm` | Apple Silicon / WebGPU | エッジ推論 | ★ |

## E-8: LLM フレームワーク連携（既存設計を尊重）

| ライブラリ | 主用途 | 統合方針 | 優先 |
|-----------|------|---------|------|
| `langchain` | チェーン構築 | `LLMeshLangChainAdapter` — LLMesh ツールを LangChain 互換に | ★★ |
| `llamaindex` | RAG | `LLMeshLlamaIndexAdapter` | ★★ |
| `haystack-ai` | RAG パイプライン | optional 統合 | ★ |
| `mcp`（公式） | Model Context Protocol（既使用） | 既存 MCP stdio 維持 | ★★★ |
| `pydantic` | データ validation | スキーマ検証強化 | ★★ |
| `instructor` | 構造化 LLM 出力 | 任意 | ★ |

## E-9: メッセージングミドルウェア

| ライブラリ | 主用途 | 統合方針 | 優先 |
|-----------|------|---------|------|
| `aiokafka` | Kafka | `KafkaAdapter` — トピック → SensorEvent | ★★ |
| `pika` | RabbitMQ AMQP 0.9 | `RabbitMQAdapter` | ★★ |
| `aio-pika` | RabbitMQ async | aiokafka と同列 | ★★ |
| `nats-py` | NATS | `NATSAdapter` | ★ |
| `redis` | Redis Streams | `RedisStreamAdapter` | ★ |
| `pulsar-client` | Apache Pulsar | optional | ★ |

## E-10: 異常検知 / 予知保全（既存 MTEngine + SPC を補完）

| ライブラリ | 主用途 | 統合方針 | 優先 |
|-----------|------|---------|------|
| `pyod` | 異常検知 30+ 手法 | `PyODAnalyzer` — IndustrialPipeline のアナライザ | ★★★ |
| `prophet`（Meta） | 時系列予測 | `ProphetAnalyzer` — 季節性検知 | ★★ |
| `river` | オンライン学習 | `RiverAnalyzer` — ストリーム適応 | ★★ |
| `skforecast` | 多変量予測 | optional | ★ |
| `tsfresh` | 時系列特徴抽出 | MT 法の前処理として | ★★ |
| `merlion`（Salesforce） | 予測+異常検知統合 | optional | ★ |
| `kats`（Meta） | 時系列ツールキット | optional | ★ |

## E-11: ロボティクス・3D 拡張

| ライブラリ | 主用途 | 統合方針 | 優先 |
|-----------|------|---------|------|
| `rclpy`（既使用） | ROS 2 | 既存 ROS2Adapter 維持 | ★★★ |
| `open3d` | 3D 点群処理 | `PointCloud` Plus バックエンド（D-6.2） | ★★ |
| `trimesh` | メッシュ処理 | `MeshAdapter`（D-6.1） | ★★ |
| `pybullet` | 物理シミュレーション | デジタルツイン用 | ★ |
| `pinocchio` | ロボット動力学 | optional | ★ |

## E-12: セキュリティ補強

| ライブラリ | 主用途 | 統合方針 | 優先 |
|-----------|------|---------|------|
| `cryptography`（既使用） | 暗号 | 維持 | ★★★ |
| `pynacl` | libsodium | Ed25519/X25519 の代替実装 | ★ |
| `python-jose` / `pyjwt` | JWT | テナント認証トークン | ★★ |
| `authlib` | OAuth/OIDC | エンタープライズ SSO 連携 | ★★ |
| `argon2-cffi` | パスワードハッシュ | テナント認証 | ★ |

## E-13: 設定・データ・I/O

| ライブラリ | 主用途 | 統合方針 | 優先 |
|-----------|------|---------|------|
| `tomllib`（stdlib） | 既使用 | 維持 | ★★★ |
| `pydantic-settings` | 環境変数設定 | TomlConfig の代替/補完 | ★★ |
| `dynaconf` | マルチソース設定 | optional | ★ |
| `polars` | 高速 DataFrame | 既存 numpy/scipy 補強 | ★★ |
| `duckdb` | embedded SQL | 設定+履歴クエリ | ★★ |

## Volume E 統合戦略

1. **薄いアダプター層**: 各人気ライブラリは optional dependency として統合し、
   既存 LLMesh 抽象（SensorEvent / IndustrialAdapter / DiagnosisResult）に
   ラップするだけのアダプターを提供する。
2. **置き換え不可**: 既存の純 stdlib 実装（IndustrialMetrics / IndustrialTracer）は
   維持し、人気ライブラリは **追加バックエンド** として並列に提供する。
3. **互換性テスト**: 各統合に対して `tests/integration/` 以下に
   実際のライブラリで動作するスモーク テストを配置（CI で gating）。
4. **ライセンス監視**: 各 optional 依存の OSS ライセンスを README に明記。
   GPL/AGPL は extras から除外。

## Volume E 実装優先キュー（v3 後半 → v4）

| 優先度 | 項目 | 期待効果 |
|--------|------|---------|
| ★★★ | E-7.1 ONNX Runtime | エッジ AI 推論性能 +50% |
| ★★★ | E-2.1 Presidio 統合 | PII 検出精度 +30% |
| ★★★ | E-3.1 prometheus_client 互換 | 既存 Prometheus 環境への即時導入 |
| ★★★ | E-1.1 Siemens S7 / E-1.2 Allen-Bradley | 工場現場の最大 PLC ベンダ 2 社対応 |
| ★★ | E-9.1 Kafka / E-9.2 RabbitMQ | エンタープライズメッセージング |
| ★★ | E-10.1 PyOD | 異常検知の選択肢 +30 種 |
| ★★ | E-4.1 InfluxDB | 時系列ストレージのデファクト |
| ★ | クラウド連携・LangChain・量子 | 段階展開 |

---

# Volume F: LLM 機能拡張要件（2026-05-07 策定）

産業ローカル LLM の性能・運用品質を向上させるための機能群。

## F-1: RAG（Retrieval-Augmented Generation）

- **F-1.1** ローカルベクトル DB 統合（chromadb / qdrant / lance）
- **F-1.2** 設備マニュアル・SOP の自動取り込み（PDF / DOCX / TXT）
- **F-1.3** 過去 DiagnosisResult の時系列インデックス（incident knowledge base）
- **F-1.4** ハイブリッド検索（BM25 + dense embedding）
- **F-1.5** プライバシーパイプラインを通った要約のみインデックス化（生データ非保存）

## F-2: 推論バックエンド多様化（C-14 / E-7 拡張）

- **F-2.1** ONNX Runtime（CPU/CUDA/DirectML/CoreML）
- **F-2.2** llama.cpp の Continuous Batching 対応
- **F-2.3** vLLM 互換エンドポイント（OpenAI API シム）
- **F-2.4** 量子化モデル管理（GGUF Q4_K_M / Q5_K_M / Q8_0 自動選択）
- **F-2.5** モデル熱交換（A/B 切り替え 0 ダウンタイム）

## F-3: プロンプト管理

- **F-3.1** `PromptTemplate` 抽象 — Jinja2 ベース、安全モード強制
- **F-3.2** プロンプトバージョニング（git-blob 風 SHA-256 ID）
- **F-3.3** A/B テスト基盤 — テンプレ別の DiagnosisResult 比較
- **F-3.4** プロンプトインジェクション検知（既存 PromptFirewall 強化）

## F-4: LLM 出力評価

- **F-4.1** `OutputEvaluator` — 構造化出力検証（JSON Schema, Pydantic）
- **F-4.2** factuality 検証 — RAG コンテキストとの整合性スコア
- **F-4.3** 安全性スコア（toxic / unsafe / PII redaction 確認）
- **F-4.4** 自己一貫性（temperature 0 vs n>1 の合議）
- **F-4.5** ベンチマーク `eval_bench/` — golden set + scoring

## F-5: ファインチューニング基盤（オプトイン）

- **F-5.1** LoRA / QLoRA 継続学習（`peft` ライブラリ）
- **F-5.2** ローカル工場固有用語の専門化
- **F-5.3** 学習データの個人情報除去（Presidio 強制通過）
- **F-5.4** 学習履歴の audit chain 記録

## F-6: クラウド / ホステッド LLM 統合（2026-05-09 追加 — v3.1+）

LLMesh は当初「Secure Local LLM Swarm」として位置づけられたが、運用
現場では **ローカル LLM とクラウド LLM の混合構成** が要件となる
ケースが多い（オンプレ機密処理 + 高度推論はクラウド、回線断時の
フォールバックなど）。`LLMBackend` ABC を維持したまま、主要クラウド
LLM プロバイダを公式サポート対象にする。

### F-6.1 OpenAI 互換クラウドバックエンド（v3.1.0 実装）

- **F-6.1.1** `OpenAICompatibleBackend` — OpenAI v1 chat-completions
  API に準拠する任意プロバイダを単一バックエンドで吸収
- **F-6.1.2** プロバイダ別 factory 関数:
  - `openai_backend()` — OpenAI 公式
  - `azure_openai_backend(resource, deployment)` — Azure OpenAI
  - `openrouter_backend()` — OpenRouter（マルチモデル proxy）
  - `groq_backend()` — Groq 高速推論
  - `together_backend()` — Together AI
  - `deepseek_backend()` — DeepSeek
  - `mistral_backend()` — Mistral AI
- **F-6.1.3** 認証方式の柔軟性: Bearer / `api-key` ヘッダ両対応
  （Azure は `api-key`、その他は `Authorization: Bearer`）
- **F-6.1.4** API キーは環境変数（`OPENAI_API_KEY` /
  `AZURE_OPENAI_API_KEY` / `GROQ_API_KEY` 等）または引数で受領、
  ログには出力しない
- **F-6.1.5** `response_format={"type":"json_object"}` 対応
  プロバイダで JSON モード指定可能（OpenAI / Azure / Groq）

### F-6.2 Anthropic Messages API ネイティブ（v3.1.0 実装）

- **F-6.2.1** `AnthropicBackend` — Anthropic Messages API 専用
  （request shape / response 構造が OpenAI と異なるため別クラス）
- **F-6.2.2** モデル指定: `claude-haiku-4-5` / `claude-sonnet-4-6` /
  `claude-opus-4-7` 他
- **F-6.2.3** 認証: `x-api-key` + `anthropic-version` ヘッダ
- **F-6.2.4** レスポンスは `content` 配列の最初の `text` ブロックを抽出

### F-6.3 セキュリティ不変条件

- **F-6.3.1** すべてのクラウド呼び出しは
  `llmesh.security.http_limits.read_capped` 経由でレスポンスサイズを
  上限化（既定 16 MiB、`max_response_bytes` で上書き可能）
- **F-6.3.2** SSRF 対策: `EndpointValidator` でプロバイダ endpoint を
  許可リスト方式で検証可能（既存基盤）
- **F-6.3.3** プロンプト送信前に `PromptFirewall.classify` を必ず通す
  （L4 はクラウドへ届く前に BLOCK）
- **F-6.3.4** 監査: クラウド呼び出しは `firewall_decision` /
  `llm_invoke` イベントとして `AuditTrace` に記録、プロンプト本文は
  SHA-256 のみ保管
- **F-6.3.5** タイムアウト / リトライ / circuit breaker は既存の
  `AdapterCircuitBreakerRegistry` 互換

### F-6.4 プロバイダ追加プロセス（拡張 API）

新プロバイダ対応は `OpenAICompatibleBackend` の base_url + auth
header を上書きするだけで完結。ユーザー定義の独自バックエンドは
`LLMBackend` ABC を継承（CONTRIBUTING.md の "新規モジュール追加"
に従う）。

### F-6.5 受入基準

- 7 プロバイダ（OpenAI / Azure / OpenRouter / Groq / Together /
  DeepSeek / Mistral）+ Anthropic ネイティブで認証ヘッダ + URL 構築
  が正しく動作（mock テスト 43 件全 PASS）
- 既存 `OllamaBackend` / `LlamaCppBackend` との完全な互換性
  （`LLMBackend` ABC 経由で透過置換可能）
- Privacy pipeline / OutputValidator / 監査 / circuit breaker と全て
  互換

### F-6.6 Optional extras / 依存

- 追加依存ゼロ（urllib stdlib のみ） — wheel サイズに影響なし
- API キー管理は環境変数経由（pyproject.toml に新 extras 不要）

## Volume F 優先順位（v3.1+ 反映版）

| 優先 | 項目 | 理由 |
|------|------|------|
| ✓ 完了 | F-1.1〜F-1.3 RAG（numpy/sqlite/LSH 3 系統）| v2.13–v2.15 で完了 |
| ✓ 完了 | **F-6.1 / F-6.2 クラウド LLM 統合（OpenAI 互換 + Anthropic）** | **v3.1.0 で完了 — オンプレ + クラウド混成運用** |
| ★★★ | F-3 プロンプト管理 | A/B 改善の基盤 |
| ★★ | F-2.4 量子化管理 | エッジデバイスでの即効性能向上 |
| ★★ | F-4.1〜F-4.3 出力評価 | 本番運用での品質保証 |
| ★ | F-5 ファインチューニング | 専門化フェーズ |

---

# Volume G: 開発者体験（DevEx）要件（2026-05-07 策定）

LLMesh の運用・拡張を容易にする開発者向け機能。

## G-1: CLI 強化

- **G-1.1** `llmesh status` — 全アダプター・パイプライン状態の即時確認
- **G-1.2** `llmesh tail` — リアルタイムイベントストリーム表示
- **G-1.3** `llmesh diagnose <sensor>` — 単発診断
- **G-1.4** `llmesh replay <jsonl>` — 過去イベントログのリプレイ
- **G-1.5** `llmesh export <format>` — メトリクス/トレース/監査の一括出力
- **G-1.6** インタラクティブ TUI（textual / rich-based）

## G-2: Web ダッシュボード（オプション）

- **G-2.1** `llmesh dashboard` — 単一バイナリ HTTP UI（FastAPI ベース）
- **G-2.2** リアルタイムイベント表示（SSE）
- **G-2.3** 診断結果グラフ（Plotly / D3）
- **G-2.4** Prometheus メトリクスのインライン可視化
- **G-2.5** トレース span ビューア（OTLP 互換）
- **G-2.6** RBAC（テナント別・ロール別アクセス）

## G-3: 設定管理 UX

- **G-3.1** `llmesh configure --interactive` 拡張（設定ウィザード）
- **G-3.2** スキーマ駆動の TOML 検証（json-schema → toml lint）
- **G-3.3** 設定差分表示（`llmesh config diff`）
- **G-3.4** 設定テンプレート集（factory / smt / robotics / medical）
- **G-3.5** 環境変数 → TOML マイグレーション支援

## G-4: デバッグ・トラブルシュート

- **G-4.1** `llmesh doctor` — 環境健全性チェック（依存・ポート・権限）
- **G-4.2** 構造化ログ（JSON Lines）でのフィルタリング支援
- **G-4.3** イベントタイムライン可視化（asciinema 互換）
- **G-4.4** ペアプログラミングモード（LLM が SOP を読み上げ操作支援）

## G-5: プロビジョニング

- **G-5.1** Docker イメージ公式提供（multi-arch: amd64/arm64）
- **G-5.2** Helm chart（Kubernetes デプロイ）
- **G-5.3** systemd unit テンプレート
- **G-5.4** Ansible ロール
- **G-5.5** Terraform モジュール（クラウド IoT extras 用）

## Volume G 優先順位

| 優先 | 項目 | 理由 |
|------|------|------|
| ★★★ | G-1 CLI 強化 | 即時運用効率向上 |
| ★★★ | G-4.1 doctor | サポート負荷削減 |
| ★★ | G-2 Web UI | エンタープライズ採用の鍵 |
| ★★ | G-5.1 Docker | デプロイ標準 |
| ★ | G-5.2 Helm / G-5.5 Terraform | 大規模展開 |

---

# Volume H: 国際化・コンプライアンス要件（2026-05-07 策定）

## H-1: 国際化（i18n）

- **H-1.1** UI / メッセージの多言語対応（gettext）
- **H-1.2** SpatialSummarizer の出力言語切り替え（ja / en / zh / de）
- **H-1.3** タイムゾーン対応（IANA tz）
- **H-1.4** 単位系切り替え（SI / Imperial）
- **H-1.5** RTL（right-to-left）言語対応（dashboard）

## H-2: 規制・標準対応

- **H-2.1** **GDPR**（EU 一般データ保護規則）
  - データ主体の権利（アクセス / 削除 / 移植）の API
  - 監査チェーンに「処理の正当な根拠」記録
- **H-2.2** **CCPA**（米国カリフォルニア州プライバシー法）
- **H-2.3** **EU AI Act**（高リスク AI 規制）
  - リスク分類（minimal / limited / high / unacceptable）の宣言機能
  - 透明性義務: モデル / データセット / 評価の文書化テンプレ
- **H-2.4** **ISO/IEC 42001**（AI マネジメントシステム）
  - リスク評価ワークフロー
  - インシデント記録
- **H-2.5** **ISO/IEC 27001**（情報セキュリティ）
  - 監査チェーン + 監査ログ統合
- **H-2.6** **NIS2 指令**（EU 重要インフラサイバーセキュリティ）
- **H-2.7** **HIPAA**（医療、Volume D-7 と統合）
- **H-2.8** **SOC 2 Type II**（クラウド運用基準、ただし LAN 運用が主）
- **H-2.9** **IEC 62443**（産業制御システムセキュリティ）

## H-3: 認証・監査

- **H-3.1** 監査ログのエクスポート形式（CEF / LEEF / Syslog RFC 5424）
- **H-3.2** SIEM 連携（Splunk HEC / Elastic / OpenSearch）
- **H-3.3** SBOM 自動生成（CycloneDX / SPDX）
- **H-3.4** 脆弱性スキャン CI 統合（OSV / Snyk / Trivy）

## H-4: ライセンス管理

- **H-4.1** 依存ライブラリのライセンス自動検出（pip-licenses / scancode）
- **H-4.2** GPL / AGPL の混入防止 CI ゲート
- **H-4.3** 配布物の NOTICE 自動生成

## Volume H 優先順位

| 優先 | 項目 | 理由 |
|------|------|------|
| ★★★ | H-2.3 EU AI Act | 2026 施行、エンタープライズ必須 |
| ★★★ | H-3.3 SBOM | サプライチェーン規制対応 |
| ★★★ | H-4.1〜4.3 ライセンス | OSS 配布に必須 |
| ★★ | H-2.1 GDPR / H-2.2 CCPA | グローバル展開時に必須 |
| ★★ | H-2.4 ISO 42001 | エンタープライズ調達要件 |
| ★ | H-1 i18n | 段階展開 |

---

# Volume I: スケーラビリティ・分散要件（2026-05-07 策定）

LLMesh を単一ノードから複数ノードクラスターへ拡張する。

## I-1: 水平スケーリング

- **I-1.1** 複数ノード間の SensorEvent シャーディング（device_id ハッシュ）
- **I-1.2** リーダー選出（Raft / 軽量 paxos / etcd 連携）
- **I-1.3** ヘルスチェック + 自動フェイルオーバー
- **I-1.4** ステートレス前段 + ステートフル後段の分離
- **I-1.5** バックプレッシャー（生産者過負荷時のフロー制御）

## I-2: 永続化レイヤ

- **I-2.1** SQLite WAL → PostgreSQL / TimescaleDB へのマイグレーションパス
- **I-2.2** Audit chain の分散ハッシュ集約（Merkle tree）
- **I-2.3** Outbox の再送がクラスタ全体で重複しない仕組み（idempotency keys）

## I-3: 分散 LLM 推論

- **I-3.1** 推論ノードの専門化（GPU 専用ワーカー）
- **I-3.2** モデル分割（layer-wise / pipeline parallel）
- **I-3.3** 推論キャッシュの共有（Redis / Memcached）
- **I-3.4** リクエスト負荷分散（least-latency / round-robin / weighted）

## I-4: 高可用性（HA）

- **I-4.1** Active-Active / Active-Passive 切替
- **I-4.2** ホットスワップ可能な設定リロード
- **I-4.3** ローリングアップデート（K8s Deployment 互換）

## Volume I 優先順位

| 優先 | 項目 | 理由 |
|------|------|------|
| ★★★ | I-1.1 シャーディング | スケール限界突破 |
| ★★★ | I-1.5 バックプレッシャー | 安定運用必須 |
| ★★ | I-2.1 PostgreSQL マイグレーション | 大規模運用 |
| ★★ | I-3.1 推論ノード分離 | GPU 効率化 |
| ★ | I-4 HA | 大型導入時 |

---

# Volume J: マイグレーション・相互運用要件（2026-05-07 策定）

## J-1: 旧バージョン移行

- **J-1.1** バージョン間 schema migration ツール（alembic 風）
- **J-1.2** 設定ファイル自動アップグレード（v1 → v2 → v3）
- **J-1.3** 監査チェーンの後方互換性保証
- **J-1.4** Deprecated API の段階的削除（warning → error → 削除）

## J-2: 他フレームワークからのインポート

- **J-2.1** Node-RED フローの import（JSON）
- **J-2.2** Apache NiFi テンプレートの import
- **J-2.3** Home Assistant Automation の import
- **J-2.4** ThingsBoard デバイスプロファイルの import

## J-3: エクスポート

- **J-3.1** OpenAPI 3.1 スペック自動生成
- **J-3.2** AsyncAPI（イベントストリーム仕様）
- **J-3.3** Mermaid / PlantUML 形式のアーキテクチャ図自動生成

## J-4: クロスシステム連携

- **J-4.1** GraphQL gateway（オプション）
- **J-4.2** gRPC 双方向ストリーム
- **J-4.3** WebSocket イベント配信
- **J-4.4** Webhooks（Slack / Teams / Discord）

## Volume J 優先順位

| 優先 | 項目 | 理由 |
|------|------|------|
| ★★★ | J-1.1〜1.3 移行 | LTS 運用必須 |
| ★★ | J-3.1 OpenAPI | API ドキュメント自動化 |
| ★★ | J-4.4 Webhooks | 運用通知の標準 |
| ★ | J-2 他 FW import | 移行プロジェクト時 |

---

# 全 Volume 横断サマリー

| Volume | 章数 | 主要分野 | 状態 |
|--------|----:|---------|------|
| A | 6 | 基本機能（HTTP/MCP/署名/監査） | 完了 |
| B | 6 | 運用堅牢化（NonceStore/Outbox/Routing） | 完了 |
| C | 15 | 広範囲適用（医療/車載/航空/金融/LPWA 等） | 計画 |
| D | 9 | 画像処理拡張 | 計画 |
| E | 13 | PyPI/GitHub 人気ライブラリ統合 | 計画 |
| **F** | 5 | **LLM 機能拡張（RAG/評価/FT）** | **計画** |
| **G** | 5 | **DevEx（CLI/UI/Doctor/Provision）** | **計画** |
| **H** | 4 | **i18n・コンプライアンス（GDPR/AI Act）** | **計画** |
| **I** | 4 | **スケーラビリティ・分散** | **計画** |
| **J** | 4 | **マイグレーション・相互運用** | **計画** |

**合計 71 章 / 250+ 個別要件項目** — エンタープライズ製品に必要な
ほぼ全領域を網羅した状態。今後はこれらを優先度順に v3 → v4 → v5 で
段階実装する。

---

# Volume K: 重要インフラ・公共インフラ要件（2026-05-07 策定）

LLMesh を **国家・自治体・民間重要インフラ** の監視・制御・診断に
適用するための分野別要件。各分野は独自プロトコル + 規制を持つため、
専用アダプター + プライバシー / 安全要件を明確化する。

## K-1: 電力インフラ

### K-1.1 送配電
- **DNP3 Adapter**（Distributed Network Protocol 3.0 — 米国/豪州 SCADA デファクト）
- **IEC 60870-5-101 / -104 Adapter**（欧州 SCADA、TCP/IP）
- **IEC 61850 GOOSE / MMS Adapter**（変電所自動化、E-1 と統合）
- **Modbus over IP**（既存 ModbusAdapter で対応）

### K-1.2 再生可能エネルギー
- **SunSpec Modbus Adapter**（太陽光インバータ標準モデル）
- **OpenADR 3.0 Adapter**（需給調整、Demand Response）
- **MESA-ESS Adapter**（蓄電池 / Energy Storage System）
- 風力タービン IEC 61400-25（風力標準）

### K-1.3 スマートメーター
- **DLMS / COSEM Adapter**（電力量計、IEC 62056、E-1.6 から昇格）
- **Wireless M-Bus**（OMS, ガス/水道/熱量計の欧州標準）
- **ANSI C12.18 / .22**（北米メーター）

## K-2: 水道インフラ（上下水）

- **MQTT-SN over LoRa**（圧力センサー / 流量計）
- **HART Adapter**（産業用センサー業界標準、4-20mA + デジタル）
- **WaterML 2.0**（水文データ国際標準）
- **EPANET 連携**（水道網シミュレーションとの統合）
- 漏水音響センサー → SpatialSummarizer 拡張

## K-3: ガス・石油インフラ

- **Modbus over Serial**（既存）
- **PROFIBUS / PROFINET Adapter**（欧州工業）
- **API 21.1 Standard**（流量計算、石油ガス米国）
- メタンセンサー（IR / TDLAS）→ SensorEvent

## K-4: 鉄道インフラ

- **CBTC**（Communications-Based Train Control）統合
- **EuroBalise Telegram** デコード
- **CENELEC EN 50128 / EN 50129** 安全規格対応
- 軌道検測車（DVS イベントカメラ応用、Paper P4 と連動）
- 列車運行管理 PIS（Passenger Information System）

## K-5: 道路交通インフラ

- **NTCIP 1202**（信号機制御、米国）
- **NEMA TS-2**（信号機ハードウェア、米国）
- **UTMC**（英国都市交通制御）
- **ETC 5.8 GHz DSRC** ログ受信
- **C-V2X / V2X**（車載 → 路側）
- 車両感知器（Loop / Magnetic / Radar / LiDAR）

## K-6: 空港インフラ

- **AODB**（Airport Operational Database）連携
- **AFTN / AMHS**（航空通信）
- **A-CDM**（Airport Collaborative Decision Making）
- **METAR / TAF / SIGMET**（気象、Volume C-8 と統合）
- 滑走路監視カメラ（DVS 高速移動体検知）
- BHS（Baggage Handling System）統合

## K-7: 港湾インフラ

- **AIS**（Automatic Identification System、船舶位置）
- **VDES / VDE-SAT**（次世代 AIS）
- **CAN J1939**（コンテナクレーン制御）
- **OPC-UA + ISA-95**（コンテナ管理、既存 OPCUAAdapter）
- 自動化コンテナターミナル（ASC, AGV）連携

## K-8: 通信網インフラ

- **TR-069 / USP**（CPE 管理、家庭用ルーター）
- **NETCONF / RESTCONF / YANG**（IETF ネットワーク管理）
- **OpenConfig**（ベンダーニュートラル）
- **gNMI / gNOI**（Google 由来、Streaming テレメトリ）
- **TIP / O-RAN**（オープン無線アクセス網）
- 光ネットワーク TL1 / SONET / OTN
- **IPFIX / NetFlow / sFlow**（フロー解析）

## K-9: データセンターインフラ

- **IPMI / Redfish Adapter**（サーバー管理）
- **PDU 監視**（Schneider / Vertiv / Tripp Lite）
- **HVAC 管理**（DCIM 統合）
- **UPS 監視**（Modbus / SNMP / RFC 1628）
- 温度マッピング（複数センサー → 3D ヒートマップ → SpatialSummarizer）
- 電力使用効率（PUE）リアルタイム算出

## K-10: ビル管理（BMS / BAS）

- **BACnet/IP & BACnet/MS-TP Adapter**（ビル管理デファクト、ASHRAE 135）
- **KNX/IP Adapter**（欧州ビル管理）
- **LonWorks Adapter**（ANSI/CEA 709.1）
- **Modbus**（既存）
- **DALI**（照明、IEC 62386）
- **OpenADR 連携**（K-1.2 と統合）
- 非常用設備（防災 / 防犯）統合

## K-11: スマートホーム

- **Matter Adapter**（CHIP, Apple/Google/Amazon 三社合意）
- **Zigbee 3.0 Adapter**（zigpy 経由）
- **Z-Wave Adapter**（python-openzwave）
- **Thread Adapter**（ot-br-posix）
- **Bluetooth LE GATT Adapter**
- 家電 ECHONET Lite（日本）

## K-12: 環境計測

- **大気汚染**: PM2.5 / NOx / SOx / CO / O3（電気化学・光散乱）
- **水質**: pH / DO / 濁度 / 電気伝導度
- **放射線**: ガイガー計数管 / シンチレーター
- **騒音**: 24 時間等価騒音レベル LAeq
- **振動**: 地震動 SI 値（Spectrum Intensity）

## K-13: 廃棄物処理インフラ

- 焼却炉（温度 / O2 / NOx / CO）
- バイオガス（メタン濃度 / pH / 温度）
- 埋立地メタン回収
- 浸出水管理

## K-14: 医療施設インフラ

- **HL7 v2 / FHIR Adapter**（C-1 と統合）
- **DICOM C-STORE**（Volume D-7）
- **陰圧室 / 陽圧室監視**（ISO 14644 クリーンルーム）
- **医療ガス配管監視**（O2 / N2O / 圧縮空気）
- **薬剤管理**（コールドチェーン温度ログ）
- ICU バイタルモニタリング（HL7 + IHE PCD）

## K-15: 食品・農業インフラ

- **HACCP 温度ログ**（冷蔵 / 冷凍 / 加熱工程）
- **ISOBUS Adapter**（C-7、農業機械）
- **Smart Greenhouse**（温湿度 / CO2 / 日射量 / EC / pH）
- **コールドチェーン IoT**（流通追跡）

## K-16: 教育・研究インフラ

- **iCal / WebDAV**（時間割 / リソース予約）
- **Learning Tools Interoperability (LTI)** 統合
- 実験室機器（K-12 SCPI と統合）
- 研究データ DOI 自動付与（DataCite）

## K-17: 軍事・防衛（オプトイン専用）

> **注意**: LLMesh は研究 / 教育 / 民間用途を主対象とする。本セクションは
> 防衛技術コンプライアンス（米国 EAR / ITAR、日本 武器輸出三原則等）の
> 確認後にオプトインで利用すること。

- **Link 16 / TADIL-J** 一方向受信のみ
- **MIL-STD-1553** （C-3.2 と統合）
- **ARINC 429 / 818**（C-3.1 と統合）

## K-18: 災害対応・公共安全

- **CAP 1.2**（Common Alerting Protocol、世界共通警報）
- **緊急地震速報（EEW）受信**（気象庁 XML）
- **NL-Alert / EU-Alert / WEA**（広域携帯通報）
- 119 / 110 統合 CAD（Computer-Aided Dispatch）
- 防災行政無線（AVM, Pager 多重）

## K-19: 自治体スマートシティ統合

- **CityGML**（3D 都市モデル、Volume D-6 と統合）
- **OASIS WebAPI**（Smart Cities API）
- **FIWARE NGSI-LD**（欧州標準）
- **OGC SensorThings API**

## K-20: 横断要件 — 重要インフラ共通

- **K-20.1** **耐故障設計（Fail-operational）**: 1 ノード故障で機能停止しない
- **K-20.2** **時刻同期高精度化**: PTP IEEE 1588（既存 NTP より厳しい ±1 µs）
- **K-20.3** **規制対応**:
  - 米国 NERC CIP（電力）
  - EU NIS2（重要インフラ全般）
  - 日本 サイバーセキュリティ基本法
  - IEC 62443（産業制御システムセキュリティ、Volume H-2.9）
- **K-20.4** **可観測性義務**: 全イベントを最低 7 年間アーカイブ
- **K-20.5** **エアギャップ運用**: 外部ネットワーク完全遮断モード
- **K-20.6** **二重化**: アダプター・パイプライン・ストレージの冗長化

## Volume K 優先順位

| 優先 | 項目 | 理由 |
|------|------|------|
| ★★★ | K-10.1 BACnet | ビル管理デファクト、即応用可能 |
| ★★★ | K-1.1 DNP3 | 電力系統 SCADA、規制需要大 |
| ★★★ | K-9.1 Redfish / IPMI | DC 運用に直結 |
| ★★ | K-1.2 SunSpec / OpenADR | 再エネ普及に追随 |
| ★★ | K-2 HART | 産業センサー業界標準 |
| ★★ | K-11.1 Matter | スマートホーム新標準 |
| ★★ | K-18.1 CAP | 防災公共サービス |
| ★ | K-17 軍事 | オプトイン、適用先による |

---

# 全 Volume 横断サマリー（v2.4 計画）

| Volume | 章数 | 領域 | 状態 |
|--------|----:|------|------|
| A〜B | 12 | 基本機能 + 運用堅牢化 | 完了 |
| C | 15 | 広範囲適用 | 計画 |
| D | 9 | 画像処理拡張 | 計画 |
| E | 13 | PyPI/GitHub 人気ライブラリ統合 | 計画 |
| F | 5 | LLM 機能拡張 | 計画 |
| G | 5 | 開発者体験 | 計画 |
| H | 4 | i18n・コンプライアンス | 計画 |
| I | 4 | スケーラビリティ・分散 | 計画 |
| J | 4 | マイグレーション・相互運用 | 計画 |
| **K** | **20** | **重要インフラ・公共インフラ** | **計画** |

**合計 91 章 / 350+ 個別要件項目** — 産業 IoT から重要インフラ・
スマートシティまでをカバーする業界最大級の要件定義。

---

# Volume L: エッジ・RTOS 統合要件（2026-05-07 策定）

LLMesh はサーバーから組込み機器・RTOS まで連続的にスケールする
プラットフォームを目指す。本 Volume はマイコン・RTOS 上の
センサーデバイスを LLMesh ノードと統合する仕様を定義する。

## L-1: RTOS 動作モデル

LLMesh コア（Python）は **必ず Linux/Windows/macOS** で動作する。
RTOS 上では **薄い C クライアント** がセンサーデータを構築し、
TCP/UDP/SerialまたはMQTT-SN/CoAPで LLMesh ノードへ転送する：

```
+-----------------+        TCP/UDP/MQTT-SN         +------------------+
|  RTOS device    |  ───────────────────────────►  |  LLMesh Gateway  |
|  (TRON/Zephyr)  |     SensorEvent v1 wire        |  (Python + Rust) |
+-----------------+                                 +------------------+
```

## L-2: 対応 RTOS / 組込みプラットフォーム

| プラットフォーム | アーキ | LLMesh ロール | 想定用途 |
|----------------|------|-------------|---------|
| **μITRON / TOPPERS** | ARM Cortex-M, RX, RH850 | C client | 産業機械制御 |
| **T-Kernel 2.0** | ARM, MIPS | C client | 自動車 ECU |
| **Zephyr RTOS** | ARM, Xtensa, RISC-V | C client + MQTT-SN | IoT デバイス |
| **FreeRTOS** | Cortex-M / ESP32 | C client + MQTT/CoAP | 民生機器 |
| **NuttX** | ARM, RISC-V | C client | カメラ / ドローン |
| **Mbed OS** | Cortex-M | C client | プロトタイピング |
| **VxWorks** | x86, ARM, PPC | C client | 航空・防衛 |
| **QNX Neutrino** | x86, ARM | Python or C | 自動車インフォテイメント |
| **INTEGRITY** | x86, ARM, PPC | C client | 軍用・医療 |
| **eCos** | ARM | C client | 旧式組込み |
| **AUTOSAR Classic** | ARM | C client | 自動車 ECU |

## L-3: SensorEvent C ABI

C 言語向けの SensorEvent パッキング仕様（v1）：

```c
// llmesh_event.h
#include <stdint.h>

#define LLMESH_PROTOCOL_MODBUS 1
#define LLMESH_PROTOCOL_OPCUA  2
#define LLMESH_PROTOCOL_MQTT   3
// ... (defines mirror Python constants)

typedef enum {
    LLMESH_PRIORITY_NORMAL   = 0,
    LLMESH_PRIORITY_HIGH     = 1,
    LLMESH_PRIORITY_CRITICAL = 2,
} llmesh_priority_t;

typedef struct __attribute__((packed)) {
    uint32_t magic;          // 0x4C4D4553 ("LMES")
    uint16_t version;        // 1
    uint16_t protocol_id;
    uint64_t timestamp_ns;   // UNIX epoch ns
    uint32_t sensor_id_len;  // bytes
    uint32_t device_id_len;
    uint32_t sensor_type_len;
    uint32_t unit_len;
    uint32_t payload_len;
    uint8_t  priority;
    uint8_t  reserved[7];
    /* variable-length fields follow */
} llmesh_event_header_t;
```

## L-4: トランスポート

- **L-4.1** TCP/IP（Ethernet / Wi-Fi）— 一般的
- **L-4.2** UDP — 低オーバーヘッド・損失許容
- **L-4.3** MQTT-SN over UDP — 低消費電力 IoT
- **L-4.4** CoAP — RESTful 組込み
- **L-4.5** Serial（UART） — 産業マシン制御
- **L-4.6** CAN frame カプセル化（既存 CANAdapter で受信）

## L-5: 推論オフロード

RTOS デバイスで推論結果のみ送る運用：
- **L-5.1** TFLite Micro (≥256 KB Flash) — 異常検知前段
- **L-5.2** ESP-DL（ESP32 専用）
- **L-5.3** STM32Cube.AI（STM32 専用）
- **L-5.4** Helium MVE（Cortex-M55 / M85）

## L-6: 認証・セキュリティ

- **L-6.1** Pre-shared Key + Ed25519 軽量実装（micro-ECC）
- **L-6.2** TinyTLS / mbedTLS / wolfSSL のオプション
- **L-6.3** Trust Zone / Secure Enclave 連携

## L-7: 開発・ビルド

- **L-7.1** PlatformIO / Zephyr west 用ライブラリ
- **L-7.2** Arduino IDE 互換ヘッダ提供
- **L-7.3** ESP-IDF コンポーネント
- **L-7.4** AUTOSAR ARXML テンプレ

## L-8: 横断要件

- **L-8.1** **静的メモリ**: malloc 不使用モード
- **L-8.2** **デターミニスティック実行**: WCET 解析可能なコード
- **L-8.3** **割込みハンドラ非ブロッキング**
- **L-8.4** **コードサイズ ≤ 16 KB**（CoAP + SensorEvent core）

## Volume L 優先順位

| 優先 | 項目 | 理由 |
|------|------|------|
| ★★★ | L-3 C ABI ヘッダ | 全 RTOS 連携の基盤 |
| ★★★ | L-4.1〜4.4 トランスポート | 既存 LLMesh ゲートウェイで即受信可能 |
| ★★ | L-2 Zephyr / FreeRTOS | 対応 RTOS 上位 2 つ |
| ★★ | L-5.1 TFLite Micro | エッジ AI 連携 |
| ★ | L-2 μITRON / T-Kernel | 日本市場での需要 |
| ★ | L-7 ビルドシステム | 段階展開 |

---

# Volume M: 量子・先進計算統合要件（2026-05-07 策定、長期）

将来的な量子計算・神経形態計算プラットフォーム連携。

## M-1: 量子計算

- **M-1.1** Qiskit（IBM Quantum）統合
- **M-1.2** Cirq（Google）統合
- **M-1.3** PennyLane（Xanadu）統合
- **M-1.4** 量子鍵配送（QKD）プロトコル受信

## M-2: 神経形態（Neuromorphic）

- **M-2.1** Intel Loihi 2 連携（DVS と相性良）
- **M-2.2** SpiNNaker（マンチェスター大）
- **M-2.3** snnTorch / Norse（フレームワーク）

## M-3: 光計算 / 量子センサー

- **M-3.1** 光時計（光格子時計）データ受信
- **M-3.2** 量子重力計（地下空洞検知等）

## Volume M は v5+ の長期計画

実装は量子コンピューティングが商業化された時点で着手。

---

# 全 Volume 横断サマリー（v2.6 計画）

| Volume | 章数 | 領域 | 状態 |
|--------|----:|------|----|
| A〜B | 12 | 基本/堅牢化 | 実装済 |
| C | 15 | 広範囲適用 | 計画 |
| D | 9 | 画像処理拡張 | 計画 |
| E | 13 | PyPI 統合 | 計画 |
| F | 5 | LLM 機能 | 計画 |
| G | 5 | DevEx | 計画 |
| H | 4 | コンプライアンス | 計画 |
| I | 4 | スケーラビリティ | 計画 |
| J | 4 | マイグレーション | 計画 |
| K | 20 | 重要インフラ | 計画（K-10.1 BACnet 実装済） |
| **L** | **8** | **エッジ・RTOS** | **計画** |
| **M** | **3** | **量子・先進計算** | **長期計画** |

**合計 102 章 / 400+ 個別要件項目** — IT インフラから OT、組込み、
量子計算までを統一フレームワークでカバー。

---

# v3 Implementation Plan（2026-05-08 策定 — A 分類 3 テーマの実装計画）

> Volume N の A 分類 3 テーマ（N-7 / N-11 / N-15）を v3 ロードマップで実装する
> ための実装計画。要件定義書本文では「組合せ機会」として抽出されているが、
> ここでは **どの既存モジュールを再利用し、何を新規実装し、どこにセキュリティ
> 不変条件を貼るか** を要件レベルで詳述する。
>
> **方針**: 既存 LLMesh 基盤（MTEngine / XbarRChart / CUSUMChart /
> ImageFirewall / PrivacySummarizer / ProtocolAdapter）を最大限再利用し、
> 新規モジュールは **薄い接着レイヤー（Explainer / Aggregator）** に限定する。

## v3-N7: 説明可能 SCADA（N-7 由来）

**起点コーパス**: `infrastructure × statistics × multivariate_analysis × llm`
**対応 ROADMAP**: v3 minor（K-1.1 DNP3 と統合）

### 既存モジュール再利用
- `llmesh.industrial.mt_engine.MTEngine` — Mahalanobis 距離による異常スコア
- `llmesh.industrial.spc.cusum.CUSUMChart` — 累積和検出
- `llmesh.industrial.spc.xbar_r.XbarRChart` — 平均/範囲管理図
- `llmesh.privacy.prompt_firewall.PromptFirewall` — L0–L4 プライバシー振分

### 新規モジュール（v3-N7 で追加）
- **`llmesh.industrial.adapters.dnp3_adapter.DNP3Adapter`** — DNP3 over TCP/UDP
  - `pydnp3` ベース、SensorEvent 変換、Outstation 認証（鍵更新付き）
  - 受入: 5 件以上の DNP3 vendor 対応、replay protection 検証
- **`llmesh.industrial.spc.cusum_explained.ExplainedCUSUM`** — CUSUM + RCA hooks
  - 検出時に `tagged_window` を抽出、`LLMExplainer` に渡す
- **`llmesh.industrial.iec61850.GOOSEAdapter`** — IEC 61850 GOOSE（オプション）
  - 受入: pcap 既知データセットで MT 法 + 自然言語レポート生成

### Explainer 接着レイヤー
- **`llmesh.industrial.explainer.LLMExplainer`** — 異常 → 自然言語根本原因
  - 入力: `(alarm_event, mt_distance, contributing_dims, time_window)`
  - LLM 呼び出し前に PromptFirewall（L4 BLOCK／L3 サマリ）通過必須
  - 出力: 構造化レポート（Markdown）+ JSON（incident_id / severity / cause / suggestion）

### セキュリティ不変条件
- 全 SCADA データは **デフォルト L3 分類**（PrivacySummarizer 経由必須）
- DNP3/GOOSE 認証鍵は `llmesh/security/identity.py` の鍵管理に統一
- LLMExplainer に渡す `contributing_dims` のラベルは **PII フィルタ済み** であること
- インシデントレポートは **AuditTrace に署名付きで記録**

### テスト戦略 / 受入基準
- DNP3 vendor 5 種以上の `.pcap` 再生で SensorEvent 変換 PASS
- 既知の異常時系列（合成）で CUSUM + LLMExplainer が **Top-3 寄与次元** を当てる
- IEC 61850 GOOSE pcap 公開データセット（IEEE PSRC）で MT 距離一致を検証
- 全パスを通したエンドツーエンドテスト（property-based、hypothesis）

## v3-N11: µs 異常検知（DVS + MT、N-11 由来）

**起点コーパス**: `multivariate_analysis × image × industrial_iot`
**対応 ROADMAP**: v3 minor（既存 DVS Adapter と直結）

### 既存モジュール再利用
- `llmesh.industrial.adapters.dvs_adapter.DVSAdapter` — v1.7.0 既存
- `llmesh.industrial.mt_engine.MTEngine` — オフライン訓練 + 単位空間
- `llmesh_rust.event_codec` — Rust 拡張による DVS encode/decode（v2.5.0）

### 新規モジュール（v3-N11 で追加）
- **`llmesh.industrial.mt_online.OnlineMTEngine`** — ストリーム MT 推論
  - 入力: `(event_batch, unit_space)`（µs 粒度バッチ）
  - 出力: 同期 Mahalanobis 距離、フェイルクローズ
  - 受入: 1M evt/s で MD 計算 < 50µs P99（既存 Rust 拡張使用）
- **`llmesh.industrial.spc.hotelling_t2.HotellingT2Chart`** — 多変量管理図
  - DVS イベント密度マップを多変量入力に
  - 受入: 既知ベンチマーク（NASA Bearing, IMS）で false alarm rate ≤ 0.5%
- **`llmesh.industrial.aggregators.event_density_map.DensityMap`**
  - DVS イベントを空間グリッドに投影、Hotelling T² 入力ベクトルへ
  - 受入: 1M evt/s で grid 64×64 更新がリアルタイム成立

### セキュリティ不変条件
- DVS データは原則 L1（製造ライン）— 顔/個人映る場合は L4 → BLOCK
- `OnlineMTEngine` の `unit_space` は **per-device に分離**（v1.5.0 ルール準拠）
- バッチ処理メモリ上限: `LLMESH_MT_ONLINE_MAX_BATCH_BYTES`（デフォルト 16 MiB）

### テスト戦略 / 受入基準
- 合成 DVS データセット（高速回転体、振動）で µs オーダ異常検知の latency 計測
- NASA Bearing データセットで Hotelling T² の false alarm rate 検証
- メモリリークテスト（hypothesis + tracemalloc、1h 連続駆動）
- Rust 拡張なし環境で fallback パス（純 Python）も PASS

## v3-N15: 統計 × VLM × IoT（マルチモーダル品質管理、N-15 由来）

**起点コーパス**: `statistics × vllm × industrial_iot`
**対応 ROADMAP**: v3 minor（既存 ImageFirewall + Xbar-R と直結）

### 既存モジュール再利用
- `llmesh.privacy.image_firewall.ImageFirewall` — v1.2.0 既存
- `llmesh.privacy.image_summarizer.ImageSummarizer` — v1.2.0 既存
- `llmesh.industrial.spc.xbar_r.XbarRChart` — Xbar-R 管理図
- `llmesh.industrial.spc.cusum.CUSUMChart` — CUSUM

### 新規モジュール（v3-N15 で追加）
- **`llmesh.industrial.multimodal.vlm_feature_extractor.VLMFeatureExtractor`**
  - VLM（Vision-Language Model）出力の構造化（OCR 数値、欠陥カテゴリ）
  - 入力: 画像 → ImageFirewall → Vision LLM → 数値特徴ベクトル
  - 受入: AOI 公開データセット（DAGM, MVTec AD）で OCR/分類精度 ≥ 0.85
- **`llmesh.industrial.multimodal.unified_spc.UnifiedSPC`**
  - センサー時系列 + VLM テキスト特徴を統合した Xbar-R / CUSUM
  - スイッチ可能: `mode=concat | weighted | hierarchical`
  - 受入: 製造合成データで「画像のみ」「センサーのみ」より検出感度向上
- **`llmesh.industrial.multimodal.video_cusum.VideoCUSUM`**
  - 動画ストリーム + センサー時系列の同時 CUSUM（時刻同期）
  - 受入: drift シミュレーションで両系統に同時 alarm 発火

### セキュリティ不変条件
- VLM への入力画像は ImageFirewall を **必ず通過**（顔/書類は BLOCK）
- VLM 出力テキストも PromptFirewall（再パス）— 二段プライバシーゲート
- VLM ベンダーロックイン回避: `LLMESH_VLM_BACKEND` で ollama/llava 既定

### テスト戦略 / 受入基準
- DAGM / MVTec AD で VLMFeatureExtractor が指定精度を満たす
- 合成製造データで UnifiedSPC が単独 Xbar-R より検出力向上を有意水準で示す
- 動画 + センサーのドリフトシミュレーションで VideoCUSUM が同期 alarm 発火
- 2 段プライバシーゲート（ImageFirewall + PromptFirewall）の bypass テスト全 BLOCK

## 横断: v3 Implementation Plan の共通ガードレール

- **API 安定性**: v3.0.0 以降、上記 N-7 / N-11 / N-15 の公開 API は SemVer 準拠
- **依存方針**: 新規 PyPI 依存は optional extras（`pyproject.toml`）として追加
  - `industrial_n7 = ["pydnp3>=0.x"]`
  - `industrial_n11 = []`（既存 Rust 拡張のみで成立）
  - `industrial_n15 = ["Pillow>=10.0"]`（既に vision extras）
- **マイグレーション**: 既存 v2.x ユーザはオプトインで段階的に有効化可能
- **テスト**: property-based（hypothesis）+ 公開ベンチマークセットでの数値検証

---

# Volume N: 学際横断融合テーマ（Research Backlog／2026-05-07 策定 — Raptor RAD 駆動）

> **本 Volume の出自**: Raptor の RAD（22 分野コーパス、`C:/Users/puruy/raptor/.claude/skills/corpus/`）
> を `rad-research` スキルで横断検索し、**2 つ以上の分野が交差する未踏領域**
> として抽出した融合テーマ群。各テーマは複数 RAD 分野の論文に裏付けられた
> 「組合せ機会」を表現する。
>
> **位置づけ（2026-05-07 確定）**: Volume N の 15 テーマ全てを要件定義書には残すが、
> **実装ロードマップ（ROADMAP.md）に正式昇格するのは A 分類の 3 テーマのみ**:
> N-7 説明可能 SCADA / N-11 µs 異常検知（DVS+MT）/ N-15 統計 × VLM × IoT。
> その他のテーマは「Research Backlog」として保持し、外部条件が揃った時点で
> 個別判断で昇格する。

## N-1: Quantum × LLM × NN（量子強化 LLM）

**起点コーパス**: `quantum_computing` × `llm` × `neural_network`

- **N-1.1** 量子変分回路を Transformer 注意機構に組込む実験
- **N-1.2** 量子テンソルネットワーク（MPS / TT）による LLM 圧縮
- **N-1.3** 量子鍵配送（QKD）下のフェデレーション LLM 学習
- **N-1.4** 量子敵対的サンプリングによる LLM 安全性検証

## N-2: Edge AI × Security × Medical（医療エッジ AI）

**起点コーパス**: `mlops` × `security` × `medical`

- **N-2.1** HIPAA 準拠の Federated Learning（病院間 ECG 異常検知）
- **N-2.2** Edge LLM × DP（differential privacy）による個人化問診
- **N-2.3** 在宅医療 IoT × Presidio PII redaction × LLMesh PromptFirewall

## N-3: 自動運転 × LLM Agent（V2X 自然言語制御）

**起点コーパス**: `automotive` × `llm` × `agents` × `infrastructure`

- **N-3.1** CAN/V2X イベント → LLM Agent → 自然言語意思決定説明
- **N-3.2** ADAS 異常検知 + LLM による運転手向け説明生成
- **N-3.3** 路側 ITS との multi-agent 協調制御

## N-4: 産業センサー × Diffusion（生成的予知保全）

**起点コーパス**: `industrial_iot` × `diffusion` × `multivariate_analysis`

- **N-4.1** 拡散モデルによる正常時系列の生成 → 異常スコア
- **N-4.2** 振動・電流・温度の multimodal diffusion による故障シミュレーション
- **N-4.3** AOI 欠陥画像の合成データ生成（rare class 補完）

## N-5: ゲーム × LLM Agents × VLM（実況・NPC 進化）

**起点コーパス**: `game_dev` × `agents` × `llm` × `vllm`

- **N-5.1** eスポーツ実況 LLM（試合状態 → 自然言語解説）
- **N-5.2** NPC × multi-agent collaborative 発話システム
- **N-5.3** ゲーム画面 VLM 解析 → 動的バランス調整
- **N-5.4** 異常プレイ検知（cheat detection）× LLM による説明生成

## N-6: ロボティクス × 最適化 × 数値解析（運動制御の数理深化）

**起点コーパス**: `robotics` × `optimization` × `numerical_methods`

- **N-6.1** SDP/MPC + LLM による自然言語タスク → 数値最適化問題変換
- **N-6.2** Krylov 部分空間 × Diffusion Policy による高速モーション生成
- **N-6.3** 量子最適化（QAOA）× ロボット経路計画

## N-7: 重要インフラ × 統計 SPC × LLM（説明可能 SCADA）

**起点コーパス**: `infrastructure` × `statistics` × `multivariate_analysis` × `llm`

- **N-7.1** DNP3 ストリーム × CUSUM × LLM 異常根本原因説明
- **N-7.2** IEC 61850 GOOSE × MT 法 × 自然言語インシデントレポート
- **N-7.3** 電力負荷予測 × Prophet × LLM による調達意思決定支援

## N-8: 量子センサー × 情報理論 × 防災

**起点コーパス**: `quantum_computing` × `information_theory` × `infrastructure`

- **N-8.1** 量子重力計（地下空洞）× LLM レポート自動生成
- **N-8.2** QKD インフラ × 災害時通信プロトコル
- **N-8.3** 量子情報的プライバシー × 緊急速報の信頼経路

## N-9: 画像処理 × 数値解析 × 拡散モデル（次世代 ISP）

**起点コーパス**: `image` × `numerical_methods` × `diffusion`

- **N-9.1** 高速 SVD × diffusion による低光画像復元
- **N-9.2** Tensor 分解 × event camera (DVS) 高速復元
- **N-9.3** ControlNet × MT 法による合成欠陥画像の妥当性検証

## N-10: AI Agents × 重要インフラ × Compliance（自動監査）

**起点コーパス**: `agents` × `infrastructure` × `security`

- **N-10.1** EU AI Act 高リスク AI 自動分類エージェント
- **N-10.2** ISO/IEC 42001 監査プロセス自動化エージェント
- **N-10.3** SCADA インシデント応答エージェント（NERC CIP / NIS2）

## N-11: 多変量解析 × DVS × 予知（マイクロ秒精度の異常検知）

**起点コーパス**: `multivariate_analysis` × `image` × `industrial_iot`

- **N-11.1** DVS イベント時系列 × MT 法で µs オーダー異常検知
- **N-11.2** Hotelling T² × イベント密度マップ
- **N-11.3** 拡散コインシデンス × 高速生産ライン故障予測

## N-12: フェデレーション × 量子 × 医療（究極のプライバシー連携）

**起点コーパス**: `medical` × `quantum_computing` × `mlops`

- **N-12.1** 病院間 QKD ネットワーク上の Federated LLM 学習
- **N-12.2** 量子安全暗号 + Federated DICOM
- **N-12.3** 量子センサー × 多施設臨床試験

## N-13: ゲーム × 強化学習 × 産業デジタルツイン

**起点コーパス**: `game_dev` × `agents` × `industrial_iot`

- **N-13.1** Unity / Unreal による工場デジタルツイン × LLMesh
- **N-13.2** ゲームエンジン物理シム × 強化学習エージェントの工場ライン最適化
- **N-13.3** XR 操作インターフェース × 産業 IoT 制御

## N-14: 数値最適化 × LLM × Compliance（自動規制チェック）

**起点コーパス**: `optimization` × `llm` × `security`

- **N-14.1** 制約付き SAT/SMT × LLM 仕様書解析で規制準拠自動検証
- **N-14.2** Bayesian optimization × 動的セキュリティポリシーチューニング
- **N-14.3** プロンプト最適化 × 監査ログ生成

## N-15: 統計 × VLM × IoT（マルチモーダル品質管理）

**起点コーパス**: `statistics` × `vllm` × `industrial_iot`

- **N-15.1** Xbar-R + 画像 VLM 由来テキスト特徴の統合 SPC
- **N-15.2** OCR（VLM）× 統計マニュアル照合
- **N-15.3** 動画ストリーム + センサー時系列の同時 CUSUM

## Volume N — RAD 駆動の意義

各テーマは **Raptor RAD コーパスから自動抽出可能**な未踏領域を表現します：

```bash
# 各テーマの裏付け論文を即座に取得
python tools/bulk_corpus_collector.py --domain quantum --target 1000 \
    --queries "quantum machine learning transformer attention"
python tools/bulk_corpus_collector.py --domain medical --target 1000 \
    --queries "federated learning differential privacy ECG"
```

`triz-ideation` × `cross-domain-ideation` × `rad-research` の 3 スキル
連鎖により、Volume N の各テーマは **半自動的に研究計画書 / PoC 設計**
へ展開できます。

## Volume N 分類（2026-05-07 確定 — Research Backlog 区分）

| 分類 | 扱い | テーマ | 理由 |
|------|------|--------|------|
| **A 採用** | **v3 ROADMAP 正式昇格** | **N-7** 説明可能 SCADA | 既存 MT/SPC + CUSUM × LLM 直結、重要インフラ実装路線 |
| **A 採用** | **v3 ROADMAP 正式昇格** | **N-11** µs 異常検知（DVS+MT） | 既存 DVS Adapter + MT 法エンジンの自然な拡張 |
| **A 採用** | **v3 ROADMAP 正式昇格** | **N-15** 統計 × VLM × IoT | Xbar-R + VLM テキスト統合 SPC、既存基盤と直結 |
| B 条件付き | Research Backlog（外部条件成立で再評価） | N-2 医療エッジ AI / N-3 自動運転 LLM Agent | 規制・パートナー要件が揃えば昇格候補 |
| C 研究テーマ | Research Backlog（PoC 実装計画外） | N-4 / N-5 / N-9 / N-13 | 学術的価値はあるが実装コスパが現状不明 |
| D 組込価値薄 | Research Backlog（長期凍結） | N-1 / N-6 / N-8 / N-10 / N-12 / N-14 | 量子先進系・規制エージェント等、近未来の組込価値が薄い |

**運用ルール:** B/C/D 分類は要件定義書（本 Volume N）に残す。
ROADMAP.md の v3 残ロードマップには **A 分類 3 テーマのみ** を上げる。
昇格判断は Volume N 個別テーマの外部条件（規制、パートナー、データ取得性、
RAD コーパスからの新エビデンス）が揃った段階で個別に行う。

---

## 全 Volume 横断サマリー（v2.11 計画）

| Vol | 章 | 領域 | 状態 |
|----|---:|------|----|
| A〜M | 91 | 既存 + 計画 | 一部実装 |
| **N** | **15** | **学際横断融合（RAD 駆動）** | **Research Backlog（A 分類 3 のみ ROADMAP 昇格）** |

**合計 117 章 / 500+ 個別要件項目** — RAD コーパスを起点に、
**研究分野の組合せ可能性**を網羅したフレームワーク要件として完成。




