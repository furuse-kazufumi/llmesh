# Copyright (c) 2026 Kazufumi Furuse. Licensed under the Apache License, Version 2.0 (see LICENSE).
"""llmesh.decide —— 生成せずに判断する層の試験。

★ネットワークを使わない。決め打ちの口(`ScriptedLogitSource`)で**機構**を採点する:
符号の割り当て、符号の上だけでの正規化、並び順の平均で位置バイアスが消えること、
温度が順位を変えないこと、較正が測れること、そして**空や未観測で黙って答えを
作らないこと**。

実モデルに対する測定は `examples/decide_exception_oracle.py`(要 on-prem LLM)。
"""
from __future__ import annotations

import math

import pytest

from llmesh.decide import (
    CodeError,
    ScriptedLogitSource,
    SourceError,
    apply_temperature,
    ask_choice,
    ask_yes_no,
    build_codebook,
    fit_temperature,
    reliability,
)
from llmesh.decide.codes import TOP_LOGPROBS_CAP


# --------------------------------------------------------------------------- #
# 符号
# --------------------------------------------------------------------------- #
def test_codes_are_one_letter_and_in_declared_order() -> None:
    book = build_codebook(["refund", "tech", "sales"])
    assert book.codes == ("A", "B", "C")
    assert book.by_code == {"A": "refund", "B": "tech", "C": "sales"}
    assert book.code_of("tech") == "B"


def test_duplicate_options_are_refused() -> None:
    with pytest.raises(CodeError, match="重複"):
        build_codebook(["a", "b", "a"])


def test_an_empty_option_set_is_refused() -> None:
    with pytest.raises(CodeError, match="空"):
        build_codebook([])


def test_more_options_than_the_cap_are_refused_not_truncated() -> None:
    """★切り詰めると「絶対に選ばれない選択肢」が黙って生まれる。拒否が正しい。"""
    with pytest.raises(CodeError, match="上限"):
        build_codebook(["o%d" % i for i in range(TOP_LOGPROBS_CAP + 1)])


# --------------------------------------------------------------------------- #
# 読み出し
# --------------------------------------------------------------------------- #
def _src(dist: dict[str, float]) -> ScriptedLogitSource:
    return ScriptedLogitSource(table={}, default=dist)


def test_the_answer_is_read_from_the_logits_not_generated() -> None:
    """符号の上だけで正規化する —— 選択肢と無関係なトークンは効かない。"""
    src = _src({"A": math.log(0.6), "B": math.log(0.2), "The": math.log(0.2)})
    d = ask_choice(src, "state", "pick", ["alpha", "beta"], orders=1)
    assert d.option == "alpha"
    assert d.probabilities["alpha"] == pytest.approx(0.75)      # 0.6/(0.6+0.2)
    assert d.probabilities["beta"] == pytest.approx(0.25)
    assert d.observed_mass == pytest.approx(0.8)                # 符号が占めた質量


def test_a_low_observed_mass_is_reported_not_hidden() -> None:
    """★「本当は選択肢の外を言いたかった」状態が呼び出し側に見えること。"""
    src = _src({"A": math.log(0.02), "B": math.log(0.01), "Sorry": math.log(0.97)})
    d = ask_choice(src, "state", "pick", ["alpha", "beta"], orders=1)
    assert d.observed_mass < 0.05, d.observed_mass
    assert d.confidence > 0.6            # 符号の上では自信ありに見えてしまう
    assert d.option == "alpha"


def test_an_option_never_seen_is_named_and_gets_no_invented_probability() -> None:
    src = _src({"A": math.log(0.7), "B": math.log(0.3)})
    d = ask_choice(src, "state", "pick", ["alpha", "beta", "gamma"], orders=1)
    assert d.unobserved == ("gamma",)
    assert d.probabilities["gamma"] == 0.0


def test_no_code_observed_refuses_instead_of_guessing() -> None:
    """★1 つも見えないなら答えを作らない(fail-closed)。"""
    src = _src({"The": math.log(0.9), "Sorry": math.log(0.1)})
    with pytest.raises(ValueError, match="答えを作らない"):
        ask_choice(src, "state", "pick", ["alpha", "beta"], orders=1)


