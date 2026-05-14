"""Tests for LLMeshSettings — load, save, set_value, as_table."""
import json

import pytest

from llmesh.config.settings import LLMeshSettings


class TestDefaults:
    def test_default_cb_failure_threshold(self):
        s = LLMeshSettings()
        assert s.cb_failure_threshold == 3

    def test_default_fairness_enabled(self):
        s = LLMeshSettings()
        assert s.fairness_enabled is True

    def test_default_fanout_k(self):
        s = LLMeshSettings()
        assert s.fanout_k == 1


class TestLoadSave:
    def test_load_missing_file_returns_defaults(self, tmp_path):
        s = LLMeshSettings.load(tmp_path / "no_such_file.json")
        assert s.cb_failure_threshold == 3

    def test_load_corrupt_file_returns_defaults(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text("not json {{{")
        s = LLMeshSettings.load(p)
        assert s.cb_failure_threshold == 3

    def test_save_and_reload(self, tmp_path):
        p = tmp_path / "settings.json"
        s = LLMeshSettings(cb_failure_threshold=7, fanout_k=3)
        s.save(p)
        s2 = LLMeshSettings.load(p)
        assert s2.cb_failure_threshold == 7
        assert s2.fanout_k == 3

    def test_save_produces_valid_json(self, tmp_path):
        p = tmp_path / "settings.json"
        LLMeshSettings().save(p)
        data = json.loads(p.read_text())
        assert "cb_failure_threshold" in data

    def test_load_ignores_unknown_keys(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"unknown_key": 99, "fanout_k": 5}))
        s = LLMeshSettings.load(p)
        assert s.fanout_k == 5
        assert not hasattr(s, "unknown_key")

    def test_partial_file_fills_missing_with_defaults(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"fanout_k": 2}))
        s = LLMeshSettings.load(p)
        assert s.fanout_k == 2
        assert s.cb_failure_threshold == 3  # default


class TestSetValue:
    def test_set_int_flat(self):
        s = LLMeshSettings()
        s.set_value("cb_failure_threshold", "5")
        assert s.cb_failure_threshold == 5

    def test_set_int_dotted(self):
        s = LLMeshSettings()
        s.set_value("cb.failure_threshold", "5")
        assert s.cb_failure_threshold == 5

    def test_set_float(self):
        s = LLMeshSettings()
        s.set_value("cb_recovery_timeout", "120.5")
        assert s.cb_recovery_timeout == pytest.approx(120.5)

    def test_set_bool_true_variants(self):
        for val in ("true", "True", "1", "yes", "on"):
            s = LLMeshSettings(fairness_enabled=False)
            s.set_value("fairness_enabled", val)
            assert s.fairness_enabled is True

    def test_set_bool_false_variants(self):
        for val in ("false", "False", "0", "no", "off"):
            s = LLMeshSettings(fairness_enabled=True)
            s.set_value("fairness_enabled", val)
            assert s.fairness_enabled is False

    def test_unknown_key_raises_key_error(self):
        s = LLMeshSettings()
        with pytest.raises(KeyError):
            s.set_value("nonexistent_key", "42")

    def test_bad_int_raises_value_error(self):
        s = LLMeshSettings()
        with pytest.raises(ValueError):
            s.set_value("fanout_k", "not_a_number")

    def test_set_persists_to_file(self, tmp_path):
        p = tmp_path / "settings.json"
        s = LLMeshSettings()
        s.set_value("fanout_k", "4")
        s.save(p)
        s2 = LLMeshSettings.load(p)
        assert s2.fanout_k == 4


class TestAsTable:
    def test_as_table_contains_all_keys(self):
        s = LLMeshSettings()
        table = s.as_table()
        assert "cb" in table
        assert "fairness" in table
        assert "fanout" in table

    def test_as_table_contains_values(self):
        s = LLMeshSettings(fanout_k=7)
        assert "7" in s.as_table()
