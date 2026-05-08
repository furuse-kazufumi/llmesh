"""Tests for SensorSummarizer — privacy-safe sensor payload condensation."""
from __future__ import annotations

import pytest
from llmesh.privacy.sensor_summarizer import (
    SensorSummarizer,
    SensorBlockedError,
    SensorSummary,
    _classify_topic,
    _summarise_pointcloud,
    _summarise_imu,
    _summarise_gps,
    _summarise_camera,
    _summarise_depth,
    _summarise_temperature,
    _summarise_sonar,
    SENSOR_CAMERA, SENSOR_DEPTH, SENSOR_LIDAR, SENSOR_IMU,
    SENSOR_GPS, SENSOR_TEMPERATURE, SENSOR_SONAR,
    SENSOR_FACE, SENSOR_ID_DOCUMENT, SENSOR_DIAGNOSTIC,
)


# ---------------------------------------------------------------------------
# Topic classification
# ---------------------------------------------------------------------------

class TestClassifyTopic:
    def test_camera_topics(self):
        for t in ["/camera/image_raw", "/rgb/image", "/color/image_rect"]:
            assert _classify_topic(t) == SENSOR_CAMERA, t

    def test_depth_topic(self):
        assert _classify_topic("/depth/image_rect") == SENSOR_DEPTH

    def test_lidar_topics(self):
        for t in ["/velodyne/points", "/lidar/scan", "/pointcloud"]:
            assert _classify_topic(t) == SENSOR_LIDAR, t

    def test_imu_topic(self):
        assert _classify_topic("/imu/data") == SENSOR_IMU

    def test_gps_topics(self):
        for t in ["/gps/fix", "/navsat/fix", "/gps/data"]:
            assert _classify_topic(t) == SENSOR_GPS, t

    def test_temperature_topic(self):
        assert _classify_topic("/temp/sensor") == SENSOR_TEMPERATURE

    def test_sonar_topic(self):
        assert _classify_topic("/sonar/range") == SENSOR_SONAR

    def test_face_topic(self):
        assert _classify_topic("/face_recognition/output") == SENSOR_FACE

    def test_id_document_topic(self):
        assert _classify_topic("/passport_scanner") == SENSOR_ID_DOCUMENT

    def test_unknown_defaults_to_diagnostic(self):
        assert _classify_topic("/arbitrary/topic") == SENSOR_DIAGNOSTIC


# ---------------------------------------------------------------------------
# Per-sensor summarisers
# ---------------------------------------------------------------------------

class TestSummariseCamera:
    def test_includes_dimensions_and_encoding(self):
        r = _summarise_camera({"width": 640, "height": 480, "encoding": "bgr8"})
        assert "640" in r and "480" in r and "bgr8" in r
        assert "pixel data withheld" in r

    def test_no_raw_pixels_in_output(self):
        r = _summarise_camera({"width": 1920, "height": 1080, "encoding": "rgb8",
                                "data": list(range(100))})
        assert "data" not in r.lower() or "withheld" in r


class TestSummariseDepth:
    def test_includes_dimensions(self):
        r = _summarise_depth({"width": 640, "height": 480})
        assert "640" in r and "480" in r
        assert "withheld" in r


class TestSummariseLidar:
    def test_basic(self):
        r = _summarise_pointcloud({"width": 1000, "height": 1})
        assert "1000" in r

    def test_with_ranges(self):
        r = _summarise_pointcloud({"ranges": [1.0, 2.0, 3.0, 4.0, 5.0]})
        assert "mean" in r
        assert "std" in r
        assert "min=1.000" in r
        assert "max=5.000" in r

    def test_empty_ranges(self):
        r = _summarise_pointcloud({"ranges": []})
        assert "no data" in r or "0" in r


class TestSummariseIMU:
    def test_with_all_fields(self):
        r = _summarise_imu({
            "orientation": {"x": 0.1, "y": 0.2, "z": 0.3, "w": 0.9},
            "linear_acceleration": {"x": 0.0, "y": 0.0, "z": 9.8},
            "angular_velocity": {"x": 0.01, "y": 0.0, "z": 0.0},
        })
        assert "orientation" in r
        assert "accel" in r
        assert "gyro" in r

    def test_empty_imu(self):
        r = _summarise_imu({})
        assert "no data" in r or "IMU" in r


