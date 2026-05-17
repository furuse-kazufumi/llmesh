"""Tests for llmesh.supply_chain.origins (deps --audit α)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llmesh.supply_chain import (
    Origins,
    OriginEntry,
    SupplyRisk,
    audit_installed,
    audit_requirements_file,
)
from llmesh.supply_chain.origins import origin_breakdown, risk_breakdown


def test_bundled_origins_load() -> None:
    """The bundled origin DB loads and contains expected packages."""
    o = Origins()
    names = set(o.names())
    # Sanity: at least a handful of well-known packages must be present.
    assert "fastapi" in names
    assert "litellm" in names
    assert "mindspore" in names
    assert "anthropic" in names


def test_origin_lookup_known_package() -> None:
    """Looking up a known package returns its bundled origin."""
    o = Origins()
    fa = o.lookup("fastapi")
    assert fa.origin == "US"
    assert fa.maintainer  # non-empty
    assert fa.verified  # has a verified date


def test_origin_lookup_unknown_package_returns_un() -> None:
    """Unknown packages fall back to UN, never guessed."""
    o = Origins()
    unk = o.lookup("definitely-not-a-real-package-xyz-123")
    assert unk.origin == "UN"


def test_origin_lookup_is_case_insensitive() -> None:
    """Case differences in package names don't break the lookup."""
    o = Origins()
    a = o.lookup("FastAPI")
    b = o.lookup("fastapi")
    assert a.origin == b.origin == "US"


def test_litellm_carries_supply_risk_medium() -> None:
    """LiteLLM's 2026-03 incident is reflected in the bundled DB."""
    o = Origins()
    le = o.lookup("litellm")
    assert le.origin == "US"
    assert le.supply_risk == "medium"
    assert "2026-03" in le.supply_risk_notes


def test_user_override_takes_precedence(tmp_path: Path) -> None:
    """User override file shadows the bundled DB."""
    override = tmp_path / "override.toml"
    override.write_text(
        '[fastapi]\norigin = "TRUSTED"\nmaintainer = "internal-approved"\n',
        encoding="utf-8",
    )
    o = Origins(override_path=override)
    fa = o.lookup("fastapi")
    assert fa.origin == "TRUSTED"
    assert fa.maintainer == "internal-approved"


def test_supply_risk_db_has_litellm_incident() -> None:
    """The bundled risk DB knows about the 2026-03 LiteLLM incident."""
    risk = SupplyRisk()
    incident = risk.get("litellm")
    assert incident is not None
    assert incident.severity == "MEDIUM"
    assert "1.82.7" in incident.affected_versions


def test_audit_installed_returns_sorted_unique() -> None:
    """audit_installed enumerates the current env without duplicates."""
    entries = audit_installed()
    assert len(entries) > 0
    names = [e.name.lower() for e in entries]
    assert names == sorted(names)
    assert len(names) == len(set(names))


def test_audit_requirements_file(tmp_path: Path) -> None:
    """A requirements.txt is parsed; pins / extras / comments are stripped."""
    req = tmp_path / "requirements.txt"
    req.write_text(
        "# regulated-bank example\n"
        "fastapi>=0.111\n"
        "litellm==1.83.0\n"
        "mindspore~=2.5\n"
        "qwen-agent\n"
        "\n"
        "-e ./local-pkg\n",
        encoding="utf-8",
    )
    entries = audit_requirements_file(req)
    names = {e.name.lower() for e in entries}
    assert names == {"fastapi", "litellm", "mindspore", "qwen-agent"}
    by_name = {e.name.lower(): e for e in entries}
    assert by_name["mindspore"].origin == "CN"
    assert by_name["qwen-agent"].origin == "CN"
    assert by_name["fastapi"].origin == "US"


def test_origin_breakdown() -> None:
    entries = [
        OriginEntry(name="a", origin="US"),
        OriginEntry(name="b", origin="US"),
        OriginEntry(name="c", origin="CN"),
        OriginEntry(name="d", origin="EU"),
    ]
    assert origin_breakdown(entries) == {"US": 2, "CN": 1, "EU": 1}


def test_risk_breakdown_defaults_to_low() -> None:
    entries = [
        OriginEntry(name="a", origin="US"),
        OriginEntry(name="b", origin="US", supply_risk="medium"),
        OriginEntry(name="c", origin="US", supply_risk="high"),
    ]
    breakdown = risk_breakdown(entries)
    assert breakdown["high"] == 1
    assert breakdown["medium"] == 1
    assert breakdown["low"] == 1


def test_cli_json_output_is_valid_json(tmp_path: Path, capsys) -> None:
    """The --json mode emits parseable JSON with expected schema."""
    from llmesh.cli.deps_audit import main

    req = tmp_path / "requirements.txt"
    req.write_text("fastapi>=0.111\nmindspore~=2.5\n", encoding="utf-8")
    exit_code = main(["--file", str(req), "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["summary"]["total"] == 2
    origin_map = {d["name"].lower(): d for d in payload["dependencies"]}
    assert origin_map["mindspore"]["origin"] == "CN"
    assert origin_map["fastapi"]["origin"] == "US"


def test_cli_fail_on_us(tmp_path: Path) -> None:
    """--fail-on US returns exit 1 when a US package is present."""
    from llmesh.cli.deps_audit import main

    req = tmp_path / "requirements.txt"
    req.write_text("fastapi>=0.111\n", encoding="utf-8")
    exit_code = main(["--file", str(req), "--fail-on", "US"])
    assert exit_code == 1


def test_cli_fail_on_us_passes_for_clean_input(tmp_path: Path) -> None:
    """--fail-on US returns exit 0 when no US package is present."""
    from llmesh.cli.deps_audit import main

    req = tmp_path / "requirements.txt"
    req.write_text("mindspore~=2.5\nqwen-agent\n", encoding="utf-8")
    exit_code = main(["--file", str(req), "--fail-on", "US"])
    assert exit_code == 0


def test_cli_table_output_includes_summary(tmp_path: Path, capsys) -> None:
    """Table output includes the per-origin summary block."""
    from llmesh.cli.deps_audit import main

    req = tmp_path / "requirements.txt"
    req.write_text("fastapi\nmindspore\n", encoding="utf-8")
    exit_code = main(["--file", str(req)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "PACKAGE" in out
    assert "ORIGIN" in out
    assert "Total dependencies" in out
    assert "Origin breakdown" in out


def test_litellm_risk_elevation_in_audit(tmp_path: Path, capsys) -> None:
    """LiteLLM appears with MEDIUM risk in audit output."""
    from llmesh.cli.deps_audit import main

    req = tmp_path / "requirements.txt"
    req.write_text("litellm==1.82.7\n", encoding="utf-8")
    exit_code = main(["--file", str(req), "--json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    le = payload["dependencies"][0]
    assert le["name"] == "litellm"
    assert le["supply_risk"] == "medium"
