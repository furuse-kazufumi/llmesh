# Copyright (c) 2026 Kazufumi Furuse. Licensed under the Apache License, Version 2.0 (see LICENSE).
"""llmesh.decide —— 生成せずに判断する層(label-token readout)。

**何をするか**: 選択肢に 1 トークンの符号を当て、次トークンのロジットから答えを
読む。トークンを 1 つも生成しないので、質問が 1 個でも 8 個でも、2 択でも 20 択でも
1 回の forward で済む。選択肢の外は構造的に返らない(JSON パースも再試行も不要)。
**ただしそれは構造の保証であって、正しさの保証ではない。**

**4 つの部品**:

* `build_codebook` —— 選択肢 → 1 トークン符号。重複・上限超過は拒否(fail-closed)。
* `OpenAICompatLogitSource` —— on-prem の OpenAI 互換口から対数確率を取る
  (Ollama 0.34.1 で実測。外部送信しない)。
* `ask_choice` / `ask_yes_no` / `ask_score` —— 並び順を変えて 2 回読み平均する
  (位置バイアスが消える)。
* `fit_temperature` / `reliability` —— 温度を当て、**較正できたかを測る**。

**使う前に読むこと**: 確率を閾値にして人へ上げるか自律で進めるかを分けるなら、
`reliability` を**自分のデータで**測ってからにする。温度は「全体的に自信過剰か
過小か」しか直せず、順位を変えないので**正解率は動かない**。また
`Decision.observed_mass` が小さい答えは「モデルは本当は別のことを言いたかったのに
選択肢から選ばせた」状態で、確率を閾値に使ってはいけない。

来歴: 手法は Interfaze AI の `lev`(2026-09-24 公開)のモデルカードが整理している
label-token readout を、**モデルに依存しない形で再実装**したもの。lev の重みは
学習データに非商用ライセンスのものを含むと公表されているので取り込まず、
手法だけを手元のモデルに適用する。
"""
from __future__ import annotations

from llmesh.decide.calibrate import (
    IsotonicCalibrator,
    Reliability,
    apply_temperature,
    fit_isotonic,
    fit_temperature,
    reliability,
)
from llmesh.decide.codes import (
    DEFAULT_ALPHABET,
    TOP_LOGPROBS_CAP,
    CodeBook,
    CodeError,
    build_codebook,
)
from llmesh.decide.readout import Decision, ask_choice, ask_score, ask_yes_no
from llmesh.decide.sources import (
    LogitSource,
    OpenAICompatLogitSource,
    ScriptedLogitSource,
    SourceError,
    TopLogprobs,
    check_base_url,
)

__all__ = [
    "DEFAULT_ALPHABET",
    "TOP_LOGPROBS_CAP",
    "CodeBook",
    "CodeError",
    "Decision",
    "IsotonicCalibrator",
    "LogitSource",
    "OpenAICompatLogitSource",
    "Reliability",
    "ScriptedLogitSource",
    "SourceError",
    "TopLogprobs",
    "apply_temperature",
    "ask_choice",
    "ask_score",
    "ask_yes_no",
    "build_codebook",
    "check_base_url",
    "fit_isotonic",
    "fit_temperature",
    "reliability",
]