class TestSummariseGPS:
    def test_anonymised_to_grid(self):
        r = _summarise_gps({"latitude": 35.6762, "longitude": 139.6503, "altitude": 40.0})
        assert "35°N" in r
        assert "139°E" in r
        assert "40" in r   # altitude

    def test_no_fine_coordinates(self):
        r = _summarise_gps({"latitude": 35.6762, "longitude": 139.6503})
        assert "35.6" not in r   # sub-degree precision stripped
        assert "139.6" not in r

    def test_unavailable(self):
        r = _summarise_gps({})
        assert "unavailable" in r


class TestSummariseTemperature:
    def test_basic(self):
        r = _summarise_temperature({"temperature": 36.5})
        assert "36.50" in r

    def test_with_variance(self):
        r = _summarise_temperature({"temperature": 100.0, "variance": 0.25})
        assert "variance" in r

    def test_no_reading(self):
        r = _summarise_temperature({})
        assert "no reading" in r


class TestSummariseSonar:
    def test_basic(self):
        r = _summarise_sonar({"range": 2.345})
        assert "2.345" in r

    def test_no_reading(self):
        r = _summarise_sonar({})
        assert "no reading" in r


# ---------------------------------------------------------------------------
# SensorSummarizer.summarize()
# ---------------------------------------------------------------------------

class TestSensorSummarizerSummarize:
    def setup_method(self):
        self.ss = SensorSummarizer()

    # --- L4 blocking ---

    def test_face_topic_blocked(self):
        result = self.ss.summarize("/face_recognition", {})
        assert result.blocked is True
        assert result.original_level == 4
        assert "L4" in result.block_reason

    def test_id_document_blocked(self):
        result = self.ss.summarize("/passport_scanner", {})
        assert result.blocked is True

    def test_explicit_face_type_blocked(self):
        result = self.ss.summarize("/any/topic", {}, sensor_type=SENSOR_FACE)
        assert result.blocked is True

    # --- Normal summarisation ---

    def test_camera_summarised(self):
        result = self.ss.summarize("/camera/image_raw", {"width": 640, "height": 480, "encoding": "bgr8"})
        assert result.blocked is False
        assert result.original_level == 3
        assert result.summary_level <= 1
        assert "640" in result.description
        assert "pixel data withheld" in result.description

    def test_lidar_summarised(self):
        result = self.ss.summarize("/lidar/scan", {"ranges": [1.0, 2.0, 3.0]})
        assert result.blocked is False
        assert result.original_level == 1
        assert "mean" in result.description

    def test_imu_summarised(self):
        result = self.ss.summarize("/imu/data", {
            "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
        })
        assert result.blocked is False
        assert "orientation" in result.description

    def test_gps_anonymised(self):
        result = self.ss.summarize("/gps/fix", {"latitude": 48.8566, "longitude": 2.3522})
        assert result.blocked is False
        assert "48°N" in result.description
        assert "2°E" in result.description

    def test_temperature_pass_through(self):
        result = self.ss.summarize("/temp/sensor", {"temperature": 22.5})
        assert result.blocked is False
        assert result.original_level == 0
        assert "22.50" in result.description

    def test_string_data(self):
        result = self.ss.summarize("/diagnostic/status", "system OK")
        assert result.blocked is False
        assert "system OK" in result.description

    # --- Sensor type override ---

    def test_override_sensor_type(self):
        result = self.ss.summarize("/ambiguous/topic", {"range": 1.5}, sensor_type=SENSOR_SONAR)
        assert result.blocked is False
        assert "1.5" in result.description

    # --- Summary level ---

    def test_summary_level_at_most_one(self):
        for topic, data in [
            ("/camera/image_raw", {"width": 640, "height": 480}),
            ("/depth/image", {"width": 320, "height": 240}),
            ("/lidar/scan", {"ranges": [1.0]}),
            ("/imu/data", {}),
        ]:
            result = self.ss.summarize(topic, data)
            if not result.blocked:
                assert result.summary_level <= 1, f"{topic}: level={result.summary_level}"

    # --- Description not None on success ---

    def test_description_nonempty_on_success(self):
        result = self.ss.summarize("/imu/data", {"linear_acceleration": {"x": 0, "y": 0, "z": 9.8}})
        assert not result.blocked
        assert len(result.description) > 0
