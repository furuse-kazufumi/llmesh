"""Tests for the CLI subcommands: doctor / status / sbom (v2.7)."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from llmesh.cli.doctor import (
    run_doctor, DoctorReport, DoctorCheck, render_text as doctor_render,
)
from llmesh.cli.status import run_status, StatusSnapshot, render_text as status_render
from llmesh.cli.sbom import generate_sbom, write_sbom, _CDX_SPEC_VERSION, _purl_for


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------

class TestDoctor:
    def test_returns_report(self):
        rep = run_doctor(check_ports=False)
        assert isinstance(rep, DoctorReport)
        assert rep.python_version
        assert rep.platform
        assert rep.machine
        assert len(rep.checks) > 0

    def test_python_check_present(self):
        rep = run_doctor(check_ports=False)
        names = [c.name for c in rep.checks]
        assert any("python" in n for n in names)

    def test_required_packages_checked(self):
        rep = run_doctor(check_ports=False)
        names = " ".join(c.name for c in rep.checks)
        assert "required: cryptography" in names

    def test_optional_packages_checked(self):
        rep = run_doctor(check_ports=False)
        names = " ".join(c.name for c in rep.checks)
        # At least one optional package check is reported
        assert "optional:" in names

    def test_render_text(self):
        rep = run_doctor(check_ports=False)
        text = doctor_render(rep)
        assert "LLMesh doctor" in text
        assert ("HEALTHY" in text) or ("ISSUES" in text)

    def test_check_skip_ports(self):
        rep = run_doctor(check_ports=False)
        port_checks = [c for c in rep.checks if c.name.startswith("port ")]
        assert port_checks == []

    def test_doctor_check_dataclass(self):
        c = DoctorCheck(name="x", status="ok")
        assert c.detail == ""

    def test_to_dict_serialisable(self):
        rep = run_doctor(check_ports=False)
        d = rep.to_dict()
        # Must round-trip through json
        assert json.loads(json.dumps(d))["ok"] in (True, False)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class TestStatus:
    def test_returns_snapshot(self):
        s = run_status()
        assert isinstance(s, StatusSnapshot)
        assert s.python
        assert s.platform
        assert isinstance(s.adapters_importable, list)

    def test_rust_status_boolean(self):
        s = run_status()
        assert isinstance(s.rust_extension, bool)

    def test_render_text(self):
        s = run_status()
        text = status_render(s)
        assert "LLMesh" in text
        assert "Python" in text

    def test_to_dict_round_trips_json(self):
        s = run_status()
        out = json.dumps(s.to_dict())
        parsed = json.loads(out)
        assert parsed["python"] == s.python


# ---------------------------------------------------------------------------
# SBOM
# ---------------------------------------------------------------------------

class TestSBOM:
    def test_basic_structure(self):
        s = generate_sbom()
        assert s["bomFormat"] == "CycloneDX"
        assert s["specVersion"] == _CDX_SPEC_VERSION
        assert "serialNumber" in s
        assert isinstance(s["components"], list)

    def test_metadata_component(self):
        s = generate_sbom()
        assert s["metadata"]["component"]["name"] == "llmesh"
        assert "bom-ref" in s["metadata"]["component"]

    def test_components_have_purl(self):
        s = generate_sbom()
        # Some components should be present (cryptography, etc.)
        assert len(s["components"]) > 0
        for c in s["components"]:
            assert c["purl"].startswith("pkg:pypi/")

    def test_purl_helper(self):
        assert _purl_for("Foo", "1.2.3") == "pkg:pypi/foo@1.2.3"

    def test_write_sbom_roundtrip(self, tmp_path: Path):
        s = generate_sbom()
        out = tmp_path / "deep" / "sbom.cdx.json"
        write_sbom(out, s)
        assert out.exists()
        loaded = json.loads(out.read_text())
        assert loaded["specVersion"] == _CDX_SPEC_VERSION

    def test_components_have_licenses(self):
        s = generate_sbom()
        for c in s["components"]:
            licenses = c.get("licenses", [])
            assert len(licenses) > 0
            assert "license" in licenses[0]
