# Copyright (c) 2026 Kazufumi Furuse. Licensed under the Apache License, Version 2.0 (see LICENSE).
"""llmesh.decide を**実行で真値が取れる課題**で測る。

課題: 「この Python 断片は例外を投げるか。投げるなら何か」(8 択)。
★真値は**実際に走らせて**得る —— 人の判断ではなくオラクルである。断片はこの
ファイルが決定的に組み立てるので、外から来たコードは走らせていない。

測るもの:
  Q1 並び順の平均は正解率を上げるか(対のある McNemar 検定)
  Q2 位置バイアスの大きさ
  Q3 較正は**族を跨いで**転移するか(前半で当て、後半で測る)
  Q4 無作為分割ならどうか
  Q5 「自信が高いときだけ自律で進める」は成り立つか

実行:
    python examples/decide_exception_oracle.py
    # 既定 http://127.0.0.1:11434/v1 / llama3.1:latest
    # LLMESH_DECIDE_BASE / LLMESH_DECIDE_MODEL で変えられる

on-prem の LLM に届かなければ、何もせず理由を述べて終わる(0 を返す)。
"""
from __future__ import annotations

import io
import json
import math
import os
import random
import sys
import time
from collections import Counter
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llmesh.decide import (
    OpenAICompatLogitSource,
    SourceError,
    apply_temperature,
    fit_isotonic,
    fit_temperature,
    reliability,
)
from llmesh.decide.codes import build_codebook
from llmesh.decide.readout import _prompt, _renormalise

BASE = os.environ.get("LLMESH_DECIDE_BASE", "http://127.0.0.1:11434/v1")
MODEL = os.environ.get("LLMESH_DECIDE_MODEL", "llama3.1:latest")

EXC = ("none", "ZeroDivisionError", "IndexError", "KeyError",
       "TypeError", "NameError", "ValueError", "AttributeError")
QUESTION = ("Does running this Python code raise an exception, and which one? "
            "Choose 'none' if it runs to completion.")


