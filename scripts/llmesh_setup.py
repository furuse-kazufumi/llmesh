"""llmesh_setup.py -- Interactive setup wizard for LLMesh nodes.

Commands:
    init              Generate node identity + TLS certs + config skeleton
    peer add <url>    Bootstrap: TOFU-connect to a peer node
    peer list         Show known peers
    start             Print the uvicorn start command for this node
    status            Show node identity and peer count
    check             Auto-detect environment and report readiness
"""
import argparse
import hashlib
import json
import os
import platform
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

# -- path bootstrap (run from project root) ----------------------------------
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from llmesh.identity.node_id import NodeIdentity, _b58encode
from llmesh.identity.manifest import CapabilityManifest
from llmesh.identity.resolver import DIDResolver, DIDResolutionError
from llmesh.auth.trusted_peers import TrustedPeers
from llmesh.rendezvous.client import lookup as rendezvous_lookup, LookupError

_CONFIG_DIR = Path("config")
_CERTS_DIR  = Path("certs")
_IDENTITY_FILE      = _CONFIG_DIR / "node_identity.json"
_PRIVATE_KEY_FILE   = _CONFIG_DIR / "node.key.bin"
_TRUSTED_PEERS_FILE = _CONFIG_DIR / "trusted_peers.json"
_NODE_OVERRIDES_FILE = _CONFIG_DIR / "node_overrides.json"
_SETTINGS_FILE       = _CONFIG_DIR / "settings.json"


# -- helpers -----------------------------------------------------------------

def _fingerprint(pub_hex: str) -> str:
    digest = hashlib.sha256(bytes.fromhex(pub_hex)).hexdigest()
    return ":".join(digest[i:i+2] for i in range(0, 32, 2))


def _did_to_node_id(did: str) -> str:
    """Convert a did:llmesh:1:z... identifier to its peer:... node_id."""
    try:
        resolver = DIDResolver()
        doc = resolver.resolve(did)
        return "peer:" + _b58encode(doc.public_key_bytes)
    except DIDResolutionError as exc:
        print(f"ERROR: Cannot resolve DID {did!r}: {exc}")
        sys.exit(1)


def _resolve_peer_url(raw: str, rendezvous_url: str | None) -> str:
    """Resolve a peer argument to an HTTP endpoint URL.

    Accepts:
      - did:llmesh:1:z...  → rendezvous lookup via DID→node_id conversion
      - peer:...           → rendezvous lookup directly by node_id
      - http(s)://...      → returned as-is (existing TOFU flow)
    """
    if raw.startswith("did:llmesh:1:"):
        node_id = _did_to_node_id(raw)
        return _rendezvous_lookup(node_id, rendezvous_url, label=raw)
    if raw.startswith("peer:"):
        return _rendezvous_lookup(raw, rendezvous_url, label=raw)
    return raw  # plain URL — existing behaviour


def _rendezvous_lookup(node_id: str, rendezvous_url: str | None, *, label: str) -> str:
    """Look up a node endpoint via the rendezvous server."""
    if not rendezvous_url:
        print("ERROR: --rendezvous-url (or LLMESH_RENDEZVOUS_URL) is required "
              f"to resolve {label!r}")
        sys.exit(1)
    print(f"Looking up {label} via rendezvous at {rendezvous_url} ...")
    try:
        url = rendezvous_lookup(node_id, rendezvous_url)
        print(f"  → resolved to {url}")
        return url
    except LookupError as exc:
        print(f"ERROR: Rendezvous lookup failed: {exc}")
        sys.exit(1)


def _load_identity() -> NodeIdentity:
    if not _PRIVATE_KEY_FILE.exists():
        print("ERROR: Node identity not found.  Run: python scripts/llmesh_setup.py init")
        sys.exit(1)
    return NodeIdentity.from_private_bytes(_PRIVATE_KEY_FILE.read_bytes())


def _load_peers() -> TrustedPeers:
    if not _TRUSTED_PEERS_FILE.exists():
        TrustedPeers.create_empty(_TRUSTED_PEERS_FILE)
    return TrustedPeers(_TRUSTED_PEERS_FILE)


