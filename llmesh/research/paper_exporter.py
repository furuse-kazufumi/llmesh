"""Paper exporter — JSONL trace → CSV + SVG figure + paper bundle (Phase 7).

Reads :class:`llmesh.core.TraceLogger` JSONL files and emits the
artefacts a researcher needs to drop into a paper:

- ``runs.csv``         — one row per agent.run / tool.call / evaluation
- ``metrics.csv``      — one row per (run_id, metric, value)
- ``timing.svg``       — bar chart of agent / tool durations from the trace
- ``paper_bundle.md``  — Markdown summary that links the above

All output is dependency-free: SVG is hand-rolled as text, CSV is
stdlib ``csv``, Markdown is plain string formatting. The
``run_research_pipeline`` already records what the exporter consumes
(see :mod:`llmesh.research.e2e`).
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExportBundle:
    """Filesystem paths for the exported artefacts."""

    out_dir: Path
    runs_csv: Path
    metrics_csv: Path
    timing_svg: Path
    paper_md: Path
    n_entries: int = 0
    metrics_emitted: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# trace iteration
# ---------------------------------------------------------------------------


def iter_trace(path: Path) -> Iterable[dict[str, Any]]:
    """Yield one parsed JSONL entry at a time.

    Malformed lines (e.g. a half-written final line after a crash) are
    skipped — the trace logger comments document this expectation.
    """
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue


# ---------------------------------------------------------------------------
# CSV exporters
# ---------------------------------------------------------------------------


_RUN_COLUMNS = ("run_id", "seq", "timestamp", "actor", "kind")


def export_runs_csv(entries: list[dict[str, Any]], out: Path) -> int:
    """Emit one row per trace entry; returns rows written."""
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_RUN_COLUMNS)
        for e in entries:
            writer.writerow([str(e.get(c, "")) for c in _RUN_COLUMNS])
            n += 1
    return n


def export_metrics_csv(entries: list[dict[str, Any]], out: Path) -> dict[str, float]:
    """Pull metrics out of every entry; one row per (run_id, metric, value).

    Returns a dict of the **latest** value per metric for the caller
    (so the paper Markdown can show a final-numbers summary).
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    latest: dict[str, float] = {}
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(("run_id", "seq", "metric", "value"))
        for e in entries:
            metrics = e.get("metrics") or {}
            # Also surface metrics stored under ``output_payload.metrics``
            # (the executor uses this shape for run-level totals).
            output = e.get("output_payload") or {}
            output_metrics = output.get("metrics") if isinstance(output, dict) else None
            if isinstance(output_metrics, dict):
                metrics = {**metrics, **output_metrics}
            for name, value in metrics.items():
                if not isinstance(value, (int, float)):
                    continue
                writer.writerow([e.get("run_id", ""), e.get("seq", ""), name, float(value)])
                latest[name] = float(value)
    return latest


# ---------------------------------------------------------------------------
# SVG bar chart (no matplotlib)
# ---------------------------------------------------------------------------


def render_timing_svg(entries: list[dict[str, Any]], out: Path) -> int:
    """Render a horizontal bar chart of duration_ms per entry.

    Returns the bar count. Entries without ``metrics.duration_ms`` or
    ``output_payload.total_duration_ms`` are skipped. SVG is written
    even when no bars qualify (so the bundle path always exists).
    """
    rows: list[tuple[str, float]] = []
    for e in entries:
        actor = str(e.get("actor", ""))
        kind = str(e.get("kind", ""))
        label = f"{actor} ({kind})"[:48]
        ms = _extract_duration_ms(e)
        if ms is None or ms <= 0:
            continue
        rows.append((label, ms))
    out.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        out.write_text(_empty_svg("no timing data"), encoding="utf-8")
        return 0
    out.write_text(_bar_chart_svg(rows), encoding="utf-8")
    return len(rows)


