# LLMesh 対応プラットフォーム一覧

> **かみ砕いた説明（中学生レベル）**
> このページは「LLMesh がどの機械の上で動くか」を一覧にした表です。
> パソコンの種類（Windows・Mac・Linux）や、心臓部の部品（CPU）の違い、
> さらに小さな機械（Raspberry Pi のような手のひらサイズの基板）まで、
> 動く・動かないを記号でまとめてあります。工場の機械とおしゃべりする部品
> （Modbus・MQTT などの通信のきまり）が、それぞれの環境で使えるかどうかも
> 表で確認できます。
>
> 用語の意味は [`GLOSSARY.md`](GLOSSARY.md)（用語集）を参照してください。

本ドキュメントは LLMesh が公式サポートするプラットフォームの完全な
一覧と、各プラットフォーム固有の制約・推奨設定をまとめたものです。

> **AI 向け要約**: LLMesh コア（Pure Python）は **3 OS × 2 CPU 系 × 2 Python**
> = 主要 12 組合せで CI 検証済み。Rust 拡張は **8 ターゲット**で wheel
> 配布。EtherCAT のみ Linux 限定。それ以外は全プラットフォーム共通動作。

---

## 1. OS 対応マトリクス（Pure Python コア）

| OS | バージョン | x86_64 | ARM64 | 備考 |
|----|----------|:------:|:-----:|------|
| **Linux (glibc)** | Ubuntu 20.04+, RHEL 8+, Debian 11+ | ✓ | ✓ | manylinux_2_28 |
| **Linux (musl)** | Alpine 3.16+ | ✓ | ✓ | musllinux_1_2 |
| **Windows** | 10 21H2+, 11, Server 2019+ | ✓ | ✓ | MSVC 14.30+ |
| **macOS** | 12 Monterey+ | ✓ | ✓ | Apple Silicon ネイティブ |
| **FreeBSD** | 13.x+ | ✓ | – | コミュニティ |
| **NetBSD / OpenBSD** | 7.x+ | ⚠ | – | 動作報告ベース |

**Python**: 3.11 / 3.12（3.13 は 2026-Q3 検証中）

---

## 2. Rust 拡張 wheel 配布マトリクス

| ターゲット | プラットフォーム | wheel タグ |
|-----------|-----------------|-----------|
| `x86_64-unknown-linux-gnu` | Linux x86_64 (glibc) | `manylinux_2_28_x86_64` |
| `x86_64-unknown-linux-musl` | Linux x86_64 (musl) | `musllinux_1_2_x86_64` |
| `aarch64-unknown-linux-gnu` | Linux ARM64 (Pi 4/5, Graviton) | `manylinux_2_28_aarch64` |
| `aarch64-unknown-linux-musl` | Linux ARM64 (musl) | `musllinux_1_2_aarch64` |
| `x86_64-pc-windows-msvc` | Windows x86_64 | `win_amd64` |
| `aarch64-pc-windows-msvc` | Windows ARM64 | `win_arm64` |
| `x86_64-apple-darwin` | macOS Intel | `macosx_10_12_x86_64` |
| `aarch64-apple-darwin` | macOS Apple Silicon | `macosx_11_0_arm64` |

**abi3-py311 単一 wheel**: 各ターゲットの wheel 1 つで Python 3.11 / 3.12 / 3.13 全対応

> **Rust 拡張は完全オプション** — ビルド済 wheel が無い環境でも、
> 純 Python フォールバックで全機能が動作します（性能のみ低下）。

---

## 3. アーキテクチャ別の推奨用途

### x86_64 サーバー / ワークステーション
- 想定: 工場 SCADA サーバー、データセンター、開発機
- 推奨: フル機能（`pip install "llmesh[industrial,dev]"`）
- LLM バックエンド: Ollama / LlamaCpp（CPU or NVIDIA GPU）