def _fetch_identity(url: str, ca_cert: str | None) -> dict:
    """Fetch /identity from a peer node (with optional TLS verification)."""
    full_url = url.rstrip("/") + "/identity"
    ctx: ssl.SSLContext | None = None
    if full_url.startswith("https://"):
        ctx = ssl.create_default_context()
        if ca_cert and Path(ca_cert).exists():
            ctx.load_verify_locations(ca_cert)
        else:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(full_url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as exc:
        print(f"ERROR: Cannot reach {full_url}: {exc}")
        sys.exit(1)


# -- commands ----------------------------------------------------------------

def cmd_init(args) -> None:
    """Generate node identity, TLS certs, and config skeleton."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Node identity (Ed25519)
    if _PRIVATE_KEY_FILE.exists() and not args.force:
        print(f"Identity already exists at {_PRIVATE_KEY_FILE}  (use --force to regenerate)")
        identity = _load_identity()
    else:
        identity = NodeIdentity.generate()
        _PRIVATE_KEY_FILE.write_bytes(identity.private_bytes())
        try:
            _PRIVATE_KEY_FILE.chmod(0o600)  # no-op on Windows; silently ignored
        except NotImplementedError:
            pass
        print(f"Generated node identity -> {_PRIVATE_KEY_FILE}")

    # 2. Save public info
    pub_info = {
        "node_id":        identity.node_id,
        "public_key_hex": identity.public_key_hex,
        "did":            identity.did_key,
        "fingerprint":    _fingerprint(identity.public_key_hex),
    }
    _IDENTITY_FILE.write_text(json.dumps(pub_info, indent=2))
    print(f"Public identity       -> {_IDENTITY_FILE}")

    # 3. TLS certs (if gen_certs.py has been run)
    node_crt = _CERTS_DIR / "node.crt"
    if not node_crt.exists():
        print("\nTLS certs not found.  Run:")
        print("  python scripts/gen_certs.py ca --out certs/")
        print(f"  python scripts/gen_certs.py node --name {identity.node_id[:12]} --out certs/")

    # 4. Empty trusted_peers if missing
    if not _TRUSTED_PEERS_FILE.exists():
        TrustedPeers.create_empty(_TRUSTED_PEERS_FILE)
        print(f"Empty trusted peers   -> {_TRUSTED_PEERS_FILE}")

    print("\n-- Node identity ------------------------------------------")
    print(f"  node_id     : {identity.node_id}")
    print(f"  fingerprint : {pub_info['fingerprint']}")
    print(f"  did         : {identity.did_key}")
    print("\nShare the fingerprint with peers for TOFU verification.")
    print("Next: python scripts/llmesh_setup.py peer add <peer-url>")


def cmd_peer_add(args) -> None:
    """Bootstrap: TOFU-connect to a peer and add to trusted_peers."""
    rendezvous_url = getattr(args, "rendezvous_url", None) or os.environ.get("LLMESH_RENDEZVOUS_URL")
    url     = _resolve_peer_url(args.url, rendezvous_url)
    ca_cert = args.ca_cert

    print(f"Fetching identity from {url} ...")
    info = _fetch_identity(url, ca_cert)

    node_id = info.get("node_id", "")
    pub_hex = info.get("public_key_hex", "")
    did     = info.get("did", "")
    fp      = info.get("fingerprint", _fingerprint(pub_hex))

    print()
    print("-- Peer identity ------------------------------------------")
    print(f"  node_id     : {node_id}")
    print(f"  fingerprint : {fp}")
    print(f"  did         : {did}")
    print(f"  endpoint    : {url}")
    print()
    print("Verify this fingerprint matches what is shown on the PEER machine")
    print("(run: python scripts/llmesh_setup.py status  on the peer).")
    print()

    answer = input("Trust this node? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted - peer not added.")
        sys.exit(0)

    peers = _load_peers()
    peers.add(
        node_id=node_id,
        public_key_hex=pub_hex,
        did=did,
        endpoint=url.rstrip("/"),
        source="manual",
    )
    print(f"\nAdded {node_id} to {_TRUSTED_PEERS_FILE}")
    print("Gossip will automatically discover additional peers from this node.")


def cmd_peer_list(args) -> None:
    peers = _load_peers()
    if not len(peers):
        print("No trusted peers yet.  Run: python scripts/llmesh_setup.py peer add <url>")
        return
    print(f"{'node_id':<40} {'source':<20} {'endpoint'}")
    print("-" * 90)
    for p in peers:
        print(f"{p.node_id:<40} {p.source:<20} {p.endpoint}")


def cmd_start(args) -> None:
    """Print the uvicorn command to start this node."""
    node_crt = _CERTS_DIR / "node.crt"
    node_key = _CERTS_DIR / "node.key"
    port     = args.port

    env_vars = [
        f"LLMESH_NODE_IDENTITY_PATH={_PRIVATE_KEY_FILE}",
        f"LLMESH_TRUSTED_PEERS_PATH={_TRUSTED_PEERS_FILE}",
    ]
    cmd_parts = [
        "uvicorn llmesh.mcp.server:app",
        f"--host 0.0.0.0 --port {port}",
    ]
    if node_crt.exists() and node_key.exists():
        cmd_parts += [
            f"--ssl-certfile {node_crt}",
            f"--ssl-keyfile  {node_key}",
        ]
    else:
        print("WARNING: TLS certs not found - server will use plain HTTP.")

    print("\n-- Start command ------------------------------------------")
    for e in env_vars:
        print(f"  {e} \\")
    print(f"  {' '.join(cmd_parts)}")
    print()


def cmd_status(args) -> None:
    identity = _load_identity()
    peers    = _load_peers()
    fp = _fingerprint(identity.public_key_hex)
    print("\n-- This node ----------------------------------------------")
    print(f"  node_id     : {identity.node_id}")
    print(f"  fingerprint : {fp}   <- share this for TOFU verification")
    print(f"  did         : {identity.did_key}")
    print(f"\n-- Trusted peers ({len(peers)}) ------------------------------------")
    for p in peers:
        print(f"  {p.node_id:<38} {p.source:<20} {p.endpoint}")


def cmd_check(args) -> None:
    """Auto-detect environment and report readiness for running LLMesh."""
    import subprocess
    import sys as _sys

    OK   = "  [OK]"
    ERR  = "  [!!]"
    WARN = "  [??]"

    print(f"\n-- Environment  (OS: {platform.system()} {platform.release()}) ------")
    print(f"   machine: {platform.machine()}  Python: {platform.python_implementation()}")

    print("\n-- Python -------------------------------------------------")
    vi = _sys.version_info
    tag = OK if vi >= (3, 11) else WARN
    print(f"{tag} Python {vi.major}.{vi.minor}.{vi.micro}  (3.11+ required)")

    print("\n-- LLMesh package -----------------------------------------")
    try:
        import llmesh  # noqa: F401
        print(f"{OK} llmesh importable")
    except ImportError:
        print(f"{ERR} llmesh not importable - run: pip install -e .[dev]")

    print("\n-- LLM backend --------------------------------------------")
    ollama_url = "http://localhost:11434"
    llamacpp_url = "http://localhost:8080"
    ollama_ok = False
    llamacpp_ok = False

    try:
        req = urllib.request.Request(ollama_url + "/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
        models = [m["name"] for m in data.get("models", [])]
        ollama_ok = True
        print(f"{OK} Ollama running at {ollama_url}")
        if models:
            print(f"   models: {', '.join(models[:8])}")
        else:
            print(f"{WARN} No Ollama models — run: ollama pull llama3.2")
    except Exception as exc:
        print(f"{WARN} Ollama not reachable ({exc})")

    try:
        req = urllib.request.Request(llamacpp_url + "/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
        if data.get("status") == "ok":
            llamacpp_ok = True
            print(f"{OK} llama-server running at {llamacpp_url}")
        else:
            print(f"{WARN} llama-server at {llamacpp_url} returned status={data.get('status')!r}")
    except Exception as exc:
        print(f"{WARN} llama-server not reachable ({exc})")

    if not ollama_ok and not llamacpp_ok:
        print(f"{ERR} No LLM backend available")
        print("   Ollama:     https://ollama.com && ollama pull llama3.2")
        print("   llama.cpp:  https://github.com/ggerganov/llama.cpp")

    print("\n-- Docker -------------------------------------------------")
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            print(f"{OK} Docker {result.stdout.strip()}")
        else:
            print(f"{ERR} Docker daemon not running")
    except FileNotFoundError:
        print(f"{ERR} docker not found - install Docker Desktop")
    except Exception as exc:
        print(f"{ERR} Docker check failed: {exc}")

    print("\n-- Node identity ------------------------------------------")
    if _PRIVATE_KEY_FILE.exists():
        try:
            identity = _load_identity()
            fp = _fingerprint(identity.public_key_hex)
            print(f"{OK} Identity found: {identity.node_id}")
            print(f"   fingerprint: {fp}")
        except Exception as exc:
            print(f"{ERR} Identity corrupt: {exc}")
    else:
        print(f"{ERR} No identity - run: python scripts/llmesh_setup.py init")

    print("\n-- TLS certificates ---------------------------------------")
    ca  = _CERTS_DIR / "ca.crt"
    crt = _CERTS_DIR / "node.crt"
    key = _CERTS_DIR / "node.key"
    if ca.exists() and crt.exists() and key.exists():
        print(f"{OK} Certs present in {_CERTS_DIR}/")
    elif not ca.exists():
        print(f"{ERR} CA cert missing - run: python scripts/gen_certs.py ca --out certs/")
    else:
        print(f"{ERR} Node cert/key missing - run: python scripts/gen_certs.py node --out certs/")

    print("\n-- Trusted peers ------------------------------------------")
    peers = _load_peers()
    count = len(peers)
    if count:
        print(f"{OK} {count} trusted peer(s) configured")
    else:
        print(f"{WARN} No peers yet - run: python scripts/llmesh_setup.py peer add <url>")

    print()


def cmd_autosetup(args) -> None:
    """Diagnose environment step-by-step and configure this node automatically."""
    import subprocess
    import sys as _sys

    STEP = "[STEP]"
    OK   = "  [OK]"
    ERR  = "  [!!]"
    WARN = "  [??]"

    issues: list[str] = []

    print(f"\nLLMesh Auto-Setup  (OS: {platform.system()} {platform.release()} / {platform.machine()})")
    print("=" * 55)

    # --- Step 1: Python ---
    print(f"\n{STEP} 1/5  Python version")
    vi = _sys.version_info
    if vi >= (3, 11):
        print(f"{OK} Python {vi.major}.{vi.minor}.{vi.micro}")
    else:
        print(f"{ERR} Python {vi.major}.{vi.minor}.{vi.micro} -- 3.11+ required")
        issues.append("Python 3.11+ required")

    # --- Step 2: llmesh package ---
    print(f"\n{STEP} 2/5  LLMesh package")
    try:
        import llmesh  # noqa: F401
        print(f"{OK} llmesh importable")
    except ImportError:
        print(f"{ERR} llmesh not importable")
        print("       Fix: pip install -e .[dev]")
        issues.append("llmesh not installed - run: pip install -e .[dev]")

    # --- Step 3: LLM backend (Ollama or llama.cpp) ---
    print(f"\n{STEP} 3/5  LLM backend (Ollama or llama-server)")
    _ollama_ok = False
    _llamacpp_ok = False

    try:
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
        models = [m["name"] for m in data.get("models", [])]
        _ollama_ok = True
        print(f"{OK} Ollama running at http://localhost:11434")
        if models:
            print(f"   models available: {', '.join(models[:6])}")
        else:
            print(f"{WARN} No models pulled yet — run: ollama pull llama3.2")
            issues.append("No Ollama model - run: ollama pull llama3.2")
    except Exception:
        print(f"{WARN} Ollama not reachable at http://localhost:11434")

    try:
        req = urllib.request.Request("http://localhost:8080/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
        if data.get("status") == "ok":
            _llamacpp_ok = True
            print(f"{OK} llama-server running at http://localhost:8080")
    except Exception:
        print(f"{WARN} llama-server not reachable at http://localhost:8080")

    if not _ollama_ok and not _llamacpp_ok:
        print(f"{ERR} No LLM backend found")
        print("       Install Ollama: https://ollama.com && ollama pull llama3.2")
        print("       Or llama-server: https://github.com/ggerganov/llama.cpp")
        issues.append("No LLM backend running (Ollama or llama-server required)")

    # --- Step 4: Node identity ---
    print(f"\n{STEP} 4/5  Node identity")
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    identity: NodeIdentity | None = None
    if _PRIVATE_KEY_FILE.exists() and not args.force:
        try:
            identity = _load_identity()
            fp = _fingerprint(identity.public_key_hex)
            print(f"{OK} Existing identity loaded")
            print(f"   node_id:     {identity.node_id}")
            print(f"   fingerprint: {fp}")
        except Exception as exc:
            print(f"{ERR} Existing identity corrupt: {exc}")
            print("       Fix: python scripts/llmesh_setup.py autosetup --force")
            issues.append("identity corrupt - use --force to regenerate")
    else:
        identity = NodeIdentity.generate()
        _PRIVATE_KEY_FILE.write_bytes(identity.private_bytes())
        try:
            _PRIVATE_KEY_FILE.chmod(0o600)
        except NotImplementedError:
            pass
        pub_info = {
            "node_id":        identity.node_id,
            "public_key_hex": identity.public_key_hex,
            "did":            identity.did_key,
            "fingerprint":    _fingerprint(identity.public_key_hex),
        }
        _IDENTITY_FILE.write_text(json.dumps(pub_info, indent=2))
        if not _TRUSTED_PEERS_FILE.exists():
            TrustedPeers.create_empty(_TRUSTED_PEERS_FILE)
        fp = _fingerprint(identity.public_key_hex)
        print(f"{OK} Node identity generated")
        print(f"   node_id:     {identity.node_id}")
        print(f"   fingerprint: {fp}")
        print("   Share the fingerprint with peers for TOFU verification.")

    # --- Step 5: TLS certificates ---
    print(f"\n{STEP} 5/5  TLS certificates")
    ca_crt   = _CERTS_DIR / "ca.crt"
    node_crt = _CERTS_DIR / "node.crt"
    node_key = _CERTS_DIR / "node.key"
    gen_certs_script = Path(__file__).parent / "gen_certs.py"

    if ca_crt.exists() and node_crt.exists() and node_key.exists():
        print(f"{OK} Certs already present in {_CERTS_DIR}/")
    elif gen_certs_script.exists() and identity is not None:
        _CERTS_DIR.mkdir(parents=True, exist_ok=True)
        node_name = identity.node_id[:12]
        try:
            for step_args in (
                [_sys.executable, str(gen_certs_script), "ca",   "--out", str(_CERTS_DIR)],
                [_sys.executable, str(gen_certs_script), "node", "--name", node_name, "--out", str(_CERTS_DIR)],
            ):
                r = subprocess.run(step_args, capture_output=True, text=True)
                if r.returncode != 0:
                    raise RuntimeError(r.stderr[:200] or r.stdout[:200])
            print(f"{OK} TLS certs generated in {_CERTS_DIR}/")
        except Exception as exc:
            print(f"{ERR} Cert generation failed: {exc}")
            issues.append("TLS cert generation failed - check gen_certs.py")
    else:
        print(f"{WARN} gen_certs.py not found - skipping auto-TLS")
        print(f"       Run manually: python scripts/gen_certs.py ca --out certs/")
        print(f"                     python scripts/gen_certs.py node --out certs/")

    # --- Summary ---
    print("\n" + "=" * 55)
    if issues:
        print(f"{ERR} Setup completed with {len(issues)} issue(s) to resolve:")
        for i, issue in enumerate(issues, 1):
            print(f"   {i}. {issue}")
    else:
        print(f"{OK} All steps passed. Node is ready!")
        print("\n   Next steps:")
        print("   1. Add a peer:  python scripts/llmesh_setup.py peer add <url>")
        print("   2. Start node:  python scripts/llmesh_setup.py start")
    print()


# -- node override commands --------------------------------------------------

def _load_overrides():
    from llmesh.routing.node_overrides import NodeOverrides
    return NodeOverrides(path=_NODE_OVERRIDES_FILE)


def cmd_node_block(args) -> None:
    """Block a node from receiving any requests."""
    overrides = _load_overrides()
    overrides.block(args.node_id, reason=args.reason)
    print(f"Blocked: {args.node_id}")
    if args.reason:
        print(f"  reason: {args.reason}")


def cmd_node_unblock(args) -> None:
    """Remove a manual block from a node."""
    overrides = _load_overrides()
    if not overrides.is_blocked(args.node_id):
        print(f"Not blocked: {args.node_id}")
        return
    overrides.unblock(args.node_id)
    print(f"Unblocked: {args.node_id}")


def cmd_node_pin(args) -> None:
    """Pin a node as priority — bypasses fairness, sorted first."""
    overrides = _load_overrides()
    overrides.pin(args.node_id, label=args.label)
    print(f"Pinned: {args.node_id}")
    if args.label:
        print(f"  label: {args.label}")


def cmd_node_unpin(args) -> None:
    """Remove a priority pin from a node."""
    overrides = _load_overrides()
    if not overrides.is_pinned(args.node_id):
        print(f"Not pinned: {args.node_id}")
        return
    overrides.unpin(args.node_id)
    print(f"Unpinned: {args.node_id}")


def cmd_node_list(args) -> None:
    """List blocked and pinned nodes."""
    import datetime
    overrides = _load_overrides()
    blocked = overrides.blocked_nodes()
    pinned  = overrides.pinned_nodes()

    def _ts(t: float) -> str:
        return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")

    if blocked:
        print(f"\n{'BLOCKED':^6}  ({len(blocked)} node(s))")
        print(f"  {'node_id':<42} {'reason':<24} since")
        print("  " + "-" * 82)
        for nid, meta in blocked.items():
            reason = meta.get("reason", "")[:22]
            since  = _ts(meta.get("blocked_at", 0))
            print(f"  {nid:<42} {reason:<24} {since}")
    else:
        print("\nNo blocked nodes.")

    if pinned:
        print(f"\n{'PINNED (priority)':^6}  ({len(pinned)} node(s))")
        print(f"  {'node_id':<42} {'label':<24} since")
        print("  " + "-" * 82)
        for nid, meta in pinned.items():
            label = meta.get("label", "")[:22]
            since = _ts(meta.get("pinned_at", 0))
            print(f"  {nid:<42} {label:<24} {since}")
    else:
        print("\nNo pinned nodes.")
    print()


# -- settings commands -------------------------------------------------------

def _load_settings():
    from llmesh.config.settings import LLMeshSettings
    return LLMeshSettings.load(_SETTINGS_FILE)


def cmd_settings_show(args) -> None:
    """Show current settings (defaults when no settings.json exists)."""
    settings = _load_settings()
    source = f"{_SETTINGS_FILE}" if _SETTINGS_FILE.exists() else "(defaults - no settings.json)"
    print(f"\n-- LLMesh Settings  {source} --")
    print(settings.as_table())
    print()


def cmd_settings_set(args) -> None:
    """Set a single setting and persist to settings.json."""
    settings = _load_settings()
    try:
        settings.set_value(args.key, args.value)
    except (KeyError, ValueError) as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    settings.save(_SETTINGS_FILE)
    flat_key = args.key.replace(".", "_")
    print(f"Set {flat_key} = {getattr(settings, flat_key)}")
    print(f"Saved to {_SETTINGS_FILE}")


def cmd_settings_reset(args) -> None:
    """Delete settings.json and revert to compiled-in defaults."""
    if _SETTINGS_FILE.exists():
        _SETTINGS_FILE.unlink()
        print(f"Deleted {_SETTINGS_FILE}  (defaults restored)")
    else:
        print("No settings.json found — already at defaults.")


# -- plugin commands ---------------------------------------------------------

def cmd_plugin_list(args) -> None:
    """List all registered protocol adapters (built-in and plugins)."""
    from llmesh.protocol.registry import AdapterRegistry
    available = AdapterRegistry.available()
    specs = AdapterRegistry.plugin_specs()
    settings = _load_settings()
    configured = settings.adapter_plugins

    print("\n-- Protocol Adapters ----------------------------------------")
    if not available:
        print("  (none registered)")
    else:
        print(f"  {'name':<16} {'source'}")
        print("  " + "-" * 44)
        for name in available:
            source = f"plugin: {specs[name]}" if name in specs else "built-in"
            print(f"  {name:<16} {source}")

    if configured:
        print("\n-- Configured plugins (settings.json) -----------------------")
        for spec in configured:
            print(f"  {spec}")
    print()


def cmd_plugin_add(args) -> None:
    """Add a plugin spec to settings.json and validate it can be imported."""
    from llmesh.protocol.registry import AdapterRegistry
    spec = args.spec
    try:
        name = AdapterRegistry.load_plugin(spec)
    except (ValueError, ImportError, AttributeError, TypeError) as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    settings = _load_settings()
    if spec not in settings.adapter_plugins:
        settings.adapter_plugins.append(spec)
        settings.save(_SETTINGS_FILE)
        print(f"Plugin registered: {name} ({spec})")
        print(f"Saved to {_SETTINGS_FILE}")
    else:
        print(f"Already configured: {spec}")


def cmd_plugin_remove(args) -> None:
    """Remove a plugin by protocol name from settings.json."""
    settings = _load_settings()
    name = args.name

    before = list(settings.adapter_plugins)
    settings.adapter_plugins = [
        s for s in settings.adapter_plugins
        if not s.endswith(f"={name}")
    ]
    if settings.adapter_plugins == before:
        print(f"No plugin named {name!r} found in settings.json")
        return
    settings.save(_SETTINGS_FILE)
    print(f"Removed plugin {name!r} from {_SETTINGS_FILE}")


# -- entry point -------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="LLMesh node setup wizard")
    sub = p.add_subparsers(dest="cmd", required=True)

    init_p = sub.add_parser("init", help="Generate identity + config")
    init_p.add_argument("--force", action="store_true", help="Overwrite existing identity")

    peer_p = sub.add_parser("peer", help="Manage trusted peers")
    peer_sub = peer_p.add_subparsers(dest="peer_cmd", required=True)

    add_p = peer_sub.add_parser("add", help="Add a peer via TOFU")
    add_p.add_argument(
        "url",
        help="Peer URL, node_id (peer:...), or DID (did:llmesh:1:z...). "
             "DID/node_id requires --rendezvous-url or LLMESH_RENDEZVOUS_URL.",
    )
    add_p.add_argument("--ca-cert", default="certs/ca.crt", help="CA cert for TLS verification")
    add_p.add_argument(
        "--rendezvous-url",
        default=None,
        dest="rendezvous_url",
        help="Rendezvous server base URL (overrides LLMESH_RENDEZVOUS_URL env var)",
    )

    peer_sub.add_parser("list", help="List trusted peers")

    start_p = sub.add_parser("start", help="Print start command")
    start_p.add_argument("--port", default=8001, type=int)

    sub.add_parser("status", help="Show node identity + peers")
    sub.add_parser("check",  help="Auto-detect environment and report readiness")

    autosetup_p = sub.add_parser("autosetup", help="Diagnose + setup node in one pass")
    autosetup_p.add_argument("--force", action="store_true", help="Regenerate existing identity")

    # -- node override commands -------------------------------------------
    node_p = sub.add_parser("node", help="Manage per-node overrides (block / pin)")
    node_sub = node_p.add_subparsers(dest="node_cmd", required=True)

    block_p = node_sub.add_parser("block", help="Block a node from receiving requests")
    block_p.add_argument("node_id", help="Node ID to block (peer:...)")
    block_p.add_argument("--reason", default="", help="Human-readable reason")

    unblock_p = node_sub.add_parser("unblock", help="Remove block from a node")
    unblock_p.add_argument("node_id", help="Node ID to unblock")

    pin_p = node_sub.add_parser("pin", help="Pin a node as priority (bypasses fairness)")
    pin_p.add_argument("node_id", help="Node ID to pin (peer:...)")
    pin_p.add_argument("--label", default="", help="Optional human-readable label")

    unpin_p = node_sub.add_parser("unpin", help="Remove priority pin from a node")
    unpin_p.add_argument("node_id", help="Node ID to unpin")

    node_sub.add_parser("list", help="List all blocked and pinned nodes")

    # -- plugin commands --------------------------------------------------
    plugin_p = sub.add_parser("plugin", help="Manage custom protocol adapter plugins")
    plugin_sub = plugin_p.add_subparsers(dest="plugin_cmd", required=True)

    plugin_sub.add_parser("list", help="List registered adapters and configured plugins")

    add_plugin_p = plugin_sub.add_parser(
        "add", help="Load a plugin and add it to settings.json"
    )
    add_plugin_p.add_argument(
        "spec",
        help="Plugin spec: 'module.path:ClassName=protocol_name' (e.g. mypkg:GRPCAdapter=grpc)",
    )

    rm_plugin_p = plugin_sub.add_parser(
        "remove", help="Remove a plugin from settings.json by protocol name"
    )
    rm_plugin_p.add_argument("name", help="Protocol name to remove (e.g. grpc)")

    # -- settings commands ------------------------------------------------
    settings_p = sub.add_parser("settings", help="View and edit node settings")
    settings_sub = settings_p.add_subparsers(dest="settings_cmd", required=True)

    settings_sub.add_parser("show", help="Show current settings")

    set_p = settings_sub.add_parser("set", help="Set a setting value")
    set_p.add_argument(
        "key",
        help="Setting key (dotted or flat), e.g. cb.failure_threshold or fairness_enabled",
    )
    set_p.add_argument("value", help="New value")

    settings_sub.add_parser("reset", help="Delete settings.json and revert to defaults")

    args = p.parse_args()

    if args.cmd == "init":
        cmd_init(args)
    elif args.cmd == "peer":
        if args.peer_cmd == "add":
            cmd_peer_add(args)
        elif args.peer_cmd == "list":
            cmd_peer_list(args)
    elif args.cmd == "start":
        cmd_start(args)
    elif args.cmd == "status":
        cmd_status(args)
    elif args.cmd == "autosetup":
        cmd_autosetup(args)
    elif args.cmd == "check":
        cmd_check(args)
    elif args.cmd == "node":
        if args.node_cmd == "block":
            cmd_node_block(args)
        elif args.node_cmd == "unblock":
            cmd_node_unblock(args)
        elif args.node_cmd == "pin":
            cmd_node_pin(args)
        elif args.node_cmd == "unpin":
            cmd_node_unpin(args)
        elif args.node_cmd == "list":
            cmd_node_list(args)
    elif args.cmd == "plugin":
        if args.plugin_cmd == "list":
            cmd_plugin_list(args)
        elif args.plugin_cmd == "add":
            cmd_plugin_add(args)
        elif args.plugin_cmd == "remove":
            cmd_plugin_remove(args)
    elif args.cmd == "settings":
        if args.settings_cmd == "show":
            cmd_settings_show(args)
        elif args.settings_cmd == "set":
            cmd_settings_set(args)
        elif args.settings_cmd == "reset":
            cmd_settings_reset(args)


if __name__ == "__main__":
    main()
