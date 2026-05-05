"""Tests for DataLevel and ClassifiedPayload."""
import pytest
from llmesh.classifier import DataLevel, ClassifiedPayload, combine_payloads


class TestDataLevel:
    def test_ordering(self):
        assert DataLevel.L0 < DataLevel.L1 < DataLevel.L4

    def test_public_p2p_allowed_for_l0_l1(self):
        assert DataLevel.L0.allows_public_p2p
        assert DataLevel.L1.allows_public_p2p

    def test_public_p2p_blocked_for_l2_plus(self):
        assert not DataLevel.L2.allows_public_p2p
        assert not DataLevel.L3.allows_public_p2p
        assert not DataLevel.L4.allows_public_p2p

    def test_trusted_nodes_allowed_for_l2(self):
        assert DataLevel.L2.allows_trusted_nodes

    def test_trusted_nodes_blocked_for_l3_plus(self):
        assert not DataLevel.L3.allows_trusted_nodes
        assert not DataLevel.L4.allows_trusted_nodes

    def test_labels(self):
        assert DataLevel.L0.label() == "Public"
        assert DataLevel.L4.label() == "Regulated/Secret"


class TestClassifiedPayload:
    def test_create_string_payload(self):
        p = ClassifiedPayload.create("hello", DataLevel.L0)
        assert p.data == "hello"
        assert p.level == DataLevel.L0
        assert len(p.sha256) == 64

    def test_create_dict_payload(self):
        p = ClassifiedPayload.create({"key": "val"}, DataLevel.L1, lineage=["origin"])
        assert p.data == {"key": "val"}
        assert p.lineage == ("origin",)

    def test_frozen_immutable(self):
        p = ClassifiedPayload.create("data", DataLevel.L0)
        with pytest.raises((AttributeError, TypeError)):
            p.level = DataLevel.L4  # type: ignore[misc]

    def test_with_decision(self):
        p = ClassifiedPayload.create("data", DataLevel.L1)
        p2 = p.with_decision("allowed")
        assert p2.policy_decision == "allowed"
        assert p2.level == p.level

    def test_reclassify_increases_level(self):
        p = ClassifiedPayload.create("secret", DataLevel.L1)
        p2 = p.reclassify(DataLevel.L3, "detected_pii")
        assert p2.level == DataLevel.L3
        assert "reclassify:detected_pii" in p2.lineage

    def test_reclassify_never_decreases(self):
        p = ClassifiedPayload.create("data", DataLevel.L3)
        p2 = p.reclassify(DataLevel.L0, "tried_to_downgrade")
        assert p2.level == DataLevel.L3

    def test_invalid_level_type(self):
        with pytest.raises(TypeError):
            ClassifiedPayload(
                data="x", level=99,  # type: ignore[arg-type]
                lineage=(), policy_decision="", sha256="a" * 64
            )


class TestCombinePayloads:
    def test_combine_takes_max_level(self):
        p0 = ClassifiedPayload.create("a", DataLevel.L0)
        p3 = ClassifiedPayload.create("b", DataLevel.L3)
        merged = combine_payloads(p0, p3)
        assert merged.level == DataLevel.L3

    def test_combine_merges_lineage(self):
        p0 = ClassifiedPayload.create("a", DataLevel.L0, lineage=["src_a"])
        p1 = ClassifiedPayload.create("b", DataLevel.L1, lineage=["src_b"])
        merged = combine_payloads(p0, p1)
        assert "src_a" in merged.lineage
        assert "src_b" in merged.lineage
        assert "combined" in merged.lineage

    def test_combine_single_payload(self):
        p = ClassifiedPayload.create("x", DataLevel.L2)
        merged = combine_payloads(p)
        assert merged.level == DataLevel.L2

    def test_combine_empty_raises(self):
        with pytest.raises(ValueError):
            combine_payloads()

    def test_combine_policy_is_reclassified(self):
        p = ClassifiedPayload.create("x", DataLevel.L0)
        merged = combine_payloads(p, p)
        assert merged.policy_decision == "reclassified_after_merge"