### ARM64 / Apple Silicon
- 想定: Raspberry Pi 4/5、Jetson Orin、AWS Graviton、Mac M1/M2/M3
- 推奨: `pip install "llmesh[industrial]"`（dev は host で）
- LLM バックエンド: LlamaCpp（NEON 加速）/ MLX（Apple Silicon 専用）
- **Rust 拡張は ARM64 でも動作確認済み**（aarch64 wheel 配布）

### Linux musl（Alpine）
- 想定: 軽量 Docker イメージ、エッジコンテナ
- 推奨: `pip install "llmesh"`（最小機能）
- musllinux wheel が配布されるため Pure Python と Rust 両対応

### Windows
- 想定: 工場制御 PC、開発機、Windows Server
- 注意: EtherCAT 不可（Linux + CAP_NET_RAW 必須）
- 推奨: `pip install "llmesh[industrial]"`（ethercat 除外）

### macOS
- 想定: 開発機、設計検証
- 注意: 産業用ハードウェアドライバ未対応の場合あり（Modbus/MQTT/CAN は OK）

---

## 4. プラットフォーム別の制約マトリクス

| 機能 | Linux | Windows | macOS | FreeBSD |
|------|:-----:|:-------:|:-----:|:-------:|
| HTTP / TCP / UDP / SSH | ✓ | ✓ | ✓ | ✓ |
| Modbus TCP/RTU | ✓ | ✓ | ✓ | ✓ |
| Serial（pyserial） | ✓ | ✓ | ✓ | ✓ |
| OPC-UA（asyncua） | ✓ | ✓ | ✓ | ✓ |
| MQTT（paho-mqtt） | ✓ | ✓ | ✓ | ✓ |
| **EtherCAT（pysoem）** | ✓ | ✗ | ✗ | ✗ |
| CAN（python-can） | ✓ | ✓ | ✓ | ⚠ |
| BACnet（bacpypes3） | ✓ | ✓ | ✓ | ⚠ |
| FTP / SFTP / SSH server | ✓ | ✓ | ✓ | ✓ |
| Email（SMTP/IMAP/POP3） | ✓ | ✓ | ✓ | ✓ |
| SNMP / NTP | ✓ | ✓ | ✓ | ✓ |
| ROS 2 (rclpy) | ✓ | ⚠ | ⚠ | ✗ |
| Vision (Pillow) | ✓ | ✓ | ✓ | ✓ |
| 3D Sensors (AOI/Depth/DVS) | ✓ | ✓ | ✓ | ✓ |
| Rust 拡張 (llmesh_rust) | ✓ | ✓ | ✓ | ⚠ |

凡例: ✓ 公式サポート / ⚠ 動作可能だが CI 未網羅 / ✗ 非対応

---

## 5. CPU アーキテクチャ最適化

| アーキテクチャ | Rust target | 最適化 |
|---------------|------------|-------|
| x86_64 baseline | x86_64-* | SSE2 |
| x86_64-v3 (Haswell+) | x86_64-* (CFLAGS) | AVX2 / FMA |
| x86_64-v4 (Skylake-X+) | x86_64-* (CFLAGS) | AVX-512 |
| ARM64 baseline | aarch64-* | NEON |
| Apple Silicon | aarch64-apple-darwin | NEON + AMX (透過) |

`cargo build --release --target=...` で各 target のネイティブビルドが可能。

---

## 6. 容器化対応

### Docker
公式 multi-arch イメージ（v3 計画）：
```bash
docker pull llmesh/llmesh:2.5             # 自動 arch 解決
docker pull llmesh/llmesh:2.5-musl        # Alpine ベース
docker pull llmesh/llmesh:2.5-cuda12      # NVIDIA GPU 推論用
```

サポートする `--platform`:
- `linux/amd64`
- `linux/arm64`
- `linux/arm/v7`（Raspberry Pi 3 等、Pure Python のみ）

