# Copyright (c) 2026 Kazufumi Furuse. Licensed under the Apache License, Version 2.0 (see LICENSE).
"""選択肢に 1 トークンの符号を割り当てる。

手法の要: **答えを生成させず、次トークンのロジットから読む**。そのためには
選択肢 1 つが**ちょうど 1 トークン**で表せる符号を持たねばならない。2 トークンに
割れる符号を混ぜると、その選択肢だけ確率が過小に出る(先頭トークンの確率しか
見えないため、後続で分岐する別の語と混ざる)。

★この repo の規律に従い **fail-closed**: 符号が作れない・重複する・上限を超える
場合は黙って切り詰めず `CodeError` を上げる。黙って落とすと「選ばれない選択肢」が
生まれ、それは出力を見ても分からない。
"""
from __future__ import annotations

from dataclasses import dataclass

#: 1 トークンになりやすい符号の候補。BPE 系の語彙では単独の英大文字・数字は
#: ほぼ確実に 1 トークンだが、**確実ではない**ので実測で確かめる口を別に用意する
#: (`llmesh.decide.sources.LogitSource.probe_single_token`)。
DEFAULT_ALPHABET: tuple[str, ...] = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

#: OpenAI 互換 API の `top_logprobs` は 20 が上限(Ollama 0.34.1 で実測。
#: 21 以上を要求すると 400 を返す)。1 回の forward で読める選択肢はここまで。
TOP_LOGPROBS_CAP = 20


class CodeError(ValueError):
    """符号を割り当てられない(重複・上限超過・空)。"""


@dataclass(frozen=True)
class CodeBook:
    """選択肢名 → 符号 の対応と、その逆引き。

    Attributes:
        options: 宣言順の選択肢名。
        codes: 同じ順の符号(1 文字)。
        by_code: 符号 → 選択肢名。
    """

    options: tuple[str, ...]
    codes: tuple[str, ...]
    by_code: dict[str, str]

    def code_of(self, option: str) -> str:
        return self.codes[self.options.index(option)]


def build_codebook(options: list[str] | tuple[str, ...],
                   alphabet: tuple[str, ...] = DEFAULT_ALPHABET,
                   cap: int = TOP_LOGPROBS_CAP) -> CodeBook:
    """選択肢に符号を割り当てる。

    Args:
        options: 選択肢名。重複不可、1 つ以上。
        alphabet: 使う符号の候補(既定は英大文字)。
        cap: 1 回の読み出しで許す選択肢数の上限。

    Raises:
        CodeError: 空 / 重複 / 上限超過 / 符号が足りない。
    """
    opts = tuple(options)
    if not opts:
        raise CodeError("選択肢が空。読み出す先が無い")
    if len(set(opts)) != len(opts):
        dup = sorted({o for o in opts if opts.count(o) > 1})
        raise CodeError(f"選択肢名が重複している: {dup}")
    if len(opts) > cap:
        raise CodeError(
            f"選択肢が {len(opts)} 個で、1 回の読み出しの上限 {cap} を超える。"
            "★切り詰めると「選ばれない選択肢」が黙って生まれるので拒否する。"
            "段に分けて訊くか、cap を上げられる backend を使うこと")
    if len(opts) > len(alphabet):
        raise CodeError(
            f"符号が足りない(選択肢 {len(opts)} / 符号 {len(alphabet)})")
    codes = tuple(alphabet[i] for i in range(len(opts)))
    return CodeBook(options=opts, codes=codes,
                    by_code={c: o for c, o in zip(codes, opts)})
