# Copyright (c) 2026 Kazufumi Furuse. Licensed under the Apache License, Version 2.0 (see LICENSE).
"""次トークンの対数確率を取る口。

`LogitSource` は「プロンプトを渡すと、**次の 1 トークン**の上位 K 個の
(トークン文字列, 対数確率) を返す」だけの薄い口。答えを生成させないので、
何トークン出るかに依らず計算量が一定になる。

実装は 2 つ:

* `OpenAICompatLogitSource` —— OpenAI 互換 `/v1/chat/completions` の
  `logprobs` / `top_logprobs`。**Ollama 0.34.1 で実測動作**(上限 20)。
  on-prem のまま使えるので FullSense の原則(外部送信しない)を崩さない。
* `ScriptedLogitSource` —— 決め打ちの応答を返す試験用。CI はネットワークを
  使わないので、機構の試験はこちらで回す。

★応答は untrusted 扱い(llmesh の規約)。形が違えば `SourceError` にする。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol


class SourceError(RuntimeError):
    """LLM の口が使えない、または応答の形が契約と違う。"""


#: 許す scheme。``urlopen`` は ``file:`` も開くので、検証しないと
#: ``file:///etc/passwd`` を渡してローカルファイルを読ませられる(CWE-22)。
#: 設定や呼び出し側から来る値なので、信頼境界で必ず確かめる。
_ALLOWED_SCHEMES = frozenset({"http", "https"})


def check_base_url(base_url: str) -> str:
    """``base_url`` が http/https のホストつき URL であることを確かめて返す。

    Raises:
        SourceError: scheme が許されていない / ホストが無い。
            ★例外文に URL そのものは載せない(口の場所を漏らさない)。
    """
    try:
        parts = urllib.parse.urlsplit(str(base_url))
    except ValueError as exc:
        raise SourceError("base_url を解釈できない") from exc
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise SourceError(
            f"base_url の scheme {parts.scheme!r} は許していない(http / https のみ)。"
            "urlopen は file: も開くので、ここで止める")
    if not parts.netloc:
        raise SourceError("base_url にホストが無い")
    return str(base_url)


@dataclass(frozen=True)
class TopLogprobs:
    """次トークンの上位 K。

    Attributes:
        pairs: (トークン文字列, 対数確率) を確率の高い順に。
        model: 応答が名乗ったモデル名(監査用)。
    """

    pairs: tuple[tuple[str, float], ...]
    model: str = ""

    def as_dict(self) -> dict[str, float]:
        """同じトークンが 2 度出たら**高い方**を採る(応答は untrusted)。"""
        out: dict[str, float] = {}
        for tok, lp in self.pairs:
            if tok not in out or lp > out[tok]:
                out[tok] = lp
        return out


class LogitSource(Protocol):
    """次トークンの上位 K を返すもの。"""

    def top_logprobs(self, prompt: str, k: int) -> TopLogprobs:
        ...


# --------------------------------------------------------------------------- #
@dataclass
class OpenAICompatLogitSource:
    """OpenAI 互換 `/v1/chat/completions` から次トークンの上位 K を読む。

    Args:
        base_url: 例 ``http://127.0.0.1:11434/v1``(値はログに出さない)。
        model: モデル名。
        timeout: 秒。
        system: 任意の system メッセージ。
    """

    base_url: str
    model: str
    timeout: float = 120.0
    system: str = ""
    _calls: int = field(default=0, init=False, repr=False)

    @property
    def calls(self) -> int:
        """投げた回数(1 回 = 1 forward)。速度の主張を数える口。"""
        return self._calls

    def top_logprobs(self, prompt: str, k: int) -> TopLogprobs:
        msgs: list[dict[str, str]] = []
        if self.system:
            msgs.append({"role": "system", "content": self.system})
        msgs.append({"role": "user", "content": prompt})
        body = {
            "model": self.model,
            "messages": msgs,
            "max_tokens": 1,
            "temperature": 0,
            "logprobs": True,
            "top_logprobs": int(k),
        }
        url = check_base_url(self.base_url).rstrip("/") + "/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST")
        try:
            # scheme は check_base_url で http/https に限定済み
            # nosec B310 —— scheme は直前の check_base_url で http/https に限定
            # 済み(file: や独自 scheme は SourceError)。門 =
            # tests/test_decide.py::test_a_file_scheme_base_url_is_refused
            with urllib.request.urlopen(  # nosec B310
                    req, timeout=self.timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SourceError(f"LLM の口に届かない: {type(exc).__name__}") from exc
        self._calls += 1

        try:
            choice = raw["choices"][0]
            entry = choice["logprobs"]["content"][0]
            tops = entry.get("top_logprobs") or []
            pairs = tuple((str(t["token"]), float(t["logprob"])) for t in tops)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise SourceError(
                "応答に top_logprobs が無い。この backend は対数確率を返さない"
                f"(= この手法は使えない)。{type(exc).__name__}") from exc
        if not pairs:
            raise SourceError("top_logprobs が空だった")
        return TopLogprobs(pairs=pairs, model=str(raw.get("model", "")))

    def probe_single_token(self, codes: tuple[str, ...]) -> tuple[str, ...]:
        """符号が**そのまま 1 トークンとして返るか**を実測し、返らないものを挙げる。

        語彙は backend ごとに違うので、1 トークン性は仮定でなく実測で確かめる。
        符号を並べて訊き、上位 K に**その符号そのもの**が現れるかを見る。
        """
        listing = " ".join(f"{c}={c}" for c in codes)
        prompt = ("Reply with exactly one character from this list and nothing else.\n"
                  f"{listing}\nAnswer:")
        seen = set(self.top_logprobs(prompt, min(len(codes) + 5, 20)).as_dict())
        return tuple(c for c in codes if c not in seen)


# --------------------------------------------------------------------------- #
@dataclass
class ScriptedLogitSource:
    """試験用。プロンプトに含まれる印で応答を選ぶ決め打ちの口。

    Args:
        table: (プロンプトに含まれる文字列) → {トークン: 対数確率}。
        default: どれにも当たらないときの応答。
    """

    table: dict[str, dict[str, float]]
    default: dict[str, float] = field(default_factory=dict)
    seen: list[str] = field(default_factory=list)

    def top_logprobs(self, prompt: str, k: int) -> TopLogprobs:
        self.seen.append(prompt)
        for needle, dist in self.table.items():
            if needle in prompt:
                items = sorted(dist.items(), key=lambda kv: -kv[1])[:k]
                return TopLogprobs(pairs=tuple(items), model="scripted")
        if not self.default:
            raise SourceError("scripted の表に無いプロンプト(既定も無い)")
        items = sorted(self.default.items(), key=lambda kv: -kv[1])[:k]
        return TopLogprobs(pairs=tuple(items), model="scripted")
