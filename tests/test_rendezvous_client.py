"""Tests for ``llmesh.rendezvous.client.announce`` / ``lookup``.

既存 ``tests/test_rendezvous.py`` は server-side (FastAPI TestClient で
``/announce`` / ``/lookup`` を直接叩く) を網羅するが、**client.py の
``announce()`` / ``lookup()`` 関数自体はテストされていない**
(coverage 23%)。``urlopen`` を mock して以下の経路を検証する。

announce:
- happy path (status 200/201) → 例外なし
- 非 200/201 status → AnnounceError
- HTTPError (400/403/422 等) → AnnounceError
- URLError (connection refused 等) → AnnounceError
- payload に node_id/did/endpoint/timestamp_utc/signature が含まれる
- signature は identity の Ed25519 鍵で検証可能

lookup:
- happy path → endpoint 文字列を返す
- HTTPError 404 → LookupError "not found"
- HTTPError 500 等 → LookupError "HTTP {code}"
- URLError → LookupError "connection failed"
- 不正 JSON → LookupError "unexpected response format"
- "endpoint" キー欠落 → LookupError
- レスポンス過大 → LookupError "response too large"
- node_id の URL エンコード (":" 等を含む) が機能する
"""

from __future__ import annotations

import io
import json
import urllib.error
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from llmesh.identity.node_id import NodeIdentity
from llmesh.rendezvous.client import (
    AnnounceError,
    LookupError,
    announce,
    lookup,
)


# ---------------------------------------------------------------------------
# 共通モック支援
# ---------------------------------------------------------------------------


