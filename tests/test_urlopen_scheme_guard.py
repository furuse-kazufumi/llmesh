"""urlopen に渡る URL の scheme を、開く手前で見ていることの門。

★この門が生まれた理由: `llmesh/speculative/transport.py` の `# nosec B310` は
**urlopen の 1 行手前**に書かれていた。bandit の nosec は同じ行しか見ないので、
その抑制は何もしていなかった。しかも文面は「register で検証済み」と書いてあったが、
`NodeRegistry.register()` は endpoint を検証しない —— 検証しているのは
`discovery/router.py` の HTTP 入口だけで、ライブラリ API から直接 register すれば
素通りする。**閉じているように見えて閉じていない門**だった。

`file:` を許すと urlopen はローカルファイルを読む(CWE-22)。だから
「例外が上がること」だけでなく **urlopen が呼ばれていないこと**も見る ——
例外は読み終わった後にも上げられるので、上がったことは開いていない証拠にならない。
"""
from __future__ import annotations

import urllib.request

import pytest

from llmesh.security.endpoint_validator import EndpointValidationError, EndpointValidator
from llmesh.skills.sync import SkillSyncError, UrllibTransport
from llmesh.speculative import transport as spec_transport


class _Tripwire:
    """呼ばれたら記録するだけの urlopen。★開いてしまったことを検出する。"""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def __call__(self, *a: object, **k: object) -> object:
        self.calls.append(a)
        raise AssertionError("urlopen が呼ばれた —— 検査が手前で止めていない")


@pytest.fixture
def tripwire(monkeypatch: pytest.MonkeyPatch) -> _Tripwire:
    tw = _Tripwire()
    monkeypatch.setattr(urllib.request, "urlopen", tw)
    return tw


BAD_URLS = [
    "file:///etc/passwd",              # ローカルファイル読み出し(CWE-22)
    "file://C:/Windows/win.ini",
    "ftp://example.com/x",             # 別の scheme
    "gopher://example.com/",
    "http://user:pass@example.com/",   # URL 埋め込みの資格情報
    "http://169.254.169.254/latest/",  # クラウド metadata(IMDS)
    "http://metadata.google.internal/",
]


@pytest.mark.parametrize("url", BAD_URLS)
def test_skill_sync_refuses_before_opening(url: str, tripwire: _Tripwire) -> None:
    with pytest.raises(SkillSyncError):
        UrllibTransport().get_json(url)
    assert tripwire.calls == [], "拒む前に開いている"


@pytest.mark.parametrize("url", BAD_URLS)
def test_skill_sync_post_refuses_before_opening(url: str, tripwire: _Tripwire) -> None:
    with pytest.raises(SkillSyncError):
        UrllibTransport().post_json(url, {"a": 1})
    assert tripwire.calls == [], "拒む前に開いている"


@pytest.mark.parametrize("url", BAD_URLS)
def test_speculative_transport_refuses_before_opening(url: str, tripwire: _Tripwire) -> None:
    with pytest.raises(EndpointValidationError):
        spec_transport._validated_endpoint(url)
    assert tripwire.calls == []


def test_on_prem_addresses_are_allowed() -> None:
    """★on-prem を壊さないことも門にする。

    私設アドレスと同一ホストは、この製品では**正当な peer** である
    (「Local 環境こそ AI の本来の居場所」)。厳しくしすぎて自宅の mesh が
    喋れなくなるなら、それは直しではなく別の壊れ方である。
    """
    v = EndpointValidator(allow_private=True, allow_loopback=True)
    for ok in ("http://127.0.0.1:8080", "http://localhost:11434",
               "http://192.168.1.5:8080", "https://[::1]:9000"):
        assert v.validate(ok)


def test_loopback_stays_blocked_by_default() -> None:
    """既定は厳格のまま —— 開けるのは使う側が明示したときだけ。"""
    v = EndpointValidator(allow_private=True)
    for blocked in ("http://127.0.0.1:8080", "http://localhost:11434"):
        with pytest.raises(EndpointValidationError):
            v.validate(blocked)


def test_metadata_host_is_blocked_even_with_everything_open() -> None:
    """★IMDS は何を開けても閉じたまま。peer として正当なことが無いから。"""
    v = EndpointValidator(allow_private=True, allow_loopback=True)
    for imds in ("http://169.254.169.254/latest/meta-data/",
                 "http://metadata.google.internal/computeMetadata/v1/"):
        with pytest.raises(EndpointValidationError):
            v.validate(imds)


def test_the_guard_would_catch_a_removed_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """★門を壊して確かめる —— 検査を素通しに差し替えたら、門が落ちること。

    これが無いと「常に例外を上げる何か」でも門が緑になり、検査が効いているのか
    偶然なのか区別できない。
    """
    monkeypatch.setattr("llmesh.skills.sync._validated", lambda u: u)
    tw = _Tripwire()
    monkeypatch.setattr(urllib.request, "urlopen", tw)
    with pytest.raises(AssertionError):      # tripwire が鳴る = 開いてしまった
        UrllibTransport().get_json("file:///etc/passwd")
    assert tw.calls, "素通しにしたのに開かれなかった —— 探針が別の物を見ている"
