# LLMesh — 複数PC間接続ガイド

## 概要

```
PC-A (admin)            PC-B                    PC-C
  init                    init                    init
  gen_certs ca            gen_certs node          gen_certs node
  gen_certs node          ← ca.crt 受け取り        ← ca.crt 受け取り
  start                   peer add PC-A           peer add PC-A or PC-B
                          start                   start
                            ↕ gossip 自動伝播 ↕
```

初回接続（`peer add`）だけ手動。以降は **GossipClient が60秒ごとに自動伝播**。

---

## ステップ 1: 全ノードで依存関係インストール

```bash
# 全PC で実行
pip install -e ".[dev]"
pip install uvicorn[standard]
```

---

## ステップ 2: CA証明書生成（管理者PCで1回だけ）

```bash
# PC-A (admin) で実行
python scripts/gen_certs.py ca --out certs/
```

生成物:
- `certs/ca.key` — **絶対に外部へ渡さない**
- `certs/ca.crt` — 全ノードへ配布する

---

## ステップ 3: 各ノードで初期化

**各PC で個別に実行:**

```bash
# ノード名とIPを指定（例: PC-A が 192.168.1.2）
python scripts/gen_certs.py node --name node-a --ip 192.168.1.2 --out certs/

# ノードID・鍵・設定ファイルを生成
python scripts/llmesh_setup.py init
```

`ca.crt` を全ノードの `certs/` ディレクトリへコピーする（USBやscp等で渡す）:

```bash
# PC-B, PC-C へ ca.crt を配布
scp certs/ca.crt user@192.168.1.3:~/llmesh/certs/
scp certs/ca.crt user@192.168.1.4:~/llmesh/certs/
```

---

## ステップ 4: 各ノードを起動

```bash
# 起動コマンドを確認（各PCで）
python scripts/llmesh_setup.py start --port 8001

# 出力されたコマンドをそのまま実行（例）
LLMESH_NODE_IDENTITY_PATH=config/node.key.bin \
LLMESH_TRUSTED_PEERS_PATH=config/trusted_peers.json \
uvicorn llmesh.mcp.server:app --host 0.0.0.0 --port 8001 \
  --ssl-certfile certs/node.crt --ssl-keyfile certs/node.key
```

---

## ステップ 5: 最初のピア接続（TOFU）

PC-B から PC-A へ接続する方法は3通りあります:

**a) IPアドレス直指定（従来）**
```bash
python scripts/llmesh_setup.py peer add https://192.168.1.2:8001
```

**b) node_id 指定（rendezvous 経由）**
```bash
python scripts/llmesh_setup.py peer add peer:3yFx8... \
  --rendezvous-url http://rendezvous.local:9000
# または環境変数で指定:
# LLMESH_RENDEZVOUS_URL=http://rendezvous.local:9000
```

**c) DID 指定（rendezvous 経由）**
```bash
python scripts/llmesh_setup.py peer add did:llmesh:1:z6Mk... \
  --rendezvous-url http://rendezvous.local:9000
```

DID / node_id を指定した場合は rendezvous サーバーでエンドポイントを自動解決してから TOFU フローへ進みます。

出力例:
```
── Peer identity ──────────────────────────────────────────
  node_id     : peer:3yFx8...
  fingerprint : ab:cd:ef:12:34:56:78:90:ab:cd:ef:12:34:56:78:90
  endpoint    : https://192.168.1.2:8001

Verify this fingerprint matches what is shown on the PEER machine
(run: python scripts/llmesh_setup.py status  on the peer).

Trust this node? [y/N]
```

**PC-A 側で fingerprint を確認:**
```bash
python scripts/llmesh_setup.py status
# → fingerprint : ab:cd:ef:12:34:56:78:90:ab:cd:ef:12:34:56:78:90  ← 一致確認
```

一致したら `y` を入力。`config/trusted_peers.json` に保存される。

---

---

## Rendezvous サーバー（オプション）

IP アドレスを直接共有せずに接続したい場合は Rendezvous サーバーを使用します。

### 起動

```bash
# 任意の PC（または専用サーバー）で実行
uvicorn llmesh.rendezvous.server:app --factory \
  --host 0.0.0.0 --port 9000
```

### ノードのアナウンス

各ノードの起動スクリプトから登録します:

```python
from llmesh.identity.node_id import NodeIdentity
from llmesh.rendezvous.client import announce

identity = NodeIdentity.from_private_bytes(Path("config/node.key.bin").read_bytes())
announce(identity, "https://192.168.1.2:8001", "http://rendezvous.local:9000")
```

### セキュリティ特性

| 特性 | 内容 |
|------|------|
| なりすまし防止 | Ed25519 署名 + TOFU（同 node_id の公開鍵変更を拒否） |
| リプレイ防止 | タイムスタンプ ±300秒ウィンドウ（`ANNOUNCE_WINDOW_SECONDS = 300`） |
| Phase 1 | エンドポイント URL は平文保存 |
| Phase 2（将来） | `encrypted_announce.py` の AES-256-GCM で暗号化 |

### DID フォーマット

LLMesh は W3C DID Core に基づく独自メソッド `did:llmesh:1:` を使用します:

```
did:llmesh:1:z<base58btc(0xed01 || Ed25519_pubkey)>
```

バージョン番号 `1` により将来の鍵アルゴリズム移行が可能です。

---

## ステップ 6: 自動ピア伝播（Gossip）

手動操作はここまで。サーバー起動後、GossipClient が自動的に:

1. 既知ピアの `/registry/peers` を60秒ごとに pull
2. 返ってきたピアのマニフェスト（Ed25519署名）を検証
3. 新しいピアを `config/trusted_peers.json` へ追記
4. NodeRegistry へ登録

PC-C が PC-A だけを知っていても、PC-A が PC-B を知っていれば **自動的に PC-B も接続される**。

ピア一覧確認:
```bash
python scripts/llmesh_setup.py peer list
```

---

## セキュリティモデル

| 脅威 | 対策 |
|------|------|
| 通信の盗聴・改ざん | TLS（自己CA署名証明書） |
| なりすまし | Ed25519 リクエスト署名（X-LLMesh-Signature）+ body_sha256 canonical binding |
| リプレイ攻撃（ノード間） | タイムスタンプ ±30秒（`TOLERANCE_MS = 30_000`）+ NonceStore |
| リプレイ攻撃（rendezvous） | タイムスタンプ ±300秒（`ANNOUNCE_WINDOW_SECONDS = 300`）|
| 不正ノードの混入 | TOFU初回確認 + manifest Ed25519検証 |
| Gossip経由の汚染 | マニフェスト署名検証（自己証明 — trust-on-first-use） |

### Gossip の信頼モデル

Gossip は **推移的信頼** を使用します:
- A が B を信頼 → B が C を紹介 → A は C を自動信頼

厳格な管理が必要な場合は Gossip を無効にし、全ノードを手動で `peer add` してください。

---

## トラブルシューティング

**`auth_failed: untrusted_node`**
→ 相手ノードを `peer add` していない。または `trusted_peers.json` が未読み込み（サーバー再起動）。

**`timestamp_stale`**
→ PC 間の時刻がずれている。NTP で同期:
```bash
w32tm /resync   # Windows
timedatectl     # Linux
```

**TLS証明書エラー**
→ `ca.crt` が全ノードに配布されているか確認。`peer add` に `--ca-cert certs/ca.crt` を明示指定。

**Gossip が伝播しない**
→ ファイアウォールでポート（デフォルト8001）が開いているか確認。`/registry/peers` エンドポイントへ curl でアクセス可能か確認。
