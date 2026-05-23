"""MCP JSON-RPC 2.0 stdio server for Claude Code integration (v1.2.0).

Launched via:
    python -m llmesh serve-mcp

Privacy pipeline (PromptFirewall -> PrivacySummarizer) applies to every
tools/call invocation. Nonces are generated server-side so Claude Code
callers need not supply one.

MCP stdio transport: Content-Length framing over stdin/stdout.

Vision support (v1.2.0): tools/call accepts an optional `image_base64`
argument (base64-encoded PNG/JPEG). The image is routed through
ImageFirewall -> ImageSummarizer before reaching the LLM backend.
Requires: pip install llmesh[vision]

Security invariants:
- No shell=True, eval, exec, or pickle.
- Raw L4 prompts/images are blocked; L3 content is summarized first.
- All subprocess calls use list-based arguments.
- Raw image pixels are never stored after classification.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import uuid
from typing import Any

from ..classifier.data_level import DataLevel
from ..llm.backend import BackendError, LLMBackend
from ..llm.llamacpp import LlamaCppBackend
from ..llm.ollama import OllamaBackend
from ..privacy.firewall import PromptFirewall
from ..privacy.image_firewall import ImageFirewall
from ..privacy.image_summarizer import ImageSummarizer
from ..privacy.summarizer import PrivacySummarizer
from .schemas import TOOL_SCHEMAS
from .validator import OutputValidator, ValidationError

_SERVER_NAME = "llmesh"
_SERVER_VERSION = "1.2.0"

# MCP tool input schema — prompt is required; image_base64 is optional.
_MCP_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "Task prompt for the LLM",
        },
        "image_base64": {
            "type": "string",
            "description": (
                "Optional base64-encoded PNG or JPEG image. "
                "Routed through ImageFirewall before reaching the LLM. "
                "Requires llmesh[vision] (Pillow)."
            ),
        },
    },
    "required": ["prompt"],
}

_TOOL_DESCRIPTIONS: dict[str, str] = {
    "generate_code": "Generate code from a natural-language description",
    "review_code":   "Review code for bugs, security issues, and style",
    "generate_tests": "Generate tests for the provided code",
    "critique_output": "Critique and score a candidate LLM output",
}

_ALLOWED_TOOLS = set(TOOL_SCHEMAS.keys())


# ---------------------------------------------------------------------------
# Transport helpers
# ---------------------------------------------------------------------------

def _read_message(stdin) -> dict[str, Any] | None:
    """Read one JSON-RPC 2.0 message using Content-Length framing."""
    headers: dict[bytes, bytes] = {}
    while True:
        raw = stdin.readline()
        if not raw:
            return None
        line = raw.strip()
        if not line:
            break
        if b":" in line:
            key, _, val = line.partition(b":")
            headers[key.strip().lower()] = val.strip()

    try:
        length = int(headers.get(b"content-length", 0))
    except ValueError:
        return None
    if length <= 0:
        return None

    body = stdin.read(length)
    if len(body) < length:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def _write_message(stdout, msg: dict[str, Any]) -> None:
    """Write one JSON-RPC 2.0 message using Content-Length framing."""
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    header = b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n"
    stdout.write(header)
    stdout.write(body)
    stdout.flush()


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------

def _ok(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# MCP method handlers
# ---------------------------------------------------------------------------

def _handle_initialize(params: dict[str, Any]) -> dict[str, Any]:
    # 2025-06-18 is the first MCP revision with structured tool output
    # (structuredContent + outputSchema), which the llrepr representation layer
    # rides on. Tool results still co-locate a text block for backwards compat.
    return {
        "protocolVersion": "2025-06-18",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
    }


def _handle_tools_list(_params: dict[str, Any]) -> dict[str, Any]:
    tools = []
    for name in sorted(_ALLOWED_TOOLS):
        tools.append({
            "name": name,
            "description": _TOOL_DESCRIPTIONS.get(name, name),
            "inputSchema": _MCP_INPUT_SCHEMA,
        })
    return {"tools": tools}


def _handle_image(
    image_base64: str,
    img_firewall: ImageFirewall,
    img_summarizer: ImageSummarizer,
) -> tuple[str, bool, str]:
    """Classify and summarise an image argument.

    Returns:
        (description, is_error, error_text)
    """
    try:
        raw = base64.b64decode(image_base64, validate=True)
    except Exception as exc:
        return "", True, f"image_base64_decode_error: {exc}"

    clf = img_firewall.classify_bytes(raw, filename="mcp-image")
    if clf.blocked:
        return "", True, f"image_blocked: {clf.reason}"

    if clf.requires_summarization:
        summary = img_summarizer.summarize(raw, original_level=clf.level)
        if summary.blocked:
            return "", True, f"image_summarization_blocked: {summary.block_reason}"
        return summary.description, False, ""

    # L0/L1 image — pass as placeholder (pixels not forwarded)
    return f"[Image: {clf.width}x{clf.height} {clf.format}]", False, ""


def _handle_tools_call(
    params: dict[str, Any],
    firewall: PromptFirewall,
    summarizer: PrivacySummarizer,
    llm: LLMBackend,
    validator: OutputValidator,
    img_firewall: ImageFirewall | None = None,
    img_summarizer: ImageSummarizer | None = None,
) -> dict[str, Any]:
    tool_name = str(params.get("name", ""))
    if tool_name not in _ALLOWED_TOOLS:
        return {"content": [{"type": "text", "text": f"unknown tool: {tool_name}"}], "isError": True}

    arguments = params.get("arguments") or {}
    prompt = str(arguments.get("prompt", ""))
    image_b64: str = arguments.get("image_base64", "") or ""

    task_id = str(uuid.uuid4())
    server_nonce = secrets.token_hex(16)
    node_id = "mcp-stdio"

    # Image pipeline (v1.2.0)
    image_description = ""
    if image_b64:
        _img_fw = img_firewall or ImageFirewall()
        _img_sum = img_summarizer or ImageSummarizer()
        image_description, is_err, err_text = _handle_image(image_b64, _img_fw, _img_sum)
        if is_err:
            return {"content": [{"type": "text", "text": err_text}], "isError": True}

    # Combine image description with text prompt
    if image_description:
        combined_prompt = f"{prompt}\n\n[Image context: {image_description}]" if prompt else image_description
    else:
        combined_prompt = prompt

    # Text privacy pipeline
    fw = firewall.classify(combined_prompt, node_id=node_id, task_id=task_id)
    if fw.blocked:
        return {
            "content": [{"type": "text", "text": f"blocked: {fw.reason}"}],
            "isError": True,
        }

    effective_prompt = combined_prompt
    if fw.requires_summarization:
        try:
            sr = summarizer.summarize_text(combined_prompt, DataLevel(fw.level))
            effective_prompt = sr.summary
        except Exception:
            return {
                "content": [{"type": "text", "text": "l3_summarization_failed"}],
                "isError": True,
            }

    # LLM invocation
    backend_body: dict[str, Any] = {
        "task_id": task_id,
        "caller_nonce": server_nonce,
        "prompt": effective_prompt,
    }
    try:
        llm_result = llm.invoke(tool_name, backend_body)
    except BackendError as exc:
        return {
            "content": [{"type": "text", "text": f"backend_error: {exc}"}],
            "isError": True,
        }

    llm_result.setdefault("task_id", task_id)
    llm_result.setdefault("caller_nonce_echo", server_nonce)

    # Output validation
    try:
        validated = validator.validate(
            json.dumps(llm_result),
            tool_name,
            server_nonce,
            node_id=node_id,
            task_id=task_id,
        )
    except ValidationError as exc:
        return {
            "content": [{"type": "text", "text": f"validation_error: {exc.reason}"}],
            "isError": True,
        }

    return {
        "content": [{"type": "text", "text": json.dumps(validated, ensure_ascii=False)}],
        "isError": False,
    }


# ---------------------------------------------------------------------------
# Server loop
# ---------------------------------------------------------------------------

def _build_pipeline() -> tuple[PromptFirewall, PrivacySummarizer, LLMBackend, OutputValidator]:
    firewall = PromptFirewall()
    summarizer = PrivacySummarizer()
    validator = OutputValidator()

    backend_name = os.environ.get("LLMESH_BACKEND", "ollama").lower()
    url = os.environ.get("LLMESH_BACKEND_URL", "")
    model = os.environ.get("LLMESH_MODEL", "")
    kw: dict[str, Any] = {}
    if url:
        kw["base_url"] = url
    if model:
        kw["model"] = model
    llm: LLMBackend = LlamaCppBackend(**kw) if backend_name == "llamacpp" else OllamaBackend(**kw)

    return firewall, summarizer, llm, validator


def run_stdio_server(
    stdin=None,
    stdout=None,
    *,
    _pipeline: tuple | None = None,
) -> None:
    """Run the MCP stdio server until stdin closes.

    Args:
        stdin:  Binary stdin stream (default: sys.stdin.buffer).
        stdout: Binary stdout stream (default: sys.stdout.buffer).
        _pipeline: Inject (firewall, summarizer, llm, validator) for testing.
    """
    _in = stdin if stdin is not None else sys.stdin.buffer
    _out = stdout if stdout is not None else sys.stdout.buffer

    if _pipeline is not None:
        firewall, summarizer, llm, validator = _pipeline
    else:
        firewall, summarizer, llm, validator = _build_pipeline()

    while True:
        msg = _read_message(_in)
        if msg is None:
            break

        req_id = msg.get("id")
        method = str(msg.get("method", ""))
        params = msg.get("params") or {}

        # Notifications (no id) — acknowledge silently
        if req_id is None:
            continue

        if method == "initialize":
            _write_message(_out, _ok(req_id, _handle_initialize(params)))

        elif method == "ping":
            _write_message(_out, _ok(req_id, {}))

        elif method == "tools/list":
            _write_message(_out, _ok(req_id, _handle_tools_list(params)))

        elif method == "tools/call":
            result = _handle_tools_call(
                params, firewall, summarizer, llm, validator,
                img_firewall=ImageFirewall(),
                img_summarizer=ImageSummarizer(),
            )
            _write_message(_out, _ok(req_id, result))

        else:
            _write_message(_out, _err(req_id, -32601, f"Method not found: {method}"))
