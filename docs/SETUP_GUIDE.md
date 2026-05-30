# LLMesh 環境構築ガイド（AI/開発者共通）

> **はじめに（やさしい説明）**
> この文書は、LLMesh というソフトを自分のパソコンで動かすための「組み立て説明書」です。
> 家具を買ったときに付いてくる「まず箱を開けて、部品 A を取り付けて…」という手順書と同じで、
> 上から順番にコマンド（パソコンへの命令文）をそのままコピーして打っていけば、開発の準備が整います。
> むずかしい言葉が出てきたら、まとめ表で意味を確認できます → [用語集（GLOSSARY.md）](GLOSSARY.md)

このドキュメントは **AI エージェント・新規開発者の両方** が読み取って
LLMesh 開発環境を一発で再現できることを目的とした手順書です。
各ブロックは **コピペ実行可能** な形で記述します。

> **AI 向け要約（必読）**: LLMesh は Python 3.11+ ベース。
> Industrial 機能を使うなら `pip install llmesh[industrial]`。
> Rust 拡張で 6× 加速したいなら `rust_ext/` をビルド。
> 開発時は `pip install -e ".[dev]"`。テストは `pytest -q`。

---

## 1. 前提環境

| ツール | バージョン | 用途 |
|--------|----------|------|
| Python | **3.11 以上**（3.12 も可） | コア言語 |
| pip | 22+ | パッケージマネージャ |
| git | 2.40+ | 取得・履歴管理 |
| Rust toolchain | 1.80+（任意） | `rust_ext/` ビルド |
| maturin | 1.13+（任意） | PyO3 wheel ビルド |
| MSVC（Windows） | VS 2022 BuildTools | Rust 拡張ビルド時に必要 |

### OS 別の追加要件

- **Linux**: `build-essential`、EtherCAT を使うなら CAP_NET_RAW or root
- **macOS**: Xcode CLT（`xcode-select --install`）
- **Windows**: VS 2022 Build Tools（C++ workload）

---

## 2. 最小インストール（コア機能のみ）

```bash
git clone https://github.com/your-org/llmesh.git
cd llmesh
python -m pip install -e .
```

確認:
```bash
python -m llmesh --help
```

---

## 3. 産業用フル機能インストール

```bash
pip install -e ".[industrial,dev]"
```

これで以下が同時に揃います：

| パッケージ | 用途 |
|-----------|------|
| pymodbus | Modbus TCP/RTU |
| pyserial | RS-232 / RS-485 |
| asyncua | OPC-UA |
| paho-mqtt | MQTT v3/v5 |
| numpy / scipy | MT 法 / SPC |
| pytest / pytest-asyncio / pytest-cov | テスト |
| ruff | 静的解析 |
| bandit | セキュリティスキャン |
| hypothesis | property-based testing |
| coverage | カバレッジ計測 |

### 追加 extras

```bash
pip install -e ".[ethercat]"   # Linux + pysoem（要 CAP_NET_RAW）
pip install -e ".[can]"        # python-can（CAN bus）
pip install -e ".[bacnet]"     # bacpypes3（ビル管理）
pip install -e ".[vision]"     # Pillow（画像処理）
pip install -e ".[claude]"     # MCP stdio for Claude Code
pip install -e ".[email,ftp,mgmt,udp,ssh]"   # ネットワークアダプター
# v2.13+ 強化機能
pip install -e ".[presidio]"   # Microsoft Presidio（PII 検出 Layer 1.5）
pip install -e ".[rag]"        # RAG（numpy ベクトルストア）
```

> Presidio を実運用する場合は spaCy のモデルダウンロードも必要:
> ```bash
> python -m spacy download en_core_web_sm
> ```
> モデル未導入時は `PresidioDetector` は `presidio_unavailable` として
> no-op で動作（Layer 1.5 がスキップされ、既存 Layer 0/1/2 のみ動く）。

---

## 4. Rust 拡張のビルド（6× 高速化）

PointCloud / DVS シリアライズが高速化されます。

### 4-1. Rust ツールチェーン導入

```bash
# Linux/macOS
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Windows (PowerShell)
Invoke-WebRequest -Uri https://win.rustup.rs/x86_64 -OutFile rustup-init.exe
.\rustup-init.exe -y
```

### 4-2. maturin インストール

```bash
pip install maturin
```

### 4-3. ビルド + インストール