def _extract_duration_ms(entry: dict[str, Any]) -> float | None:
    metrics = entry.get("metrics") or {}
    if isinstance(metrics, dict):
        v = metrics.get("duration_ms")
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    output = entry.get("output_payload") or {}
    if isinstance(output, dict):
        v = output.get("total_duration_ms")
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def _empty_svg(message: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 80">'
        f'<text x="20" y="50" font-family="monospace" font-size="14">{_xml_escape(message)}</text>'
        "</svg>\n"
    )


def _bar_chart_svg(rows: list[tuple[str, float]]) -> str:
    width = 720
    row_h = 24
    label_w = 240
    bar_max_w = width - label_w - 80
    height = max(80, row_h * len(rows) + 40)
    hi = max(v for _, v in rows)
    bars: list[str] = []
    for i, (label, value) in enumerate(rows):
        y = 20 + i * row_h
        bw = max(1, int(bar_max_w * (value / hi)))
        bars.append(
            f'<text x="10" y="{y + 16}" font-family="monospace" font-size="12">'
            f"{_xml_escape(label)}</text>"
            f'<rect x="{label_w}" y="{y + 4}" width="{bw}" height="{row_h - 8}" '
            f'fill="#3a86ff" />'
            f'<text x="{label_w + bw + 8}" y="{y + 16}" font-family="monospace" '
            f'font-size="12">{value:.1f} ms</text>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">'
        + "".join(bars)
        + "</svg>\n"
    )


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# Markdown summary
# ---------------------------------------------------------------------------


def render_paper_md(
    *,
    entries: list[dict[str, Any]],
    metrics: dict[str, float],
    runs_csv: Path,
    metrics_csv: Path,
    timing_svg: Path,
    out: Path,
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ids = sorted({str(e.get("run_id", "")) for e in entries if e.get("run_id")})
    starts = [e for e in entries if e.get("kind") == "run.start"]
    ends = [e for e in entries if e.get("kind") == "run.end"]
    md = io.StringIO()
    md.write("# Research run bundle\n\n")
    md.write(f"- entries: {len(entries)}\n")
    md.write(f"- runs:    {len(run_ids)}\n")
    md.write(f"- starts:  {len(starts)}\n")
    md.write(f"- ends:    {len(ends)}\n\n")
    if metrics:
        md.write("## Final metrics\n\n")
        md.write("| metric | value |\n|---|---|\n")
        for name in sorted(metrics):
            md.write(f"| {name} | {metrics[name]:.4f} |\n")
        md.write("\n")
    md.write("## Artefacts\n\n")
    md.write(f"- [runs.csv]({runs_csv.name})\n")
    md.write(f"- [metrics.csv]({metrics_csv.name})\n")
    md.write(f"- [timing.svg]({timing_svg.name})\n")
    out.write_text(md.getvalue(), encoding="utf-8")


# ---------------------------------------------------------------------------
# façade
# ---------------------------------------------------------------------------


def export_paper_bundle(*, trace_path: Path, out_dir: Path) -> ExportBundle:
    """Run all three exporters and return the artefact :class:`ExportBundle`."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = list(iter_trace(trace_path))
    runs_csv = out_dir / "runs.csv"
    metrics_csv = out_dir / "metrics.csv"
    timing_svg = out_dir / "timing.svg"
    paper_md = out_dir / "paper_bundle.md"
    n_rows = export_runs_csv(entries, runs_csv)
    metrics = export_metrics_csv(entries, metrics_csv)
    render_timing_svg(entries, timing_svg)
    render_paper_md(
        entries=entries,
        metrics=metrics,
        runs_csv=runs_csv,
        metrics_csv=metrics_csv,
        timing_svg=timing_svg,
        out=paper_md,
    )
    return ExportBundle(
        out_dir=out_dir,
        runs_csv=runs_csv,
        metrics_csv=metrics_csv,
        timing_svg=timing_svg,
        paper_md=paper_md,
        n_entries=n_rows,
        metrics_emitted=metrics,
    )


__all__ = [
    "ExportBundle",
    "export_metrics_csv",
    "export_paper_bundle",
    "export_runs_csv",
    "iter_trace",
    "render_paper_md",
    "render_timing_svg",
]
