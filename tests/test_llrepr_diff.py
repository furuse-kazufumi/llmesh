"""Tests for the llrepr typed diff/patch primitive (prediction-error)."""
from __future__ import annotations

from llmesh.llrepr import (
    Container,
    Document,
    Heading,
    ListNode,
    Text,
    apply_patch,
    diff_documents,
    prediction_error,
)
from llmesh.llrepr.diff import apply_ops, diff_dicts


def _doc(title: str, body: str) -> Document:
    return Document.of(
        Heading(level=2, children=[Text(text=title)]),
        Container(tag="block", children=[Text(text=body)]),
    )


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

def test_identical_documents_have_empty_diff():
    a = _doc("T", "body")
    assert diff_documents(a, _doc("T", "body")) == []
    assert prediction_error(diff_documents(a, a)) == 0


def test_leaf_change_round_trips():
    a = _doc("Title", "old body")
    b = _doc("Title", "new body")
    ops = diff_documents(a, b)
    assert ops  # non-empty
    assert apply_patch(a, ops).to_dict() == b.to_dict()


def test_added_child_round_trips():
    a = Document.of(Text(text="one"))
    b = Document.of(Text(text="one"), Text(text="two"))
    ops = diff_documents(a, b)
    assert apply_patch(a, ops).to_dict() == b.to_dict()


def test_removed_child_round_trips():
    a = Document.of(Text(text="one"), Text(text="two"), Text(text="three"))
    b = Document.of(Text(text="one"))
    ops = diff_documents(a, b)
    assert apply_patch(a, ops).to_dict() == b.to_dict()


def test_nested_list_change_round_trips():
    a = Document.of(ListNode(ordered=False, items=[[Text(text="a")], [Text(text="b")]]))
    b = Document.of(ListNode(ordered=True, items=[[Text(text="a")], [Text(text="B!")], [Text(text="c")]]))
    ops = diff_documents(a, b)
    assert apply_patch(a, ops).to_dict() == b.to_dict()


# ---------------------------------------------------------------------------
# Prediction-error magnitude (the point of the primitive)
# ---------------------------------------------------------------------------

def test_small_change_yields_small_diff():
    a = _doc("Pressure alarm", "Sensor S1 exceeded UCL.")
    # near-miss speculation: only the body wording differs
    b = _doc("Pressure alarm", "Sensor S1 exceeded the upper control limit.")
    ops = diff_documents(a, b)
    # Only one leaf replace should travel — the heading is unchanged.
    assert prediction_error(ops) == 1
    assert ops[0]["op"] == "replace"


def test_diff_dicts_and_apply_ops_directly():
    a = {"x": 1, "y": {"z": 2}}
    b = {"x": 1, "y": {"z": 3}, "w": 9}
    ops = diff_dicts(a, b)
    assert apply_ops(a, ops) == b


def test_whole_root_replace():
    # Replacing the entire value at root via an empty-path replace op.
    out = apply_ops({"a": 1}, [{"op": "replace", "path": "", "value": {"b": 2}}])
    assert out == {"b": 2}