# --------------------------------------------------------------------------- #
def snippets() -> list[str]:
    """雛形にパラメータを振る。例外の有無が**一目では分からない**形にしてある。"""
    out: list[str] = []
    for n in range(1, 9):
        for off in (-1, 0, 1):
            out.append("def f(a, b):\n    return a / (b + %d)\n"
                       "t = 0\nfor k in range(%d):\n    t += f(k, k)\n" % (off, n))
    for size in range(1, 9):
        for upto in range(1, 9):
            if abs(size - upto) <= 3:
                out.append("xs = list(range(%d))\ns = 0\n"
                           "for i in range(%d):\n    s += xs[i]\n" % (size, upto))
    for keys in ('"a", "c"', '"a", "b"', '"a", "c", "b"', '"c"', '"b"'):
        for extra in ("", ', "b": 2'):
            out.append(f'd = {{"a": 1, "c": 3{extra}}}\nt = 0\nfor k in ({keys}):\n    t += d[k]\n')
    for bad in range(5):
        items = ", ".join(('"%d"' % i) if i == bad else str(i) for i in range(4))
        out.append(f"xs = [{items}]\ns = 0\nfor x in xs:\n    s += x\n")
        out.append(f"xs = [{items}]\ns = 0\nfor x in xs:\n    s += int(x)\n")
    for pre in (True, False):
        for name in ("scale", "factor"):
            body = f"def g(v):\n    return v * {name}\n"
            out.append((f"{name} = 2\n" + body + "print(g(3))\n") if pre
                       else (body + f"print(g(3))\n{name} = 2\n"))
    for token in ("1", "x", "3.5", "-2", "1e3", " 4 ", "0x10"):
        out.append(f'vals = ["1", "2", "{token}"]\nn = 0\nfor v in vals:\n    n += int(v)\n')
        out.append(f'vals = ["1", "2", "{token}"]\nn = 0.0\nfor v in vals:\n    n += float(v)\n')
    for meth in ("upper", "uppercase", "strip", "trim", "title", "capitalise"):
        out.append(f's = "abc def"\nprint(s.{meth}())\n')
    for n in range(5):
        out.append("m = {}\nfor i in range(%d):\n    m[i] = m[i - 1] + 1\n" % n)
        out.append("m = {}\nfor i in range(%d):\n    m[i] = m.get(i - 1, 0) + 1\n" % n)
        out.append(f"def h(xs):\n    return sum(xs) / len(xs)\nprint(h({list(range(n))}))\n")
    for n in range(1, 7):
        for m in range(1, 7):
            out.append("a = list(range(%d))\nb = list(range(%d))\n"
                       "print([x + y for x, y in zip(a, b)])\n" % (n, m))
            out.append("a = list(range(%d))\nb = list(range(%d))\n"
                       "print([a[i] + b[i] for i in range(%d)])\n" % (n, m, max(n, m)))
        out.append('s = "abcdef"\nprint(s[%d])\n' % n)
        out.append("t = tuple(range(%d))\nprint(t[%d])\n" % (n, n))
        out.append("t = tuple(range(%d))\nprint(t[%d])\n" % (n, n - 1))
    for items2 in ('[3, 1, 2]', '[3, "1", 2]', '["b", "a"]', '[1, None]'):
        out.append(f"xs = {items2}\nprint(sorted(xs))\n")
        out.append(f"xs = {items2}\nprint(len(xs))\n")
    for meth in ("keys", "values", "items", "entries", "get"):
        out.append(f'd = {{"a": 1}}\nprint(list(d.{meth}()))\n')
    for meth in ("sort", "sorted", "reverse", "reversed"):
        out.append(f"xs = [2, 1]\nxs.{meth}()\nprint(xs)\n")
    for attr in ("append", "add", "push"):
        out.append(f"xs = [1, 2]\nxs.{attr}(3)\nprint(xs)\n")
        out.append(f"xs = {{1, 2}}\nxs.{attr}(3)\nprint(xs)\n")
    for div in range(-2, 4):
        out.append("xs = list(range(5))\nprint([x // %d for x in xs])\n" % div)
    for arg in ("2", "'2'", "None", "[2]", "2.0"):
        out.append(f"def area(w, h):\n    return w * h\nprint(area(3, {arg}) + 1)\n")
    for n in range(5):
        out.append("xs = list(range(%d))\nprint(max(xs))\n" % n)
        out.append("xs = list(range(%d))\nprint(max(xs, default=0))\n" % n)
    seen: set[str] = set()
    uniq: list[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def truth(code: str) -> str:
    """★実際に走らせて真値を得る。

    ``globals`` と ``locals`` を分けると内包表記が外の名前を見られず、**実在しない
    ``NameError``** が量産される(モジュール実行と挙動が違う)。実測で 45/292 が
    偽の NameError になっていたので、1 つの名前空間で走らせる。
    断片の標準出力は捨てる(測定ログを汚さない)。
    """
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            ns = {"__builtins__": __builtins__, "__name__": "__snippet__"}
            exec(compile(code, "<snippet>", "exec"), ns, ns)   # noqa: S102
        return "none"
    except Exception as exc:                      # noqa: BLE001
        name = type(exc).__name__
        return name if name in EXC else "OTHER"


def wilson(k: int, m: int) -> tuple[float, float]:
    """二項比率の 95% Wilson 区間。"""
    if m == 0:
        return (0.0, 0.0)
    z, p = 1.96, k / m
    den = 1 + z * z / m
    c = (p + z * z / (2 * m)) / den
    h = z * math.sqrt(p * (1 - p) / m + z * z / (4 * m * m)) / den
    return (max(0.0, c - h), min(1.0, c + h))


def _top(p: dict[str, float]) -> str:
    return max(p, key=lambda k: p[k])


# --------------------------------------------------------------------------- #
def main() -> int:
    codes = snippets()
    buf = io.StringIO()
    with redirect_stdout(buf):
        truths = [truth(c) for c in codes]
    keep = [i for i, t in enumerate(truths) if t != "OTHER"]
    codes = [codes[i] for i in keep]
    truths = [truths[i] for i in keep]
    n = len(codes)
    maj, majn = Counter(truths).most_common(1)[0]
    print("断片 %d 本 / 真値 %s" % (n, dict(Counter(truths).most_common())))
    print(f"多数派ラベルを常に答える基準線: {majn / n:.3f} ({maj})")

    src = OpenAICompatLogitSource(base_url=BASE, model=MODEL, timeout=180)
    fwd_book = build_codebook(EXC)
    rev_book = build_codebook(tuple(reversed(EXC)))
    fwd: list[dict[str, float]] = []
    rev: list[dict[str, float]] = []
    masses: list[float] = []
    t0 = time.perf_counter()
    try:
        for i, code in enumerate(codes, 1):
            lf = src.top_logprobs(_prompt(code, QUESTION, EXC, fwd_book.codes, None), 12)
            lr = src.top_logprobs(
                _prompt(code, QUESTION, tuple(reversed(EXC)), rev_book.codes, None), 12)
            pf, mf, _ = _renormalise(lf.as_dict(), fwd_book, 1.0)
            pr, mr, _ = _renormalise(lr.as_dict(), rev_book, 1.0)
            if not pf or not pr:
                print("符号が観測できなかった —— この backend ではこの手法が成立しない")
                return 0
            fwd.append(pf)
            rev.append(pr)
            masses.append(min(mf, mr))
            if i % 60 == 0:
                print("  %d/%d" % (i, n), flush=True)
    except SourceError as exc:
        print(f"on-prem の LLM に届かないので測定を行わない: {exc}")
        print(f"  ({BASE} / {MODEL} を確かめること)")
        return 0

    dt = time.perf_counter() - t0
    print("\n呼び出し %d 回 / %.1f 秒 = %.1f ms/回(1 回 = 1 forward、生成トークン 0)"
          % (src.calls, dt, 1000 * dt / max(src.calls, 1)))

    avg = [{o: (a.get(o, 0.0) + b.get(o, 0.0)) / 2.0 for o in EXC}
           for a, b in zip(fwd, rev)]
    ok_f = [_top(a) == t for a, t in zip(fwd, truths)]
    ok_r = [_top(a) == t for a, t in zip(rev, truths)]
    ok_a = [_top(a) == t for a, t in zip(avg, truths)]

    print("\n[Q1] 並び順の平均は正解率を上げるか")
    for label, ok in (("宣言順のみ", ok_f), ("逆順のみ", ok_r), ("2 通り平均", ok_a)):
        lo, hi = wilson(sum(ok), n)
        print("   %-12s %.3f [%.3f, %.3f]" % (label, sum(ok) / n, lo, hi))
    b = sum(1 for x, y in zip(ok_f, ok_a) if x and not y)
    c = sum(1 for x, y in zip(ok_f, ok_a) if y and not x)
    chi = (abs(b - c) - 1) ** 2 / (b + c) if (b + c) else 0.0
    print("   McNemar: 宣言順だけ当たり %d / 平均だけ当たり %d -> chi2=%.3f p=%.4f"
          % (b, c, chi, math.erfc(math.sqrt(chi / 2.0)) if chi > 0 else 1.0))

    print("\n[Q2] 位置バイアス")
    dis = sum(1 for a, b2 in zip(fwd, rev) if _top(a) != _top(b2))
    lo, hi = wilson(dis, n)
    print("   並びで最上位が入れ替わる %d/%d = %.3f [%.3f, %.3f]" % (dis, n, dis / n, lo, hi))
    for label, rows, first in ((f"宣言順({EXC[0]} が先頭)", fwd, EXC[0]),
                               (f"逆順({EXC[-1]} が先頭)", rev, EXC[-1])):
        share = sum(1 for p in rows if _top(p) == maj) / n
        fshare = sum(1 for p in rows if _top(p) == first) / n
        print("   %-28s %s を選ぶ %.3f / 先頭を選ぶ %.3f" % (label, maj, share, fshare))
    print(f"   真値で {maj} は {majn / n:.3f} —— ★並べ替えただけで多数派クラスを答える率が動く。")

    rows = [{k: math.log(max(v, 1e-12)) for k, v in a.items()} for a in avg]

    def report(title: str, fit: list[int], test: list[int]) -> None:
        print("\n[%s] 当て %d / 測り %d" % (title, len(fit), len(test)))
        t, _, _ = fit_temperature([rows[i] for i in fit], [truths[i] for i in fit])
        raw = [apply_temperature(rows[i], 1.0) for i in test]
        conf_raw = [p[_top(p)] for p in raw]
        ok = [_top(p) == truths[i] for p, i in zip(raw, test)]
        fit_raw = [apply_temperature(rows[i], 1.0) for i in fit]
        iso = fit_isotonic([p[_top(p)] for p in fit_raw],
                           [_top(p) == truths[i] for p, i in zip(fit_raw, fit)])
        variants = (("素(温度 1.0)", conf_raw),
                    (f"温度 {t:.3f}",
                     [apply_temperature(rows[i], t)[_top(apply_temperature(rows[i], t))]
                      for i in test]),
                    ("単調回帰", [iso(c) for c in conf_raw]))
        for label, conf in variants:
            r = reliability(conf, ok, n_bins=5)
            print("   %-14s ECE %.4f  MCE %.4f  平均自信 %.3f  正解率 %.3f"
                  % (label, r.ece, r.mce, r.mean_confidence, r.accuracy))

    half = n // 2
    report("Q3 族を跨いだ転移(前半で当て、後半で測る)", list(range(half)), list(range(half, n)))
    idx = list(range(n))
    random.Random(11).shuffle(idx)
    report("Q4 無作為分割", idx[:half], idx[half:])

    print("\n[Q5] 「自信が高いときだけ自律で進める」は成り立つか")
    conf_all = [a[_top(a)] for a in avg]
    for th in (0.5, 0.6, 0.7, 0.8, 0.9):
        sel = [i for i in range(n) if conf_all[i] >= th]
        if not sel:
            print(f"   閾値 {th:.1f}: 該当なし")
            continue
        k = sum(1 for i in sel if ok_a[i])
        lo, hi = wilson(k, len(sel))
        print("   閾値 %.1f: %3d 本を自律 (%2.0f%%) / 正解率 %.3f [%.3f, %.3f]"
              % (th, len(sel), 100 * len(sel) / n, k / len(sel), lo, hi))
    print("   observed_mass < 0.5: %d/%d" % (sum(1 for m in masses if m < 0.5), n))

    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "out")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "decide_exception_oracle_raw.json")
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"model": MODEL, "n": n,
                   "items": [{"truth": t, "fwd": a, "rev": b}
                             for t, a, b in zip(truths, fwd, rev)]},
                  fh, ensure_ascii=False, indent=1)
    print(f"\n素の結果を書いた: {out}(解析はこれだけで再現できる)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
