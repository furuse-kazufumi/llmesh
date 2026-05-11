"""Tests for `llmesh dashboard` subcommand (subprocess shim to llove).

llmesh は産業ターゲット (組み込み Linux / RTOS) でも素のまま動かす方針
のため textual / rich / pillow に依存しない. dashboard コマンドは llove を
subprocess で呼ぶだけの薄いシムであり, ここでは:

- llove 未インストール時の検出と案内メッセージ
- subprocess 呼び出しが正しい argv (python -m llove ...) を組み立てるか
- --check モードの 2 状態
- 戻り値の透過 (llove の returncode をそのまま返す)

をモックで検証する. 本物の llove は起動しない.
"""
from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

from llmesh import __main__ as m


# ---------------------------------------------------------------------------
# 未インストール時の挙動
# ---------------------------------------------------------------------------


class TestLloveMissing:
    def test_check_reports_missing(self, capsys: pytest.CaptureFixture[str]) -> None:
        with mock.patch.object(m, "_llove_module_present", return_value=False):
            rc = m._cmd_dashboard(["--check"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "missing" in err and "llove" in err

    def test_dashboard_without_llove_returns_1(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with mock.patch.object(m, "_llove_module_present", return_value=False):
            rc = m._cmd_dashboard([])
        assert rc == 1
        err = capsys.readouterr().err
        assert "llmesh-llove" in err  # インストール案内
        assert "llmesh-suite" in err  # 一括インストール案内


# ---------------------------------------------------------------------------
# インストール済み時の subprocess argv
# ---------------------------------------------------------------------------


class TestSubprocessInvocation:
    def _fake_completed(self, returncode: int = 0) -> object:
        # subprocess.run の戻り値モック
        ns = types.SimpleNamespace()
        ns.returncode = returncode
        return ns

    def test_default_runs_llove_demo(self) -> None:
        with mock.patch.object(m, "_llove_module_present", return_value=True), \
             mock.patch("subprocess.run") as run:
            run.return_value = self._fake_completed(0)
            rc = m._cmd_dashboard([])
        assert rc == 0
        args_list = run.call_args.args[0]
        assert args_list[0] == sys.executable
        assert args_list[1:4] == ["-m", "llove", "demo"]

    def test_passes_through_args(self) -> None:
        with mock.patch.object(m, "_llove_module_present", return_value=True), \
             mock.patch("subprocess.run") as run:
            run.return_value = self._fake_completed(0)
            rc = m._cmd_dashboard(["view", "--source", "llmesh+modbus://h:502"])
        assert rc == 0
        args_list = run.call_args.args[0]
        assert args_list[1:] == ["-m", "llove", "view", "--source", "llmesh+modbus://h:502"]

    def test_propagates_returncode(self) -> None:
        with mock.patch.object(m, "_llove_module_present", return_value=True), \
             mock.patch("subprocess.run") as run:
            run.return_value = self._fake_completed(42)
            rc = m._cmd_dashboard(["demo"])
        assert rc == 42

    def test_check_when_present(self, capsys: pytest.CaptureFixture[str]) -> None:
        with mock.patch.object(m, "_llove_module_present", return_value=True):
            rc = m._cmd_dashboard(["--check"])
        assert rc == 0
        assert "OK" in capsys.readouterr().out

    def test_no_shell_used(self) -> None:
        """Security: subprocess.run must be invoked with list argv (no shell=True)."""
        with mock.patch.object(m, "_llove_module_present", return_value=True), \
             mock.patch("subprocess.run") as run:
            run.return_value = self._fake_completed(0)
            m._cmd_dashboard(["demo"])
        # check=False, no shell kwarg
        kwargs = run.call_args.kwargs
        assert "shell" not in kwargs or kwargs["shell"] is False
        # argv は list
        assert isinstance(run.call_args.args[0], list)

    def test_spawn_failure_returns_1(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with mock.patch.object(m, "_llove_module_present", return_value=True), \
             mock.patch("subprocess.run", side_effect=FileNotFoundError("python missing")):
            rc = m._cmd_dashboard(["demo"])
        assert rc == 1
        assert "failed to spawn" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# main() ディスパッチ
# ---------------------------------------------------------------------------


class TestMainDispatch:
    def test_dashboard_in_help_listing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = m.main([])
        assert rc == 0
        assert "dashboard" in capsys.readouterr().out

    def test_dashboard_dispatched(self) -> None:
        with mock.patch.object(m, "_cmd_dashboard", return_value=7) as fn:
            rc = m.main(["dashboard", "demo", "--scenario", "cost"])
        assert rc == 7
        fn.assert_called_once_with(["demo", "--scenario", "cost"])
