# Copyright (c) 2026 Kazufumi Furuse. Licensed under the Apache License, Version 2.0 (see LICENSE).
"""ロジットから答えを読む(生成しない)。

流れは 4 段:

1. 選択肢に 1 トークンの符号を割り当てる(`codes.build_codebook`)。
2. 次トークンの上位 K を取り、**符号の上だけで**正規化する。
3. 選択肢の**並び順を変えて 2 回**読み、確率を平均する —— 位置バイアスが消える。
4. 温度を当てて較正する(`calibrate` で当てた値をここで使う)。

★2 と 3 のあいだに 1 つ、正直に扱うべきものがある: **上位 K に現れなかった符号**。
確率を勝手に作らず、`unobserved` として名前で返し、`observed_mass`(符号が
占めた確率の和)も返す。`observed_mass` が小さい答えは「モデルは本当は別のことを
言いたかったのに、選択肢の中から選ばせた」状態で、確率を閾値に使ってはいけない。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from llmesh.decide.codes import TOP_LOGPROBS_CAP, CodeBook, build_codebook
from llmesh.decide.sources import LogitSource


@dataclass(frozen=True)
class Decision:
    """1 つの問いへの答え。

    Attributes:
        option: 最も確率の高い選択肢。
        probabilities: 選択肢 → 確率(合計 1)。
        confidence: `option` の確率。
        observed_mass: 上位 K のうち**符号が占めた確率の和**(0〜1)。
            小さいほど「本当は別のことを言いたかった」状態で、閾値に使えない。
        unobserved: 上位 K に一度も現れなかった選択肢。
        orders: 平均した並び順の数。
        temperature: 当てた温度。
        disagreement: 並び順ごとの最上位が食い違ったか(位置バイアスの実測)。
    """

    option: str
    probabilities: dict[str, float]
    confidence: float
    observed_mass: float
    unobserved: tuple[str, ...]
    orders: int
    temperature: float
    disagreement: bool


def _renormalise(logprobs: dict[str, float], book: CodeBook,
                 temperature: float) -> tuple[dict[str, float], float, tuple[str, ...]]:
    """符号の上だけで正規化する。返り値 = (選択肢→確率, 符号が占めた質量, 未観測)。"""
    seen = {c: logprobs[c] for c in book.codes if c in logprobs}
    unobserved = tuple(book.by_code[c] for c in book.codes if c not in seen)
    mass = float(sum(math.exp(v) for v in seen.values()))
    if not seen:
        # 1 つも見えないなら答えを作らない(fail-closed)
        return {}, 0.0, unobserved
    t = max(float(temperature), 1e-6)
    scaled = {c: v / t for c, v in seen.items()}
    top = max(scaled.values())
    exp = {c: math.exp(v - top) for c, v in scaled.items()}
    z = sum(exp.values())
    probs = {book.by_code[c]: e / z for c, e in exp.items()}
    for name in unobserved:
        probs[name] = 0.0
    return probs, mass, unobserved


def _prompt(state: str, instructions: str, options: tuple[str, ...],
            codes: tuple[str, ...], descriptions: dict[str, str] | None) -> str:
    lines = [c + " = " + o + (" — " + descriptions[o]
                              if descriptions and descriptions.get(o) else "")
             for c, o in zip(codes, options)]
    return ("State:\n{}\n\nQuestion: {}\n\nOptions:\n{}\n\n"
            "Reply with exactly one letter from the options above and nothing else.\n"
            "Answer:".format(state.strip(), instructions.strip(), "\n".join(lines)))


def ask_choice(source: LogitSource, state: str, instructions: str,
               options: list[str] | tuple[str, ...], *,
               descriptions: dict[str, str] | None = None,
               temperature: float = 1.0,
               orders: int = 2,
               cap: int = TOP_LOGPROBS_CAP) -> Decision:
    """選択肢の中から 1 つ選ぶ。生成トークンは 0。

    Args:
        source: 次トークンの対数確率を返す口。
        state: 判断の対象(本文・チケット・JSON など)。
        instructions: 何を判断するか。
        options: 選択肢名(宣言順)。
        descriptions: 選択肢名 → 補足説明。
        temperature: 較正で当てた温度(1.0 = 素)。
        orders: 並び順を何通り読むか。2 なら宣言順と逆順(位置バイアスが消える)。
        cap: 1 回の読み出しで許す選択肢数。

    Returns:
        `Decision`。選択肢の外は構造的に返らない —— ただし**正しさの保証ではない**。

    Raises:
        CodeError: 符号を割り当てられない。
        SourceError: 口が使えない / 応答の形が違う。
        ValueError: 上位 K に符号が 1 つも現れなかった(答えを作らない)。
    """
    opts = tuple(options)
    build_codebook(opts, cap=cap)
    k = min(max(len(opts) + 4, 5), cap)

    perms: list[tuple[str, ...]] = [opts]
    if orders >= 2:
        perms.append(tuple(reversed(opts)))
    for i in range(2, orders):                       # 3 通り以上は回転で作る
        perms.append(opts[i - 1:] + opts[: i - 1])

    acc: dict[str, float] = {o: 0.0 for o in opts}
    masses: list[float] = []
    unobs: set[str] = set()
    tops: list[str] = []
    for perm in perms:
        pb = build_codebook(perm, cap=cap)
        prompt = _prompt(state, instructions, perm, pb.codes, descriptions)
        lp = source.top_logprobs(prompt, k).as_dict()
        probs, mass, miss = _renormalise(lp, pb, temperature)
        if not probs:
            raise ValueError(
                f"上位 {k} に選択肢の符号が 1 つも現れなかった。答えを作らない ——"
                "符号が 1 トークンでないか、問いの形がモデルに通っていない")
        for name, p in probs.items():
            acc[name] += p
        masses.append(mass)
        unobs |= set(miss)
        tops.append(max(probs, key=lambda n: probs[n]))

    n = float(len(perms))
    probabilities = {o: acc[o] / n for o in opts}
    best = max(probabilities, key=lambda o: probabilities[o])
    return Decision(
        option=best,
        probabilities=probabilities,
        confidence=probabilities[best],
        observed_mass=float(sum(masses) / n),
        unobserved=tuple(sorted(unobs)),
        orders=len(perms),
        temperature=float(temperature),
        disagreement=len(set(tops)) > 1,
    )


def ask_yes_no(source: LogitSource, state: str, question: str, *,
               temperature: float = 1.0, orders: int = 2) -> Decision:
    """はい / いいえ。`probabilities["yes"]` が p(yes)。"""
    return ask_choice(source, state, question, ("yes", "no"),
                      temperature=temperature, orders=orders)


def ask_score(source: LogitSource, state: str, instructions: str,
              levels: list[str] | tuple[str, ...], *,
              temperature: float = 1.0, orders: int = 2) -> tuple[float, Decision]:
    """順序のある水準から**期待値**を返す。

    Returns:
        (期待水準 0 始まり, `Decision`)。水準は宣言順に 0,1,2,... と見なす。
    """
    d = ask_choice(source, state, instructions, levels,
                   temperature=temperature, orders=orders)
    ev = sum(i * d.probabilities[name] for i, name in enumerate(tuple(levels)))
    return float(ev), d
