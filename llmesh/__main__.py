"""llmesh CLI entry point.

Usage:
    python -m llmesh audit verify <log_path> [--key-hex <hex>]

Environment variable fallback for HMAC key: LLMESH_AUDIT_HMAC_KEY
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _cmd_audit_verify(args: list[str]) -> int:
    if not args:
        print("Usage: llmesh audit verify <log_path> [--key-hex <hex>]", file=sys.stderr)
        return 2

    log_path = args[0]
    key_hex = None

    i = 1
    while i < len(args):
        if args[i] == "--key-hex" and i + 1 < len(args):
            key_hex = args[i + 1]
            i += 2
        else:
            i += 1

    if key_hex is None:
        key_hex = os.environ.get("LLMESH_AUDIT_HMAC_KEY", "")

    if not key_hex:
        print("error: HMAC key required (--key-hex or LLMESH_AUDIT_HMAC_KEY)", file=sys.stderr)
        return 2

    try:
        hmac_key = bytes.fromhex(key_hex)
    except ValueError:
        print(f"error: invalid hex key: {key_hex!r}", file=sys.stderr)
        return 2

    from llmesh.audit.trace import AuditTrace
    result = AuditTrace.verify_chain_detailed(log_path, hmac_key)

    if result.valid:
        print(f"OK  entries={result.entry_count}  file={log_path}")
        return 0
    else:
        seq = result.first_error_seq
        detail = result.error_detail
        print(f"FAIL  file={log_path}  first_error_seq={seq}  detail={detail}", file=sys.stderr)
        return 1


def _cmd_timeline(args: list[str]) -> int:
    """timeline <subcommand> [options]

    Subcommands:
      show   [--db <path>] [--limit N] [--node <id>]  Recent events (newest first)
      task   <task_id> [--db <path>]                  Full lifecycle for one task
      resumable [--db <path>]                          Tasks without a terminal event
    """
    if not args:
        print(_cmd_timeline.__doc__, file=sys.stderr)
        return 2

    sub = args[0]
    rest = args[1:]

    db_path = os.environ.get("LLMESH_TIMELINE_DB_PATH", "")
    limit = 20
    node_id = ""

    i = 0
    positional: list[str] = []
    while i < len(rest):
        if rest[i] == "--db" and i + 1 < len(rest):
            db_path = rest[i + 1]
            i += 2
        elif rest[i] == "--limit" and i + 1 < len(rest):
            limit = int(rest[i + 1])
            i += 2
        elif rest[i] == "--node" and i + 1 < len(rest):
            node_id = rest[i + 1]
            i += 2
        else:
            positional.append(rest[i])
            i += 1

    if not db_path:
        print("error: set LLMESH_TIMELINE_DB_PATH or pass --db <path>", file=sys.stderr)
        return 2

    from llmesh.timeline.store import TimelineStore
    store = TimelineStore(db_path)

    if sub == "show":
        events = store.get_recent_events(limit=limit, node_id=node_id)
        if not events:
            print("(no events)")
            return 0
        # Group by task for compact display
        print(f"{'task_id':36}  {'timestamp_utc':29}  {'event_type':20}  {'node_id':12}  details")
        print("-" * 110)
        for ev in reversed(events):
            meta = "  ".join(f"{k}={v}" for k, v in ev.metadata.items())
            print(f"{ev.task_id:36}  {ev.timestamp_utc:29}  {ev.event_type:20}  {ev.node_id:12}  {meta}")
        return 0

    if sub == "task":
        if not positional:
            print("error: task_id required", file=sys.stderr)
            return 2
        task_id = positional[0]
        events = store.get_task_timeline(task_id)
        if not events:
            print(f"(task {task_id!r} not found)")
            return 1
        print(f"\n=== Task {task_id} ===")
        first = events[0]
        for ev in events:
            delta = ev.delta_ms(first) if ev is not first else 0
            meta = "  ".join(f"{k}={v}" for k, v in ev.metadata.items())
            print(f"  +{delta:<8}ms  {ev.event_type:<22}  {ev.timestamp_utc}  {meta}")
        terminal = events[-1].is_terminal
        resumable = not terminal
        print(f"\n  terminal={terminal}  resumable={resumable}")
        if resumable:
            print("  -> Client may retry this task_id with a fresh nonce.")
        return 0

    if sub == "resumable":
        tasks = store.get_resumable_tasks()
        if not tasks:
            print("(no resumable tasks)")
            return 0
        print(f"\n{'task_id':36}  {'last_event':22}  {'idle_sec':8}  {'node_id':12}  last_ts")
        print("-" * 105)
        for t in tasks:
            print(f"{t['task_id']:36}  {t['last_event']:22}  {t['idle_sec']:>8}  "
                  f"{t['node_id']:12}  {t['last_ts']}")
        print(f"\nTotal: {len(tasks)} resumable task(s)")
        return 0

    print(f"Unknown timeline subcommand: {sub!r}", file=sys.stderr)
    return 2


def _cmd_serve_mcp() -> int:
    from llmesh.mcp.stdio_server import run_stdio_server
    run_stdio_server()
    return 0


def _prompt(question: str, choices: list[str] | None = None, default: str = "") -> str:
    """Interactive prompt — reads from stdin, returns stripped answer."""
    if choices:
        opts = "/".join(choices)
        line = f"{question} [{opts}]"
        if default:
            line += f" (default: {default})"
    else:
        line = question
        if default:
            line += f" (default: {default})"
    print(line)
    try:
        answer = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return answer if answer else default


def _prompt_multi(question: str, options: list[str]) -> list[str]:
    """Show numbered list; user enters comma-separated indices or names."""
    print(f"\n{question}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    print("  Enter numbers separated by commas (e.g. 1,3) or names:")
    try:
        raw = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return []
    if not raw:
        return []
    selected: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if token.isdigit():
            idx = int(token) - 1
            if 0 <= idx < len(options):
                selected.append(options[idx])
        elif token in options:
            selected.append(token)
    return selected


def _cmd_configure(args: list[str]) -> int:
    """Interactive Setup Wizard for LLMesh Industrial deployment.

    Usage:
        llmesh configure [--file <path>]   (default: llmesh.toml)
        llmesh configure --show            (print current industrial config)
    """
    from pathlib import Path
    from llmesh.config.toml_config import LLMeshTomlConfig
    from llmesh.config.industrial_config import (
        SUPPORTED_PROTOCOLS,
        SUPPORTED_DEVICE_TYPES,
        SUPPORTED_ANALYSIS_METHODS,
        NETWORK_POLICIES,
    )

    toml_path = Path("llmesh.toml")
    show_only = False

    i = 0
    while i < len(args):
        if args[i] == "--file" and i + 1 < len(args):
            toml_path = Path(args[i + 1])
            i += 2
        elif args[i] == "--show":
            show_only = True
            i += 1
        else:
            i += 1

    cfg = LLMeshTomlConfig.load(toml_path)

    if show_only:
        ic = cfg.industrial
        if not ic.is_configured():
            print("No industrial configuration found. Run: llmesh configure")
            return 0
        print("[industrial]")
        for k, v in ic.to_dict().items():
            print(f"  {k} = {v!r}")
        return 0

    print("\n=== LLMesh Industrial Setup Wizard ===\n")

    # [1/5] Domain
    domain = _prompt(
        "[1/5] Industry domain",
        choices=["manufacturing", "logistics", "medical", "other"],
        default=cfg.industrial.domain or "manufacturing",
    )

    # [2/5] Device types
    dev_opts = sorted(SUPPORTED_DEVICE_TYPES)
    device_types = _prompt_multi("[2/5] Device types (select all that apply)", dev_opts)
    if not device_types and cfg.industrial.device_types:
        device_types = cfg.industrial.device_types

    # [3/5] Protocols
    proto_opts = sorted(SUPPORTED_PROTOCOLS)
    protocols = _prompt_multi("[3/5] Sensor protocols (select all that apply)", proto_opts)
    if not protocols and cfg.industrial.protocols:
        protocols = cfg.industrial.protocols

    # [4/5] Analysis methods
    method_opts = sorted(SUPPORTED_ANALYSIS_METHODS)
    methods = _prompt_multi("[4/5] Analysis methods (select all that apply)", method_opts)
    if not methods and cfg.industrial.analysis_methods:
        methods = cfg.industrial.analysis_methods

    # [5/5] Network + data policy
    net_policy = _prompt(
        "[5/5] Network policy",
        choices=sorted(NETWORK_POLICIES),
        default=cfg.industrial.network_policy,
    )
    if net_policy not in NETWORK_POLICIES:
        net_policy = "local_only"

    retention_str = _prompt(
        "      Data retention (days)",
        default=str(cfg.industrial.data_retention_days),
    )
    try:
        retention_days = max(1, int(retention_str))
    except ValueError:
        retention_days = 90

    unit_space_dir = _prompt(
        "      MT-method unit-space directory",
        default=cfg.industrial.unit_space_dir or "unit_spaces",
    )

    # Apply to config
    from llmesh.config.industrial_config import IndustrialConfig
    cfg.industrial.__class__  # ensure import resolved
    new_industrial = IndustrialConfig(
        domain=domain,
        device_types=device_types,
        protocols=protocols,
        analysis_methods=methods,
        network_policy=net_policy,
        data_retention_days=retention_days,
        unit_space_dir=unit_space_dir,
    )

    import dataclasses
    cfg = dataclasses.replace(cfg, industrial=new_industrial)

    # Write back to TOML
    try:
        import tomli_w  # type: ignore[import]
        with open(toml_path, "wb") as f:
            tomli_w.dump(cfg.to_dict(), f)
        print(f"\nConfiguration saved to {toml_path}")
    except ImportError:
        # Fallback: write minimal TOML manually (no tomli-w installed)
        _write_toml_fallback(toml_path, cfg.to_dict())
        print(f"\nConfiguration saved to {toml_path} (tomli-w not installed; basic format)")

    print("\nIndustrial settings:")
    for k, v in new_industrial.to_dict().items():
        print(f"  {k} = {v!r}")
    return 0


def _write_toml_fallback(path: Path, d: dict) -> None:
    """Minimal TOML serialiser for the [industrial] section only (no tomli-w fallback)."""

    def _val(v: object) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, str):
            return f'"{v}"'
        if isinstance(v, list):
            return "[" + ", ".join(_val(x) for x in v) + "]"
        return f'"{v}"'

    lines: list[str] = []
    for section, val in d.items():
        if isinstance(val, dict):
            lines.append(f"\n[{section}]")
            for k, v in val.items():
                lines.append(f"{k} = {_val(v)}")
        else:
            lines.append(f"{section} = {_val(val)}")

    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _cmd_mt_collect(args: list[str]) -> int:
    """mt-collect — record normal sensor readings to a .npz file.

    Usage:
        llmesh mt-collect --device <id> --duration <sec> --output <file.npz>
                          [--adapter modbus|serial|stdin] [--interval <sec>]

    The simplest mode reads whitespace-separated float rows from stdin
    (--adapter stdin, the default when no hardware is configured).

    Example (stdin):
        printf "2.1 2.3 2.0\\n1.9 2.2 2.1\\n" | llmesh mt-collect --device d1 --duration 0 --output n.npz
    """
    import time

    device_id = ""
    duration = 60.0
    output = "normal_data.npz"
    adapter = "stdin"
    interval = 1.0

    i = 0
    while i < len(args):
        if args[i] == "--device" and i + 1 < len(args):
            device_id = args[i + 1]
            i += 2
        elif args[i] == "--duration" and i + 1 < len(args):
            duration = float(args[i + 1])
            i += 2
        elif args[i] == "--output" and i + 1 < len(args):
            output = args[i + 1]
            i += 2
        elif args[i] == "--adapter" and i + 1 < len(args):
            adapter = args[i + 1]
            i += 2
        elif args[i] == "--interval" and i + 1 < len(args):
            interval = float(args[i + 1])
            i += 2
        else:
            i += 1
    _ = interval  # parsed for forward compatibility; not yet used by stdin adapter

    if not device_id:
        print("error: --device is required", file=sys.stderr)
        return 2

    try:
        import numpy as np  # type: ignore[import]
    except ImportError:
        print("error: numpy required — pip install 'llmesh[industrial]'", file=sys.stderr)
        return 1

    rows: list[list[float]] = []

    if adapter == "stdin":
        print(f"Reading from stdin (device={device_id}). Enter rows of floats, one per line. Ctrl-D to stop.")
        deadline = time.monotonic() + duration if duration > 0 else float("inf")
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = [float(v) for v in line.split()]
                except ValueError:
                    print(f"  skip (not floats): {line!r}", file=sys.stderr)
                    continue
                rows.append(row)
                if time.monotonic() >= deadline:
                    break
        except KeyboardInterrupt:
            pass
    else:
        print(f"error: adapter {adapter!r} not yet supported in mt-collect; use --adapter stdin", file=sys.stderr)
        return 2

    if not rows:
        print("error: no data collected", file=sys.stderr)
        return 1

    # Validate all rows have the same length
    n_features = len(rows[0])
    uniform = all(len(r) == n_features for r in rows)
    if not uniform:
        print("error: rows have different feature counts — cannot save", file=sys.stderr)
        return 1

    data = np.array(rows, dtype=float)
    np.savez_compressed(output, data=data, device_id=device_id)
    print(f"Saved {data.shape[0]} observations x {data.shape[1]} features → {output}")
    return 0


def _cmd_mt_train(args: list[str]) -> int:
    """mt-train — compute MT unit space from collected normal data.

    Usage:
        llmesh mt-train --input <normal_data.npz> --device <id>
                        [--output <unit_space.npz>]
    """
    input_path = ""
    device_id = ""
    output = ""

    i = 0
    while i < len(args):
        if args[i] == "--input" and i + 1 < len(args):
            input_path = args[i + 1]; i += 2
        elif args[i] == "--device" and i + 1 < len(args):
            device_id = args[i + 1]; i += 2
        elif args[i] == "--output" and i + 1 < len(args):
            output = args[i + 1]; i += 2
        else:
            i += 1

    if not input_path:
        print("error: --input is required", file=sys.stderr)
        return 2

    try:
        import numpy as np  # type: ignore[import]
    except ImportError:
        print("error: numpy required — pip install 'llmesh[industrial]'", file=sys.stderr)
        return 1

    try:
        arrays = np.load(input_path, allow_pickle=False)
    except FileNotFoundError:
        print(f"error: file not found: {input_path}", file=sys.stderr)
        return 1

    data = arrays["data"]
    if not device_id:
        device_id = str(arrays.get("device_id", ""))
    if not device_id:
        print("error: --device required (not found in npz either)", file=sys.stderr)
        return 2

    if not output:
        output = f"unit_space_{device_id}.npz"

    from llmesh.industrial.mt_engine import MTEngine
    try:
        engine = MTEngine(device_id=device_id)
        engine.fit(data)
        engine.save(output)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Trained on {data.shape[0]} observations x {data.shape[1]} features")
    print(f"Unit space saved → {output}")
    return 0


def _cmd_mt_infer(args: list[str]) -> int:
    """mt-infer — real-time Mahalanobis distance from stdin.

    Usage:
        llmesh mt-infer --model <unit_space.npz> [--threshold <float>]

    Reads one row of floats per line from stdin and prints MD + anomaly flag.
    """
    model_path = ""
    threshold = 3.0

    i = 0
    while i < len(args):
        if args[i] == "--model" and i + 1 < len(args):
            model_path = args[i + 1]; i += 2
        elif args[i] == "--threshold" and i + 1 < len(args):
            threshold = float(args[i + 1]); i += 2
        else:
            i += 1

    if not model_path:
        print("error: --model is required", file=sys.stderr)
        return 2

    from llmesh.industrial.mt_engine import MTEngine
    try:
        engine = MTEngine.load(model_path)
    except Exception as exc:
        print(f"error loading model: {exc}", file=sys.stderr)
        return 1

    print(f"Model loaded (device={engine.device_id}, features={engine._n_features}, threshold={threshold})")
    print("Enter feature rows (Ctrl-D to stop):")

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                sample = [float(v) for v in line.split()]
            except ValueError:
                print(f"  skip: {line!r}", file=sys.stderr)
                continue
            try:
                md = engine.md(sample)
                flag = "ANOMALY" if md > threshold else "OK"
                print(f"  MD={md:.4f}  {flag}")
            except ValueError as exc:
                print(f"  error: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        pass

    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    if not args:
        print("Usage: llmesh <command> [args]")
        print("Commands: audit verify | timeline show|task|resumable | serve-mcp | configure")
        print("          mt-collect | mt-train | mt-infer")
        return 0

    if args[0] == "audit" and len(args) > 1 and args[1] == "verify":
        return _cmd_audit_verify(args[2:])

    if args[0] == "timeline":
        return _cmd_timeline(args[1:])

    if args[0] == "serve-mcp":
        return _cmd_serve_mcp()

    if args[0] == "configure":
        return _cmd_configure(args[1:])

    if args[0] == "mt-collect":
        return _cmd_mt_collect(args[1:])

    if args[0] == "mt-train":
        return _cmd_mt_train(args[1:])

    if args[0] == "mt-infer":
        return _cmd_mt_infer(args[1:])

    print(f"Unknown command: {' '.join(args)}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