class _FakeResponse:
    """``urlopen`` の戻り値を模した最小 context manager."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: bytes = b"{}",
    ) -> None:
        self.status = status
        self._body = body

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            data = self._body
            self._body = b""
            return data
        chunk = self._body[:n]
        self._body = self._body[n:]
        return chunk

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


@contextmanager
def _patched_urlopen(side_effect):
    with patch("urllib.request.urlopen", side_effect=side_effect) as m:
        yield m


# ---------------------------------------------------------------------------
# announce — happy path / payload 検証
# ---------------------------------------------------------------------------


class TestAnnounceHappyPath:
    def test_announce_returns_none_on_201(self) -> None:
        identity = NodeIdentity.generate()

        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["data"] = req.data
            captured["method"] = req.get_method()
            return _FakeResponse(status=201, body=b'{"ok":true}')

        with _patched_urlopen(fake_urlopen):
            # 例外なしで戻る
            result = announce(identity, "http://10.0.0.1:8001", "https://rdv.example/")
            assert result is None

        assert captured["method"] == "POST"
        assert captured["url"].endswith("/announce")
        # 末尾 slash の重複を取り除く挙動を確認
        assert captured["url"] == "https://rdv.example/announce"

    def test_announce_payload_contains_required_fields(self) -> None:
        identity = NodeIdentity.generate()

        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout):
            captured["data"] = req.data
            return _FakeResponse(status=200)

        with _patched_urlopen(fake_urlopen):
            announce(identity, "http://10.0.0.1:8001", "https://rdv.example")

        payload = json.loads(captured["data"].decode("utf-8"))
        assert set(payload.keys()) >= {
            "node_id", "did", "endpoint", "public_key_hex",
            "timestamp_utc", "signature",
        }
        assert payload["node_id"] == identity.node_id
        assert payload["endpoint"] == "http://10.0.0.1:8001"
        assert payload["public_key_hex"] == identity.public_key_hex
        # signature は hex (Ed25519 sig は 64 bytes = 128 hex)
        assert len(payload["signature"]) == 128
        assert all(c in "0123456789abcdef" for c in payload["signature"])

    def test_announce_signature_verifies_with_identity_pubkey(self) -> None:
        """announce が生成した署名が identity の公開鍵で検証可能であること."""
        identity = NodeIdentity.generate()
        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout):
            captured["data"] = req.data
            return _FakeResponse(status=201)

        with _patched_urlopen(fake_urlopen):
            announce(identity, "http://10.0.0.1:8001", "https://rdv")

        payload = json.loads(captured["data"].decode("utf-8"))
        # client.py が組み立てるメッセージ:
        # "<node_id>|<endpoint>|<timestamp>|<pubkey>|<did>"
        message = (
            f"{payload['node_id']}|{payload['endpoint']}|"
            f"{payload['timestamp_utc']}|{payload['public_key_hex']}|"
            f"{payload['did']}"
        ).encode("utf-8")
        sig = bytes.fromhex(payload["signature"])
        # 公開鍵で検証 — 失敗すれば InvalidSignature が上がる
        identity.verify_with_public_hex(message, sig, payload["public_key_hex"])


# ---------------------------------------------------------------------------
# announce — エラー経路
# ---------------------------------------------------------------------------


class TestAnnounceErrors:
    def test_announce_raises_on_non_2xx_status(self) -> None:
        identity = NodeIdentity.generate()

        def fake_urlopen(req, timeout):
            # 200/201 以外: client は AnnounceError
            return _FakeResponse(status=204, body=b"unexpected")

        with _patched_urlopen(fake_urlopen), pytest.raises(AnnounceError) as exc_info:
            announce(identity, "http://e", "https://rdv")
        assert "204" in str(exc_info.value)

    def test_announce_raises_on_http_error(self) -> None:
        identity = NodeIdentity.generate()

        def fake_urlopen(req, timeout):
            raise urllib.error.HTTPError(
                "https://rdv/announce", 422, "Unprocessable", {},
                io.BytesIO(b'{"detail":"bad pubkey"}'),
            )

        with _patched_urlopen(fake_urlopen), pytest.raises(AnnounceError) as exc_info:
            announce(identity, "http://e", "https://rdv")
        msg = str(exc_info.value)
        assert "HTTP 422" in msg
        assert "bad pubkey" in msg

    def test_announce_raises_on_url_error(self) -> None:
        identity = NodeIdentity.generate()

        def fake_urlopen(req, timeout):
            raise urllib.error.URLError(reason="connection refused")

        with _patched_urlopen(fake_urlopen), pytest.raises(AnnounceError) as exc_info:
            announce(identity, "http://e", "https://rdv")
        assert "connection refused" in str(exc_info.value)


# ---------------------------------------------------------------------------
# lookup — happy path
# ---------------------------------------------------------------------------


class TestLookupHappyPath:
    def test_lookup_returns_endpoint_string(self) -> None:
        body = json.dumps({
            "node_id": "peer:abc",
            "endpoint": "https://10.0.0.5:8001",
            "did": "did:key:z6M...",
        }).encode("utf-8")

        def fake_urlopen(req, timeout):
            return _FakeResponse(status=200, body=body)

        with _patched_urlopen(fake_urlopen):
            ep = lookup("peer:abc", "https://rdv.example/")
        assert ep == "https://10.0.0.5:8001"

    def test_lookup_url_encodes_node_id_special_chars(self) -> None:
        """node_id に ":" 等が含まれていても URL エンコードされて正しく送られる."""
        body = json.dumps({"endpoint": "https://e"}).encode("utf-8")
        captured: dict[str, str] = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            return _FakeResponse(status=200, body=body)

        with _patched_urlopen(fake_urlopen):
            lookup("peer:has:colons", "https://rdv")
        # ":" は %3A にエンコードされる (urllib.parse.quote のデフォルト挙動)
        assert "%3A" in captured["url"]
        assert "peer%3Ahas%3Acolons" in captured["url"]

    def test_lookup_strips_trailing_slash_from_rendezvous_url(self) -> None:
        body = json.dumps({"endpoint": "https://e"}).encode("utf-8")
        captured: dict[str, str] = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            return _FakeResponse(status=200, body=body)

        with _patched_urlopen(fake_urlopen):
            lookup("peer", "https://rdv.example///")
        # 連続スラッシュも正しく rstrip されること
        assert captured["url"].startswith("https://rdv.example/lookup/")
        assert "//lookup/" not in captured["url"]


# ---------------------------------------------------------------------------
# lookup — エラー経路
# ---------------------------------------------------------------------------


class TestLookupErrors:
    def test_lookup_404_raises_not_found(self) -> None:
        def fake_urlopen(req, timeout):
            raise urllib.error.HTTPError(
                "https://rdv/lookup/x", 404, "Not Found", {}, io.BytesIO(b""),
            )

        with _patched_urlopen(fake_urlopen), pytest.raises(LookupError) as exc_info:
            lookup("missing-node", "https://rdv")
        assert "not found" in str(exc_info.value).lower()
        assert "missing-node" in str(exc_info.value)

    def test_lookup_500_raises_with_status_code(self) -> None:
        def fake_urlopen(req, timeout):
            raise urllib.error.HTTPError(
                "https://rdv/lookup/x", 500, "Server Error", {},
                io.BytesIO(b'{"err":"bang"}'),
            )

        with _patched_urlopen(fake_urlopen), pytest.raises(LookupError) as exc_info:
            lookup("any", "https://rdv")
        msg = str(exc_info.value)
        assert "HTTP 500" in msg
        assert "bang" in msg

    def test_lookup_url_error_raises_connection_failed(self) -> None:
        def fake_urlopen(req, timeout):
            raise urllib.error.URLError(reason="network down")

        with _patched_urlopen(fake_urlopen), pytest.raises(LookupError) as exc_info:
            lookup("any", "https://rdv")
        assert "connection failed" in str(exc_info.value)
        assert "network down" in str(exc_info.value)

    def test_lookup_invalid_json_raises_unexpected_format(self) -> None:
        def fake_urlopen(req, timeout):
            return _FakeResponse(status=200, body=b"not-json{{")

        with _patched_urlopen(fake_urlopen), pytest.raises(LookupError) as exc_info:
            lookup("any", "https://rdv")
        assert "unexpected response format" in str(exc_info.value)

    def test_lookup_missing_endpoint_key_raises_unexpected_format(self) -> None:
        # endpoint キーが無い valid JSON
        body = json.dumps({"node_id": "x", "did": "did:key:..."}).encode("utf-8")

        def fake_urlopen(req, timeout):
            return _FakeResponse(status=200, body=body)

        with _patched_urlopen(fake_urlopen), pytest.raises(LookupError) as exc_info:
            lookup("any", "https://rdv")
        assert "unexpected response format" in str(exc_info.value)

    def test_lookup_response_too_large_raises_lookup_error(self) -> None:
        # 大きすぎる body (DEFAULT_RENDEZVOUS_RESPONSE_BYTES = 64 KiB を超過)
        # _FakeResponse は read(n) を尊重するので簡単に作れる
        big_body = b'{"endpoint":"' + (b"x" * (1 << 17)) + b'"}'  # 128 KiB

        def fake_urlopen(req, timeout):
            return _FakeResponse(status=200, body=big_body)

        with _patched_urlopen(fake_urlopen), pytest.raises(LookupError) as exc_info:
            lookup("any", "https://rdv")
        assert "too large" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# announce → lookup integration via FastAPI TestClient
# ---------------------------------------------------------------------------


class TestAnnounceLookupIntegration:
    """client.announce / client.lookup を実 FastAPI server (TestClient) と組み
    合わせる integration test. urllib + httpx はそのままだと混ぜられないので、
    urlopen を TestClient 経由に書き換えて結合する.
    """

    def _make_urlopen_via_test_client(self, app):
        """``urlopen`` を FastAPI TestClient 経由に張り替える adapter."""
        from fastapi.testclient import TestClient

        tc = TestClient(app)

        def fake_urlopen(req, timeout):
            method = req.get_method()
            url = req.full_url
            # base prefix を剥がす (TestClient は relative path を期待)
            path = url.replace("http://testserver", "")
            if not path.startswith("/"):
                # absolute URL の場合は path 部分だけ使う
                from urllib.parse import urlparse
                path = urlparse(url).path
            data = req.data
            headers = dict(req.headers) if req.headers else {}

            if method == "POST":
                resp = tc.post(path, content=data, headers=headers)
            else:
                resp = tc.get(path)

            return _FakeResponse(
                status=resp.status_code, body=resp.content,
            )

        return fake_urlopen

    def test_announce_then_lookup_roundtrip(self) -> None:
        from llmesh.rendezvous.server import make_app

        app = make_app()
        identity = NodeIdentity.generate()
        endpoint = "http://10.0.0.42:8001"

        fake_urlopen = self._make_urlopen_via_test_client(app)
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            # announce → 例外なし
            announce(identity, endpoint, "http://testserver")
            # lookup → 同じ endpoint が返る
            looked_up = lookup(identity.node_id, "http://testserver")

        assert looked_up == endpoint