def test_a_backend_without_logprobs_says_so() -> None:
    src = ScriptedLogitSource(table={}, default={})
    with pytest.raises(SourceError):
        ask_yes_no(src, "state", "is it so?")


# --------------------------------------------------------------------------- #
# 並び順の平均 —— 位置バイアス
# --------------------------------------------------------------------------- #
def test_averaging_two_orders_cancels_a_pure_position_bias() -> None:
    """★先頭を好む癖しか無い口では、平均すると引き分けに戻る。

    どちらの並びでも「A」(= 先頭の選択肢)に 0.8 を置く口を作る。1 通りだと常に
    先頭が勝つが、2 通り平均すると 0.5 対 0.5 になる —— 位置の情報しか無いことが
    数字に出る。
    """
    src = _src({"A": math.log(0.8), "B": math.log(0.2)})
    one = ask_choice(src, "state", "pick", ["alpha", "beta"], orders=1)
    two = ask_choice(src, "state", "pick", ["alpha", "beta"], orders=2)
    assert one.probabilities["alpha"] == pytest.approx(0.8)
    assert two.probabilities["alpha"] == pytest.approx(0.5)
    assert two.probabilities["beta"] == pytest.approx(0.5)
    assert two.disagreement is True, "並びで最上位が入れ替わったのに記録されていない"
    assert two.orders == 2


def test_a_real_signal_survives_the_averaging() -> None:
    """内容で決まっている口なら、平均しても答えは変わらない。

    `ScriptedLogitSource` はプロンプトに含まれる印で応答を選ぶので、
    「beta の符号」に質量を置く表を 2 通りぶん用意する。
    """
    src = ScriptedLogitSource(table={
        "A = alpha": {"B": math.log(0.9), "A": math.log(0.1)},   # 宣言順: beta=B
        "A = beta": {"A": math.log(0.9), "B": math.log(0.1)},    # 逆順:   beta=A
    })
    d = ask_choice(src, "state", "pick", ["alpha", "beta"], orders=2)
    assert d.option == "beta"
    assert d.probabilities["beta"] == pytest.approx(0.9)
    assert d.disagreement is False


def test_the_prompt_lists_every_option_with_its_code() -> None:
    src = _src({"A": math.log(0.5), "B": math.log(0.3), "C": math.log(0.2)})
    ask_choice(src, "the state", "the question", ["x", "y", "z"], orders=1)
    prompt = src.seen[-1]
    for code, name in (("A", "x"), ("B", "y"), ("C", "z")):
        assert f"{code} = {name}" in prompt
    assert "the state" in prompt and "the question" in prompt


# --------------------------------------------------------------------------- #
# 温度と較正
# --------------------------------------------------------------------------- #
def test_temperature_does_not_change_the_ranking() -> None:
    """★順位を保つ変換なので**正解率は動かない**。動いたら実装が間違っている。"""
    row = {"a": math.log(0.5), "b": math.log(0.3), "c": math.log(0.2)}
    for t in (0.2, 0.5, 1.0, 3.0, 10.0):
        p = apply_temperature(row, t)
        assert max(p, key=lambda k: p[k]) == "a"
        assert sum(p.values()) == pytest.approx(1.0)


def test_temperature_below_one_sharpens_and_above_one_flattens() -> None:
    row = {"a": math.log(0.5), "b": math.log(0.5 / 3)}
    sharp = apply_temperature(row, 0.5)["a"]
    flat = apply_temperature(row, 4.0)["a"]
    assert sharp > apply_temperature(row, 1.0)["a"] > flat > 0.5