```bash
cd rust_ext
# Windows: 必要なら LIB に Python の libs ディレクトリを追加
#   PowerShell: $env:LIB = "C:\Users\<user>\AppData\Local\Programs\Python\Python311\libs;" + $env:LIB
python -m maturin build --release
pip install --force-reinstall target/wheels/llmesh_rust-*.whl
```

### 4-4. 確認

```bash
python -c "import llmesh_rust; print('OK', llmesh_rust.__version__)"
# → OK 0.1.0
```

`llmesh_rust` がインポート可能になると、`PointCloud.to_bytes / from_bytes` および
`encode_dvs_events / decode_dvs_events` が自動的に Rust 実装に切り替わります。
ビルド未済の環境でも純 Python フォールバックで動作するため、
**Rust ビルドは任意**です。

### 4-5. 性能比較（実測 2026-05-07）

| 操作 | Pure Python | Rust | 倍率 |
|------|-----------:|-----:|----:|
| PointCloud encode (1M) | 4.0M pts/s | **24.1M pts/s** | **6.0×** |
| PointCloud decode (1M) | 3.7M pts/s | 5.9M pts/s | 1.6× |
| DVS encode (1M) | 3.4M evt/s | 5.5M evt/s | 1.6× |
| DVS decode (1M) | 695K evt/s | 720K evt/s | 1.0× |

---

## 5. テスト実行

```bash
# 全件
pytest -q

# 産業 Industrial 関連のみ（高速）
pytest tests/test_industrial_*.py tests/test_sensor_3d_*.py \
       tests/test_*_adapter.py tests/test_property_based.py -q

# property-based を多めに
pytest tests/test_property_based.py --hypothesis-show-statistics -q

# カバレッジ
coverage run -m pytest && coverage report
```

---

## 6. 静的解析・セキュリティスキャン

```bash
ruff check llmesh/        # 静的解析
bandit -r llmesh/ -ll     # セキュリティ（HIGH/MEDIUM のみ表示）
```

期待結果（v2.4.0 時点）:
- ruff: error なし
- bandit: HIGH=0, MEDIUM=0

---

## 7. ベンチマーク

```bash
# Pure Python 経由 / Rust 経由は自動切替
python benchmarks/bench_serialization.py
```

`docs/papers/_bench_results.md` に保存することで論文素材としても使えます。

---

## 8. 合成データセット生成（テスト・論文向け）

```bash
mkdir -p tests/_synth/{aoi,depth,dvs}
python tools/gen_synthetic_dataset.py --type aoi   --count 100 --out tests/_synth/aoi
python tools/gen_synthetic_dataset.py --type depth --count 50  --out tests/_synth/depth
python tools/gen_synthetic_dataset.py --type dvs   --count 200 --out tests/_synth/dvs
```

固定シード（42）でバイト再現可能。

---

## 9. Claude Code MCP 統合

`~/.claude.json` に追加:

```json
{
  "mcpServers": {
    "llmesh": {
      "command": "python",
      "args": ["-m", "llmesh", "serve-mcp"],
      "env": {
        "LLMESH_BACKEND": "ollama",
        "LLMESH_MODEL": "llama3.2"
      }
    }
  }
}
```

Claude Code 内で `generate_code` / `review_code` / `explain_code` /
`suggest_tests` ツールが利用可能になります（PromptFirewall 通過保証）。

---

## 10. Docker（推奨：将来の v2.5+）

```bash
docker build -t llmesh:2.4 .
docker run --rm -p 9100:9100 llmesh:2.4
```

`Dockerfile` のテンプレートは `docs/dockerfiles/` を参照（v3 で同梱予定）。

---

## 11. 論文素材コーパス収集

```bash
# arXiv から 産業 + 画像処理関連 論文メタデータを取得
python tools/collect_image_papers.py --source arxiv \
    --query "automated optical inspection" --max-results 100 \
    --out docs/papers/image_corpus/arxiv_aoi.jsonl

# Semantic Scholar から
python tools/collect_image_papers.py --source semantic_scholar \
    --query "event camera industrial" --max-results 50 \
    --out docs/papers/image_corpus/s2_dvs.jsonl
```

---

## 12. 開発フロー（推奨）

1. issue 作成 / 仕様確認（`docs/REQUIREMENTS.md`）
2. ブランチ作成
3. **テストを先に書く**（pytest + hypothesis）
4. 実装（既存 `IndustrialAdapter` Protocol 準拠）
5. `ruff check llmesh/` パス
6. `bandit -r llmesh/ -ll` パス
7. `pytest -q` フル PASS
8. `CHANGELOG.md` 更新
9. PR 作成

---

## 13. AI エージェント向け補足

