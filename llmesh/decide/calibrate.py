# Copyright (c) 2026 Kazufumi Furuse. Licensed under the Apache License, Version 2.0 (see LICENSE).
"""温度を当てて確率を較正し、**較正できたかを測る**。

★確率を閾値に使う前に、その確率が当たっているかを確かめる必要がある。
「自信 0.9 と言った判断のうち、実際に 9 割が正しいか」—— これを測るのが
**信頼度図**と **ECE**(期待較正誤差)。llmesh / llive は判断を人へ上げるか
自律で進めるかを確率で分ける設計なので、ここを測らずに閾値を置くのは
責任所在の設計を短絡することになる。

温度 1 つで直せるのは「全体的に自信過剰 / 過小」だけである。順位は変わらないので
**正解率は動かない** —— 動くのは確率の当たり方だけ。そこは正直に書く。
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Reliability:
    """信頼度図の中身。

    Attributes:
        bins: (下限, 上限, 件数, 平均自信, 実測正解率) の並び。空の帯は入れない。
        ece: 期待較正誤差(件数で重み付けした |自信 - 正解率| の平均)。
        mce: 最大較正誤差。
        accuracy: 全体の正解率。
        mean_confidence: 全体の平均自信。
    """

    bins: tuple[tuple[float, float, int, float, float], ...]
    ece: float
    mce: float
    accuracy: float
    mean_confidence: float


def reliability(confidences: list[float], correct: list[bool],
                n_bins: int = 10) -> Reliability:
    """信頼度図と ECE を出す。

    Args:
        confidences: 各判断の自信(argmax の確率)。
        correct: 同じ順で、その判断が正しかったか。
        n_bins: 帯の数。

    Raises:
        ValueError: 長さが違う / 空。
    """
    if len(confidences) != len(correct):
        raise ValueError(
            f"自信 {len(confidences)} 件と正否 {len(correct)} 件で数が違う")
    if not confidences:
        raise ValueError("空では較正を測れない(★空を通す門にしない)")
    n = len(confidences)
    rows: list[tuple[float, float, int, float, float]] = []
    ece = 0.0
    mce = 0.0
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        idx = [i for i, c in enumerate(confidences)
               if (c > lo or (b == 0 and c >= lo)) and c <= hi]
        if not idx:
            continue
        conf = sum(confidences[i] for i in idx) / len(idx)
        acc = sum(1 for i in idx if correct[i]) / len(idx)
        gap = abs(conf - acc)
        ece += len(idx) / n * gap
        mce = max(mce, gap)
        rows.append((lo, hi, len(idx), conf, acc))
    return Reliability(
        bins=tuple(rows), ece=float(ece), mce=float(mce),
        accuracy=float(sum(1 for c in correct if c) / n),
        mean_confidence=float(sum(confidences) / n))


def _nll(logprob_rows: list[dict[str, float]], truths: list[str], t: float) -> float:
    """温度 t での負の対数尤度。符号でなく**選択肢名**の対数確率を受ける。"""
    total = 0.0
    for row, truth in zip(logprob_rows, truths):
        if truth not in row:
            # 真の選択肢が観測されていない行は尤度を作れない。落とさず罰を与える。
            total += 30.0
            continue
        scaled = {k: v / t for k, v in row.items()}
        top = max(scaled.values())
        z = sum(math.exp(v - top) for v in scaled.values())
        total -= (scaled[truth] - top - math.log(z))
    return total / max(len(truths), 1)


def fit_temperature(logprob_rows: list[dict[str, float]], truths: list[str], *,
                    lo: float = 0.05, hi: float = 20.0,
                    iterations: int = 60) -> tuple[float, float, float]:
    """負の対数尤度を最小にする温度を黄金分割で探す。

    Args:
        logprob_rows: 1 行 = {選択肢名: 対数確率}(正規化前)。
        truths: 同じ順の正解の選択肢名。
        lo, hi: 探索範囲。
        iterations: 反復。

    Returns:
        (温度, 温度 1 での NLL, 当てた温度での NLL)。
        ★**正解率は変わらない**(順位を保つ変換なので)。動くのは確率の当たり方だけ。

    Raises:
        ValueError: 行が空 / 数が合わない。
    """
    if len(logprob_rows) != len(truths):
        raise ValueError(
            f"行 {len(logprob_rows)} 件と正解 {len(truths)} 件で数が違う")
    if not logprob_rows:
        raise ValueError("空では温度を当てられない")
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = lo, hi
    c, d = b - gr * (b - a), a + gr * (b - a)
    fc, fd = _nll(logprob_rows, truths, c), _nll(logprob_rows, truths, d)
    for _ in range(iterations):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - gr * (b - a)
            fc = _nll(logprob_rows, truths, c)
        else:
            a, c, fc = c, d, fd
            d = a + gr * (b - a)
            fd = _nll(logprob_rows, truths, d)
    t = (a + b) / 2.0
    return float(t), _nll(logprob_rows, truths, 1.0), _nll(logprob_rows, truths, t)


def apply_temperature(row: dict[str, float], t: float) -> dict[str, float]:
    """1 行の対数確率に温度を当てて確率にする。"""
    tt = max(float(t), 1e-6)
    scaled = {k: v / tt for k, v in row.items()}
    top = max(scaled.values())
    exp = {k: math.exp(v - top) for k, v in scaled.items()}
    z = sum(exp.values())
    return {k: e / z for k, e in exp.items()}

# --------------------------------------------------------------------------- #
# 単調回帰(pool-adjacent-violators)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class IsotonicCalibrator:
    """自信 -> 較正後の確率 を、**単調非減少**に当てはめた折れ線。

    温度は 1 自由度なので「全体的に自信過剰 / 過小」しか直せない。実測では
    自信が低い帯で当たりすぎ、高い帯で外しすぎる(=交差する)ことがあり、
    そこは温度では届かない。単調回帰は隣接する違反を**併合**するので、
    逆転した高自信の帯を平らに潰して当てにいける。

    Attributes:
        xs: 区分の境目(自信、昇順)。
        ys: 各区分の較正後の確率(非減少)。
    """

    xs: tuple[float, ...]
    ys: tuple[float, ...]

    def __call__(self, confidence: float) -> float:
        """折れ線を線形補間して返す。範囲外は端の値で止める。"""
        c = float(confidence)
        if not self.xs:
            return c
        if c <= self.xs[0]:
            return self.ys[0]
        if c >= self.xs[-1]:
            return self.ys[-1]
        lo = 0
        hi = len(self.xs) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.xs[mid] <= c:
                lo = mid
            else:
                hi = mid
        span = self.xs[hi] - self.xs[lo]
        if span <= 0:
            return self.ys[hi]
        w = (c - self.xs[lo]) / span
        return float(self.ys[lo] * (1 - w) + self.ys[hi] * w)


def fit_isotonic(confidences: list[float], correct: list[bool]) -> IsotonicCalibrator:
    """自信 -> 正解率 の単調非減少な当てはめ(PAV)。

    Args:
        confidences: 各判断の自信。
        correct: 同じ順で、その判断が正しかったか。

    Raises:
        ValueError: 数が合わない / 空。
    """
    if len(confidences) != len(correct):
        raise ValueError(
            f"自信 {len(confidences)} 件と正否 {len(correct)} 件で数が違う")
    if not confidences:
        raise ValueError("空では当てはめられない(★空を通す門にしない)")
    order = sorted(range(len(confidences)), key=lambda i: confidences[i])
    xs = [confidences[i] for i in order]
    ys = [1.0 if correct[i] else 0.0 for i in order]

    # pool-adjacent-violators: 前より小さい塊が出たら、前と併合して平均にする
    blocks: list[tuple[float, float]] = []          # (値の和, 件数)
    bx: list[float] = []                            # 各塊の代表 x(末尾)
    for x, y in zip(xs, ys):
        blocks.append((y, 1.0))
        bx.append(x)
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s2, n2 = blocks.pop()
            s1, n1 = blocks.pop()
            blocks.append((s1 + s2, n1 + n2))
            bx.pop(-2)
    out_x: list[float] = []
    out_y: list[float] = []
    for (tot, cnt), x in zip(blocks, bx):
        out_x.append(float(x))
        out_y.append(float(tot / cnt))
    return IsotonicCalibrator(xs=tuple(out_x), ys=tuple(out_y))