def test_fitting_recovers_a_known_temperature() -> None:
    """★真値つきの検算 —— 既知の係数で平坦にした分布から、戻す温度を取り戻す。

    向きに注意: `apply_temperature(row, t)` は対数確率を t で**割る**ので、t>1 は
    平坦化、t<1 は鋭利化である。モデルが真の分布を係数 F だけ平坦にした値
    (``logp / F``)を返すなら、元へ戻す温度は **1/F**。最初この試験は F そのものを
    期待して落ちた —— 実装ではなく期待値が間違っていた。
    """
    import random

    rng = random.Random(7)
    flatten = 2.5                     # モデルは真の分布をこれだけ平坦に出す
    want_t = 1.0 / flatten            # したがって元へ戻す温度はこれ
    base = [{"a": math.log(0.7), "b": math.log(0.2), "c": math.log(0.1)},
            {"a": math.log(0.2), "b": math.log(0.7), "c": math.log(0.1)},
            {"a": math.log(0.1), "b": math.log(0.2), "c": math.log(0.7)}]
    rows: list[dict[str, float]] = []
    truths: list[str] = []
    for _ in range(600):
        r = rng.choice(base)
        rows.append({k: v / flatten for k, v in r.items()})
        p = apply_temperature(r, 1.0)
        truths.append(rng.choices(list(p), weights=[p[n] for n in p])[0])
    t, nll1, nllt = fit_temperature(rows, truths)
    assert abs(t - want_t) < 0.12, f"温度 {t:.3f}(期待 {want_t:.3f} = 1/{flatten:.1f})"
    assert nllt < nll1, "当てた温度で尤度が改善していない"


def test_reliability_reports_a_miscalibrated_classifier() -> None:
    """自信 0.9 と言い続けて半分外す分類器は ECE が大きい。"""
    conf = [0.9] * 100
    ok = [True] * 50 + [False] * 50
    rel = reliability(conf, ok, n_bins=10)
    assert rel.accuracy == pytest.approx(0.5)
    assert rel.mean_confidence == pytest.approx(0.9)
    assert rel.ece == pytest.approx(0.4, abs=1e-9)
    assert rel.mce == pytest.approx(0.4, abs=1e-9)


def test_reliability_refuses_an_empty_input() -> None:
    """★空を通す門にしない。"""
    with pytest.raises(ValueError, match="空"):
        reliability([], [])


def test_reliability_refuses_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="数が違う"):
        reliability([0.5, 0.6], [True])


def test_a_perfectly_calibrated_classifier_has_near_zero_ece() -> None:
    """★門を壊して確かめる —— 較正できている側で ECE が小さくなること。"""
    conf: list[float] = []
    ok: list[bool] = []
    for p, n in ((0.55, 200), (0.75, 200), (0.95, 200)):
        conf += [p] * n
        ok += [True] * round(p * n) + [False] * (n - round(p * n))
    rel = reliability(conf, ok, n_bins=10)
    assert rel.ece < 0.01, rel.ece


# --------------------------------------------------------------------------- #
# 点数(順序のある水準)
# --------------------------------------------------------------------------- #
def test_score_returns_an_expected_level() -> None:
    from llmesh.decide import ask_score
    src = _src({"A": math.log(0.5), "B": math.log(0.5)})
    ev, d = ask_score(src, "state", "how good?", ["low", "mid", "high"], orders=1)
    assert d.unobserved == ("high",)
    assert ev == pytest.approx(0.5)          # 0*0.5 + 1*0.5 + 2*0.0


# --------------------------------------------------------------------------- #
# 単調回帰
# --------------------------------------------------------------------------- #
def test_isotonic_pools_a_crossing_that_temperature_cannot_fix() -> None:
    """★温度は単調 1 自由度なので、自信と正解率が**逆転**する帯を直せない。

    実測(llama3.1:8b / 例外 8 択)で信頼度図が交差した —— 自信 0.49 で実測 0.81、
    自信 0.88 で実測 0.46。単調回帰は隣接する違反を併合できるので、そこを平らに
    潰して当てにいける。ここは作り物で、その性質だけを確かめる。
    """
    from llmesh.decide import fit_isotonic
    conf: list[float] = []
    ok: list[bool] = []
    for c, acc, m in ((0.36, 0.70, 60), (0.49, 0.81, 120),
                      (0.67, 0.71, 60), (0.88, 0.46, 40)):
        conf += [c] * m
        ok += [True] * round(acc * m) + [False] * (m - round(acc * m))
    iso = fit_isotonic(conf, ok)
    assert list(iso.ys) == sorted(iso.ys), "単調非減少になっていない"
    raw = reliability(conf, ok, n_bins=5)
    fitted = reliability([iso(c) for c in conf], ok, n_bins=5)
    assert fitted.ece < raw.ece / 5.0, (raw.ece, fitted.ece)
    t, _, _ = fit_temperature(
        [{"a": math.log(c), "b": math.log(1 - c)} for c in conf],
        ["a" if o else "b" for o in ok])
    tempered = reliability(
        [apply_temperature({"a": math.log(c), "b": math.log(1 - c)}, t)["a"]
         for c in conf], ok, n_bins=5)
    assert tempered.ece > fitted.ece, (
        "温度が単調回帰と同じだけ直せてしまった —— 交差を含む探針になっていない")