### コードベース・ナビゲーション

| 場所 | 内容 |
|------|------|
| `llmesh/industrial/` | 産業用アダプター・解析エンジン |
| `llmesh/industrial/sensor_3d/` | 3D センサー（AOI/Depth/DVS） |
| `llmesh/privacy/` | PromptFirewall / Summarizer |
| `llmesh/protocol/` | ネットワークプロトコル（HTTP/MCP/SSH 等） |
| `tests/` | テストスイート（~290 件） |
| `rust_ext/` | Rust 拡張ソース |
| `docs/papers/` | 精密工学会向け論文素材 |
| `docs/REQUIREMENTS.md` | 全 91 章要件定義 |

### よく使うコマンド集（AI が即実行可能）

```bash
# プロジェクトの状態確認
git status && python -m pytest -q --tb=no | tail -3

# 新しいアダプター追加時のチェックリスト
# 1. llmesh/industrial/<name>_adapter.py 実装
# 2. llmesh/industrial/__init__.py に追加
# 3. tests/test_<name>_adapter.py 作成
# 4. tests/test_adapter_protocol.py に Protocol 準拠検証追加
# 5. pyproject.toml に extras + entry-point 追加
# 6. docs/CHANGELOG.md 更新

# Rust 拡張の追加時
# 1. rust_ext/src/lib.rs に新 #[pyfunction] 追加
# 2. Python 側に try-import + フォールバック追加
# 3. cd rust_ext && python -m maturin build --release
# 4. pip install --force-reinstall target/wheels/*.whl
```

### よくある落とし穴

- **Windows + Rust ビルド失敗（python3.lib 見つからない）**:
  → `$env:LIB = "$env:LOCALAPPDATA\Programs\Python\Python311\libs;" + $env:LIB`
- **テストの hypothesis テストでサロゲート文字エラー**:
  → strategies に `blacklist_categories=("Cs",)` を追加
- **EtherCAT 不可（Linux）**: `sudo setcap cap_net_raw=eip $(which python)`
- **pytest が `_synth/` を勝手に拾う**: `.gitignore` と `pytest.ini` で除外
- **bandit B101 警告**: `assert` の本番コード混入 → `-ll` で MEDIUM のみ抑制

### 重要な不変条件（守るべきルール）

1. `shell=True` / `eval` / `exec` / `pickle` 使用禁止
2. すべてのアダプターが `IndustrialAdapter` Protocol を満たす
3. PromptFirewall を経由しない LLM 直接呼び出し禁止
4. Rust 拡張のワイヤフォーマットは Python 実装と byte 完全一致
5. テスト追加時は プロパティベーステスト（property-based testing） を 1 件以上含める

---

## 14. トラブルシューティング詳細

| 症状 | 原因 | 解決 |
|------|------|------|
| `RuntimeError: pysoem is not installed` | EtherCAT extra 未導入 | `pip install -e ".[ethercat]"`（Linux のみ） |
| `RuntimeError: bacpypes3 is not installed` | BACnet extra 未導入 | `pip install -e ".[bacnet]"` |
| `ImportError: llmesh_rust` | Rust 未ビルド | 任意。pure-Python で動作 |
| `ValueError: invalid metric name` | Prometheus 命名違反 | `[a-zA-Z_:][a-zA-Z_0-9:]*` に修正 |
| `ValueError: invalid tenant_id` | テナント ID 命名違反 | `[a-zA-Z0-9_\-]{1,64}` |
| AOI 画像が処理されない | 書き込み中ファイル | 自動検知（2 ポーリング待機） |
| MQTT 接続失敗 | broker 未起動 / ポート違い | `mosquitto -v` でローカルテスト |

---

## 15. 参照ドキュメント

- 仕様: [`SPECIFICATION.md`](SPECIFICATION.md)
- 産業ガイド: [`INDUSTRIAL_GUIDE.md`](INDUSTRIAL_GUIDE.md)
- クイックスタート: [`USAGE.md`](USAGE.md)
- 要件: [`REQUIREMENTS.md`](REQUIREMENTS.md)
- 変更履歴: [`CHANGELOG.md`](CHANGELOG.md)
- ロードマップ: [`ROADMAP.md`](ROADMAP.md)
- セキュリティ: [`SECURITY.md`](SECURITY.md)
- アーキテクチャ: [`ARCHITECTURE.md`](ARCHITECTURE.md)
- 論文素材: [`papers/README.md`](papers/README.md)
- 旧 SETUP: [`SETUP.md`](SETUP.md)
