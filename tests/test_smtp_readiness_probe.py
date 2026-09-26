"""SMTPAdapter の自前 readiness 探りの門。

何を守るか: aiosmtpd の `Controller.start()` は第 2 段で「引き金を 1 回・1 秒だけ」
引いて諦める(controller.py の `_trigger_server()` の socket_timeout を捨て、その後
`_factory_invoked` を待つだけ)。だから混んだ runner では `ready_timeout` をいくら
伸ばしても落ちる —— macOS CI で 1 秒 → 30 秒にしても落ちた(2026-09-26 実測)。

ここでは aiosmtpd を立てずに、**その状況だけを作る**:
`Controller.start()` が TimeoutError を投げ、`_thread_exception` は None。
そのとき

 - 港で誰かが `220` と挨拶する → 起動できたものとして進む(controller を捨てない)
 - 誰も居ない → きちんと片付けてから、**探りが何を見たか**を添えて落ちる

★両側を固定する。成功側だけ固定すると「何が何でも成功と見なす」壊れた実装でも
通ってしまう。

★所見の文面を OS 依存の語で判定しない。閉じた港への接続は Linux/macOS では
ConnectionRefusedError、Windows では timeout になる(実測)。見分けたいのは
「繋がる前に失敗した」か「繋がったが無言だった」かなので、そこで判定する。
"""
from __future__ import annotations

import socket
import threading
from unittest.mock import patch

import pytest

from llmesh.protocol import smtp_adapter as sa
from llmesh.protocol.smtp_adapter import SMTPAdapter


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Greeter:
    """`220` と挨拶するだけの偽 SMTP。aiosmtpd を使わずに「港が応答する」を作る。"""

    def __init__(self, greeting: bytes = b"220 fake.test ESMTP\r\n") -> None:
        self._greeting = greeting
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        self._sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                continue
            with conn:
                if self._greeting:
                    try:
                        conn.sendall(self._greeting)
                    except OSError:
                        pass
                else:
                    # 無言のまま相手が諦めるのを待つ(繋がるが挨拶しない港)。
                    self._stop.wait(1.0)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)
        self._sock.close()


class _FakeController:
    """第 2 段で必ず TimeoutError を投げる controller。`_thread_exception` は None。"""

    instances: list[_FakeController] = []

    def __init__(self, *_args, **_kwargs) -> None:
        self._thread_exception = None
        self.stopped = False
        _FakeController.instances.append(self)

    def start(self) -> None:
        raise TimeoutError(
            "SMTP server started, but not responding within allotted time. "
            "This might happen if the system is too busy. "
            "Try increasing the `ready_timeout` parameter."
        )

    def stop(self, no_assert: bool = False) -> None:
        self.stopped = True


@pytest.fixture(autouse=True)
def _fast_probe():
    """試験では探りを短くする。既定 (12 回 x 1 秒) は単体試験には長い。"""
    _FakeController.instances.clear()
    with patch.object(sa, "_PROBE_ATTEMPTS", 3), \
         patch.object(sa, "_PROBE_TIMEOUT", 0.4), \
         patch.object(sa, "_PROBE_PAUSE", 0.05):
        yield
    _FakeController.instances.clear()


class TestReadinessProbe:
    async def test_server_that_answers_is_treated_as_started(self):
        """引き金だけが遅れた場合: 港が 220 を返すなら起動扱いで進む。"""
        greeter = _Greeter()
        adapter = SMTPAdapter()
        try:
            with patch.object(sa, "Controller", _FakeController):
                await adapter.start("127.0.0.1", greeter.port)
            assert adapter.is_running
            # ★controller を捨てていないこと。捨てるとスレッドと listen ソケットが残る。
            assert adapter._controller is _FakeController.instances[-1]
            assert adapter._controller.stopped is False
        finally:
            greeter.close()

    async def test_nothing_listening_raises_with_what_the_probe_saw(self):
        """本物の失敗: 誰も居ないなら、探りの所見つきで落ち、controller を片付ける。"""
        port = _free_port()
        adapter = SMTPAdapter()
        with patch.object(sa, "Controller", _FakeController):
            with pytest.raises(OSError) as ei:
                await adapter.start("127.0.0.1", port)
        msg = str(ei.value)
        assert "could not bring up the SMTP server" in msg
        assert str(port) in msg
        # ★所見が入っていること。「落ちた」だけでは次に見た人が原因に辿れない。
        assert "probe saw" in msg
        assert adapter._controller is None
        assert _FakeController.instances[-1].stopped is True
        assert not adapter.is_running

    async def test_probe_tells_failed_to_connect_apart_from_connected_but_silent(self):
        """「繋がる前に失敗」と「繋がったが無言」を別の所見として言えること。

        前者はサーバが立っていない、後者は別の何かが港を持っている —— 原因が別物。
        どちらも「220 が返らない」で一括りにすると、次に何を調べるか言えなくなる。
        """
        port = _free_port()
        banner_none, not_connected = SMTPAdapter._probe_smtp("127.0.0.1", port)
        silent = _Greeter(greeting=b"")
        try:
            banner_silent, connected_quiet = SMTPAdapter._probe_smtp(
                "127.0.0.1", silent.port
            )
        finally:
            silent.close()
        assert banner_none == "" and banner_silent == ""
        # 繋がった側は「繋がった」と言い、繋がらなかった側は例外の型を言う。
        assert connected_quiet.startswith("connected"), connected_quiet
        assert not not_connected.startswith("connected"), not_connected
        assert "Error" in not_connected, not_connected
        assert connected_quiet != not_connected
