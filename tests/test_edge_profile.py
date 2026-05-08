"""Tests for EdgeProfile (v2.6 — エッジコンピュータ向け)."""
from __future__ import annotations

import pytest

from llmesh.industrial.edge_profile import (
    EdgePreset, apply_profile, detect_recommended_preset, list_profiles,
    _PROFILES, _MIN_SEEN_CAP, _MIN_DVS_BATCH,
)


class TestEdgePresets:
    def test_all_presets_defined(self):
        for preset in EdgePreset:
            assert preset in _PROFILES

    def test_micro_smaller_than_workstation(self):
        m = _PROFILES[EdgePreset.MICRO]
        w = _PROFILES[EdgePreset.WORKSTATION]
        assert m.seen_cap < w.seen_cap
        assert m.metrics_series_cap < w.metrics_series_cap
        assert m.dvs_max_events < w.dvs_max_events

    def test_presets_monotonically_grow(self):
        order = [EdgePreset.MICRO, EdgePreset.NANO,
                 EdgePreset.SMALL, EdgePreset.MEDIUM, EdgePreset.WORKSTATION]
        prev = _PROFILES[order[0]]
        for p in order[1:]:
            cur = _PROFILES[p]
            assert cur.seen_cap >= prev.seen_cap
            assert cur.metrics_series_cap >= prev.metrics_series_cap
            assert cur.dvs_max_events >= prev.dvs_max_events
            prev = cur


class TestApplyProfile:
    def test_apply_micro(self):
        p = apply_profile(EdgePreset.MICRO)
        from llmesh.industrial.sensor_3d import aoi_adapter
        assert aoi_adapter._SEEN_SET_MAX >= _MIN_SEEN_CAP
        assert p.name == "micro"
        # restore
        apply_profile(EdgePreset.WORKSTATION)

    def test_apply_clamps_to_minima(self):
        # Synthetic preset with sub-minimum values
        from llmesh.industrial.edge_profile import _Profile
        original = _PROFILES[EdgePreset.MICRO]
        try:
            _PROFILES[EdgePreset.MICRO] = _Profile(
                name="tiny", seen_cap=1, span_retention=1,
                metrics_series_cap=1, dvs_max_events=1,
                default_poll_s=0.1, description="too small",
            )
            apply_profile(EdgePreset.MICRO)
            from llmesh.industrial.sensor_3d import aoi_adapter
            assert aoi_adapter._SEEN_SET_MAX >= _MIN_SEEN_CAP
        finally:
            _PROFILES[EdgePreset.MICRO] = original
            apply_profile(EdgePreset.WORKSTATION)

    def test_apply_dvs_max_clamped(self):
        apply_profile(EdgePreset.MICRO)
        from llmesh.industrial.sensor_3d import event_adapter
        assert event_adapter._MAX_EVENTS_PER_BATCH >= _MIN_DVS_BATCH
        apply_profile(EdgePreset.WORKSTATION)

    def test_apply_metrics_cap(self):
        apply_profile(EdgePreset.NANO)
        from llmesh.industrial import metrics
        assert metrics._MAX_SERIES > 0
        apply_profile(EdgePreset.WORKSTATION)

    def test_unknown_preset_raises(self):
        with pytest.raises(ValueError):
            apply_profile("not-a-preset")  # type: ignore[arg-type]


class TestRecommend:
    def test_recommend_returns_known(self):
        p = detect_recommended_preset()
        assert p in EdgePreset


class TestListProfiles:
    def test_list_returns_all(self):
        d = list_profiles()
        for p in EdgePreset:
            assert p.value in d
            assert d[p.value]   # non-empty description