def test_isotonic_is_monotone_and_clamped_outside_its_range() -> None:
    from llmesh.decide import fit_isotonic
    iso = fit_isotonic([0.3, 0.5, 0.7, 0.9], [False, True, True, True])
    assert iso(0.0) == pytest.approx(iso.ys[0])
    assert iso(1.0) == pytest.approx(iso.ys[-1])
    prev = -1.0
    for x in [i / 50 for i in range(51)]:
        cur = iso(x)
        assert cur >= prev - 1e-12, f"単調でない点がある at {x:.2f}"
        prev = cur


def test_isotonic_refuses_empty_and_mismatched_input() -> None:
    """★空を通す門にしない。"""
    from llmesh.decide import fit_isotonic
    with pytest.raises(ValueError, match="空"):
        fit_isotonic([], [])
    with pytest.raises(ValueError, match="数が違う"):
        fit_isotonic([0.5], [True, False])
# --- HTTP の口(応答は untrusted 扱い)------------------------------------- #
# 追記: llmesh の規約「backend の応答は検証を通るまで untrusted」に沿って、
# 形の違う応答・届かない口・重複トークンを**拒否 or 正規化**することを確かめる。
# ネットワークは使わず urlopen を差し替える。


class _FakeResp:
    def __init__(self, payload: bytes) -> None:
        self._p = payload

    def read(self) -> bytes:
        return self._p

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *a: object) -> bool:
        return False


