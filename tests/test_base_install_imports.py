"""素のインストール(任意 extra 無し)で llmesh が import できることの門。

なぜこの門が要るか: CI は `pip install -e .[all]` を入れる。だから
**事故の起きる場所で測っていない** —— 素の `pip install llmesh` を誰も試して
いなかった。実際 2026-09-27 時点では

    from llmesh.protocol import AdapterRegistry     # パッケージ docstring の例

が `ModuleNotFoundError: No module named 'paramiko'` で落ちていた。paramiko は
pyproject では `ssh` という**任意**の extra なのに、`llmesh/protocol/__init__.py`
が `ssh_adapter` / `sftp_adapter` を無条件 import していたため。

測り方: 依存を消してインストールし直すことはできないので、**子プロセスの中で
その名前の import だけを塞ぐ**。`sys.meta_path` の先頭に finder を挿し、
対象モジュールと配下を ModuleNotFoundError にしてから llmesh を import する。
子プロセスなので、既にこのプロセスで読み込まれている paramiko に影響しない。

門が本物であることの確かめ方(この試験自体の自己検査):
`test_the_blocker_actually_blocks` が「塞いだ名前が実際に import できない」ことを
確かめる。これが無いと、finder が効いていない空の門でも全部緑になる。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap

import pytest

# 任意 extra が持ち込むもの。素のインストールではどれも無い。
# base の依存は cryptography / jsonschema / base58 / fastapi / uvicorn だけ。
OPTIONAL_MODULES = [
    "paramiko",     # ssh   extra  (SSHAdapter / SFTPAdapter)
    "aiosmtpd",     # email extra  (SMTPAdapter)
    "pyftpdlib",    # ftp   extra  (FTPAdapter)
    "zeroconf",     # udp   extra  (mDNS discovery)
    "watchdog",     # localfile extra
    "pysnmp",       # mgmt  extra  (SNMPAdapter)
    "ntplib",       # mgmt  extra
    "msgpack",      # msgpack extra (codec は JSON に落ちる)
    "PIL",          # vision extra
]

_BLOCKER = '''
import sys


class _Blocked:
    """名指しした最上位モジュールとその配下を import 不能にする finder。"""

    def __init__(self, names):
        self._names = tuple(names)

    def find_module(self, fullname, path=None):      # 旧 API 互換
        return self.find_spec(fullname, path)

    def find_spec(self, fullname, path=None, target=None):
        top = fullname.split(".")[0]
        if top in self._names:
            raise ModuleNotFoundError("No module named %r" % fullname, name=fullname)
        return None


_NAMES = __NAMES__
for _n in list(sys.modules):
    if _n.split(".")[0] in _NAMES:
        del sys.modules[_n]
sys.meta_path.insert(0, _Blocked(_NAMES))
'''


def _run_without(names: list[str], body: str) -> subprocess.CompletedProcess:
    """*names* を import 不能にした子プロセスで *body* を走らせる。"""
    # ★テンプレートに `%r` が本文として入っているので % 書式で埋めない
    #   (最初そうして TypeError: not enough arguments for format string)。
    code = _BLOCKER.replace("__NAMES__", repr(tuple(names))) + textwrap.dedent(body)
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=180,
    )


def test_the_blocker_actually_blocks() -> None:
    """★門の自己検査。塞いだ名前が本当に import できないことを先に確かめる。

    これが無いと、finder が効いていなくても下の試験が全部緑になる
    (「発見ゼロは未実行かもしれない」)。
    """
    r = _run_without(["paramiko"], """
        try:
            import paramiko
        except ModuleNotFoundError:
            print("BLOCKED")
        else:
            print("NOT-BLOCKED")
    """)
    assert "BLOCKED" in r.stdout, (r.stdout, r.stderr)
    assert "NOT-BLOCKED" not in r.stdout, (r.stdout, r.stderr)


@pytest.mark.parametrize("missing", OPTIONAL_MODULES)
def test_protocol_package_imports_without_one_optional_dep(missing: str) -> None:
    """任意 extra が 1 つ欠けただけで `llmesh.protocol` が落ちてはいけない。"""
    r = _run_without([missing], """
        from llmesh.protocol import AdapterRegistry, NodeAddress, UnifiedMessage
        assert "http" in AdapterRegistry.available()
        print("OK", len(AdapterRegistry.available()))
    """)
    assert r.returncode == 0, (
        f"{missing} が無いだけで llmesh.protocol が import できない:\n{r.stderr}"
    )
    assert r.stdout.startswith("OK"), (r.stdout, r.stderr)


def test_protocol_package_imports_with_no_optional_deps_at_all() -> None:
    """素のインストールそのもの —— 任意 extra を全部欠いた状態。"""
    r = _run_without(OPTIONAL_MODULES, """
        from llmesh.protocol import AdapterRegistry
        print("OK", ",".join(AdapterRegistry.available()))
    """)
    assert r.returncode == 0, (
        f"素のインストールで import できない:\n{r.stderr}"
    )
    # 素でも動くべき口が居ること(名前を数えるだけの門にしない)。
    for name in ("http", "tcp", "tcp_stream", "udp"):
        assert name in r.stdout, (name, r.stdout)


def test_ssh_adapter_says_which_extra_to_install() -> None:
    """paramiko が無い回、構築は「どの extra を入れるか」を言って落ちること。

    黙って別の例外(AttributeError: 'NoneType' has no attribute 'PKey')に
    化けると、使う人は原因に辿れない。
    """
    r = _run_without(["paramiko"], """
        from llmesh.protocol import AdapterRegistry
        for proto in ("ssh", "sftp"):
            try:
                AdapterRegistry.create(proto)
            except ImportError as exc:
                assert "llmesh[ssh]" in str(exc), (proto, str(exc))
                print("RAISED", proto)
            else:
                raise AssertionError("%s が paramiko 無しで構築できてしまった" % proto)
    """)
    assert r.returncode == 0, r.stderr
    assert "RAISED ssh" in r.stdout and "RAISED sftp" in r.stdout, (r.stdout, r.stderr)


# 任意 extra 全部。素のインストールの依存は
# cryptography / jsonschema / base58 / fastapi / uvicorn だけ。
ALL_OPTIONAL = OPTIONAL_MODULES + [
    "numpy", "scipy", "tomli_w",        # industrial / rag extra
    "pymodbus", "serial", "asyncua", "paho",   # industrial extra
    "pysoem", "can", "bacpypes3",       # ethercat / can / bacnet extra
    "presidio_analyzer", "spacy",       # presidio / compliance extra
    "pydnp3",                           # dnp3 extra
    "mcp",                              # claude extra
    "httpx", "qwen_agent", "zhipuai",   # cn-llm extra 族
]


def _subpackages() -> list[str]:
    """`llmesh/` 直下の部分パッケージ名。名前を手で並べない(足されたら漏れる)。"""
    root = pathlib.Path(__file__).resolve().parent.parent / "llmesh"
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and d.name != "__pycache__" and (d / "__init__.py").exists()
    )


def test_there_are_subpackages_to_sweep() -> None:
    """★数え上げ自体が空振りしていないことの門。0 個なら下の試験は何も測らない。"""
    subs = _subpackages()
    assert len(subs) >= 20, subs
    assert "protocol" in subs, subs


@pytest.mark.parametrize("sub", _subpackages())
def test_every_subpackage_imports_with_no_optional_deps(sub: str) -> None:
    """任意 extra を 1 つも持たない環境で、全部分パッケージが import できること。

    2026-09-27 の `llmesh.protocol` と同じ欠陥(任意 extra の無条件 import)が
    別の部分パッケージに出た回に、ここで止まる。
    """
    r = _run_without(ALL_OPTIONAL, f"""
        import importlib
        importlib.import_module("llmesh.{sub}")
        print("OK")
    """)
    assert r.returncode == 0, (
        f"llmesh.{sub} が任意 extra 無しで import できない "
        f"(その extra を base 依存にするか、import を構築時に遅らせる):\n{r.stderr}"
    )