### Kubernetes
- Helm chart（v3 計画）
- マルチアーキテクチャ ノードセレクタ対応

### systemd
- `docs/systemd/llmesh.service` テンプレ（v3 計画）

---

## 7. クラウドプラットフォーム別ガイド

### AWS
- **EC2**: x86_64 / Graviton (ARM64) 両対応
- **ECS / EKS**: multi-arch image
- **IoT Greengrass**: Lambda runtime と統合可能（v3 計画）

### Azure
- **AKS / Container Apps**: amd64 / arm64
- **IoT Edge**: モジュール化計画

### GCP
- **GKE**: amd64 / arm64
- **Cloud Run**: 単一 region で動作確認

### オンプレミス / プライベートクラウド
- **VMware**: 任意 OS
- **OpenStack**: KVM
- **Proxmox**: LXC / QEMU 両対応

---

## 8. エッジ / IoT デバイス

| デバイス | 動作確認 | 備考 |
|---------|:-------:|------|
| Raspberry Pi 4 / 5 | ✓ | aarch64 wheel、推奨 4GB+ |
| NVIDIA Jetson Orin Nano | ✓ | ONNX Runtime + CUDA 推論 |
| Apple Silicon M1/M2/M3 | ✓ | MLX / LlamaCpp Metal |
| Intel NUC | ✓ | 開発機としても |
| Beagle Bone Black | ⚠ | armv7、Pure Python のみ |
| ESP32 / マイコン | ✗ | 範囲外（C 連携経由で SensorEvent 投入） |

---

## 9. 動作確認スクリプト

任意のプラットフォームで動作可否を即時確認：

```bash
python -c "
import sys, platform
from llmesh.industrial import (
    SensorEvent, IndustrialPipeline, IndustrialMetrics,
    TenantScope, IndustrialTracer, ModbusAdapter,
)
print('Python:', sys.version.split()[0])
print('OS:', platform.system(), platform.release())
print('CPU:', platform.machine())
try:
    import llmesh_rust
    print('Rust ext:', llmesh_rust.__version__, '(accelerated)')
except ImportError:
    print('Rust ext: not built (pure-Python fallback)')
print('All core imports OK ✓')
"
```

---

## 10. 既知の制約事項

| 制約 | 影響範囲 | ワークアラウンド |
|------|---------|--------------|
| EtherCAT は Linux 専用 | K-1 / I-3 系統 | Modbus TCP で代替 |
| ROS 2 rclpy は Windows/macOS で限定的 | ロボティクス | Linux Docker で実行 |
| SOC2 監査は単一ノード前提 | クラスタ運用 | I-1 シャーディング後に拡張 |
| CUDA は NVIDIA GPU のみ | LLM 推論加速 | CPU / Metal / DirectML フォールバック |
| ARMv7 は Rust 拡張の wheel 配布なし | Raspberry Pi 3 等 | Pure Python で動作 |
| Windows EtherCAT (TwinCAT 経由) 未対応 | C-2 / Beckhoff | pyads（既存）+ ADSAdapter（C-12 計画） |

---

## 11. プラットフォーム拡張ロードマップ

| 時期 | 内容 |
|------|------|
| v2.6 | armv7 wheel 配布、Linux PowerPC64LE 検証 |
| v2.7 | Docker 公式イメージ multi-arch |
| v3.0 | Kubernetes / Helm chart 公式 |
| v3.1 | ESP-IDF / Zephyr RTOS への薄いブリッジ |
| v3.2 | WebAssembly (WASI) 動作（pyodide）|
| v3.5 | iOS / Android 連携（センサー収集アプリ） |

---

## 参照ドキュメント

- セットアップ詳細: [`SETUP_GUIDE.md`](SETUP_GUIDE.md)
- アーキテクチャ: [`ARCHITECTURE.md`](ARCHITECTURE.md)
- 要件: [`REQUIREMENTS.md`](REQUIREMENTS.md)