def _patch_urlopen(monkeypatch, payload: object, exc: Exception | None = None) -> list:
    import json as _json
    import urllib.request

    seen: list = []

    def fake(req, timeout=None):  # noqa: ANN001, ARG001
        seen.append(_json.loads(req.data.decode("utf-8")))
        if exc is not None:
            raise exc
        return _FakeResp(_json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return seen


def _payload(tops: list[tuple[str, float]], model: str = "fake-1b") -> dict:
    return {"model": model,
            "choices": [{"logprobs": {"content": [
                {"token": tops[0][0], "logprob": tops[0][1],
                 "top_logprobs": [{"token": t, "logprob": lp} for t, lp in tops]}]}}]}


def test_the_http_source_asks_for_one_token_and_no_sampling(monkeypatch) -> None:
    """★1 トークンだけ・温度 0・logprobs 要求。生成させないことが要件。"""
    from llmesh.decide import OpenAICompatLogitSource

    seen = _patch_urlopen(monkeypatch, _payload([("A", -0.1), ("B", -2.0)]))
    src = OpenAICompatLogitSource(base_url="http://example.invalid/v1", model="m")
    got = src.top_logprobs("prompt", 7)
    assert got.as_dict() == {"A": -0.1, "B": -2.0}
    assert got.model == "fake-1b"
    assert src.calls == 1
    body = seen[0]
    assert body["max_tokens"] == 1
    assert body["temperature"] == 0
    assert body["logprobs"] is True
    assert body["top_logprobs"] == 7
    assert body["messages"][-1]["content"] == "prompt"


def test_a_duplicate_token_keeps_the_higher_logprob(monkeypatch) -> None:
    """応答は untrusted —— 同じトークンが 2 度来たら高い方を採る。"""
    from llmesh.decide import OpenAICompatLogitSource

    _patch_urlopen(monkeypatch, _payload([("A", -3.0), ("A", -0.5), ("B", -1.0)]))
    src = OpenAICompatLogitSource(base_url="http://example.invalid/v1", model="m")
    assert src.top_logprobs("p", 5).as_dict() == {"A": -0.5, "B": -1.0}


def test_a_response_without_logprobs_is_refused(monkeypatch) -> None:
    """★対数確率を返さない backend では、この手法は成立しない。黙って進まない。"""
    from llmesh.decide import OpenAICompatLogitSource, SourceError

    _patch_urlopen(monkeypatch, {"choices": [{"message": {"content": "A"}}]})
    src = OpenAICompatLogitSource(base_url="http://example.invalid/v1", model="m")
    with pytest.raises(SourceError, match="対数確率"):
        src.top_logprobs("p", 5)


def test_an_empty_top_logprobs_is_refused(monkeypatch) -> None:
    from llmesh.decide import OpenAICompatLogitSource, SourceError

    _patch_urlopen(monkeypatch, {"choices": [{"logprobs": {"content": [
        {"token": "A", "logprob": -0.1, "top_logprobs": []}]}}]})
    src = OpenAICompatLogitSource(base_url="http://example.invalid/v1", model="m")
    with pytest.raises(SourceError, match="空"):
        src.top_logprobs("p", 5)


def test_an_unreachable_endpoint_is_refused_without_leaking_the_url(monkeypatch) -> None:
    """★届かないときも、例外文に口の場所を載せない。"""
    import urllib.error

    from llmesh.decide import OpenAICompatLogitSource, SourceError

    _patch_urlopen(monkeypatch, {}, exc=urllib.error.URLError("refused"))
    src = OpenAICompatLogitSource(base_url="http://secret-host.invalid/v1", model="m")
    with pytest.raises(SourceError) as ei:
        src.top_logprobs("p", 5)
    assert "secret-host" not in str(ei.value)
    assert src.calls == 0, "失敗した呼び出しを数に入れている"


def test_probe_single_token_names_codes_the_tokenizer_splits(monkeypatch) -> None:
    """★符号の 1 トークン性は仮定でなく実測で確かめる口。"""
    from llmesh.decide import OpenAICompatLogitSource

    _patch_urlopen(monkeypatch, _payload([("A", -0.1), ("B", -1.0), ("D", -2.0)]))
    src = OpenAICompatLogitSource(base_url="http://example.invalid/v1", model="m")
    assert src.probe_single_token(("A", "B", "C", "D")) == ("C",)


def test_a_system_message_is_passed_through(monkeypatch) -> None:
    from llmesh.decide import OpenAICompatLogitSource

    seen = _patch_urlopen(monkeypatch, _payload([("A", -0.1)]))
    src = OpenAICompatLogitSource(base_url="http://example.invalid/v1", model="m",
                                  system="be terse")
    src.top_logprobs("p", 3)
    assert seen[0]["messages"][0] == {"role": "system", "content": "be terse"}


def test_a_file_scheme_base_url_is_refused(monkeypatch) -> None:
    """★`urlopen` は `file:` も開く。設定から来る値なので信頼境界で止める(CWE-22)。

    止めないと `base_url="file:///etc/passwd"` でローカルファイルを読ませられる。
    bandit B310 の指摘をコメントで黙らせず、検証で閉じた。
    """
    from llmesh.decide import OpenAICompatLogitSource, SourceError, check_base_url

    for bad in ("file:///etc/passwd", "ftp://host/x", "gopher://h", "data:text/plain,x"):
        with pytest.raises(SourceError, match="scheme"):
            check_base_url(bad)
    with pytest.raises(SourceError, match="ホスト"):
        check_base_url("http:///no-host")
    for good in ("http://127.0.0.1:11434/v1", "https://example.test/v1"):
        assert check_base_url(good) == good

    # 口そのものも、呼ばれた時点で拒否する(urlopen まで届かない)
    called: list[int] = []
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: called.append(1))       # noqa: ARG005
    src = OpenAICompatLogitSource(base_url="file:///etc/passwd", model="m")
    with pytest.raises(SourceError, match="scheme"):
        src.top_logprobs("p", 3)
    assert not called, "検証前に urlopen を呼んでいる"
