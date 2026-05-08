"""LocalFileAdapter — drop-folder LLM task processing (v1.0.1).

Place a ``*.prompt.txt`` file in ``in_dir``; the adapter reads it,
runs it through the privacy pipeline and LLM backend, and writes
``*.result.txt`` to ``out_dir``.  Processed prompts are moved to
``in_dir/processed/`` so the originals are never lost.

File naming convention::

    in_dir/hello.prompt.txt          <- drop here
    out_dir/hello.result.txt         <- result appears here
    in_dir/processed/hello.prompt.txt  <- original archived

Tool name selection:
    hello.prompt.txt                 -> default_tool (default: generate_code)
    hello.generate_code.prompt.txt   -> generate_code
    hello.review_code.prompt.txt     -> review_code

Security:
    - No shell=True, eval, exec, or pickle.
    - Prompt size capped at _MAX_PROMPT_BYTES.
    - Full privacy pipeline: PromptFirewall -> PrivacySummarizer -> LLM -> OutputValidator.
    - Only .prompt.txt files are processed; other files are ignored.
    - out_dir and processed_dir are created automatically; no path traversal
      is possible because filenames are re-derived from Path.stem only.

Dependencies: watchdog>=3.0  (pip install llmesh[localfile])
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .adapter import MessageHandler, ProtocolAdapter, TransportError
from .message import MessageType, NodeAddress, UnifiedMessage

if TYPE_CHECKING:
    pass

try:
    from watchdog.events import FileClosedEvent, FileCreatedEvent, FileSystemEventHandler
    from watchdog.observers import Observer
    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False
    Observer = None  # type: ignore[assignment,misc]
    FileSystemEventHandler = object  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

_MAX_PROMPT_BYTES  = 256 * 1024   # 256 KiB
_MAX_IMAGE_BYTES   = 10 * 1024 * 1024  # 10 MiB (matches ImageFirewall default)
_PROMPT_SUFFIX     = ".prompt.txt"
_RESULT_SUFFIX     = ".result.txt"
_PROCESSED_DIR     = "processed"
_DEFAULT_TOOL      = "generate_code"
_IMAGE_PROMPT_SUFFIXES = (".prompt.png", ".prompt.jpg", ".prompt.jpeg", ".prompt.webp")


def _derive_tool_name(stem: str, default: str) -> str:
    """Extract tool name from file stem.

    ``hello``                 -> *default*
    ``hello.generate_code``   -> ``generate_code``
    """
    from ..mcp.schemas import TOOL_SCHEMAS
    parts = stem.rsplit(".", 1)
    if len(parts) == 2 and parts[1] in TOOL_SCHEMAS:
        return parts[1]
    return default


def _safe_stem(path: Path) -> str:
    """Return the stem of *path* with the .prompt(.ext) suffix removed."""
    name = path.name
    if name.endswith(_PROMPT_SUFFIX):
        return name[: -len(_PROMPT_SUFFIX)]
    for img_sfx in _IMAGE_PROMPT_SUFFIXES:
        if name.endswith(img_sfx):
            return name[: -len(img_sfx)]
    return path.stem


def _is_image_prompt(path: str) -> bool:
    """Return True if *path* ends with a supported image-prompt suffix."""
    return any(path.endswith(s) for s in _IMAGE_PROMPT_SUFFIXES)


class _PromptEventHandler(FileSystemEventHandler):  # type: ignore[misc]
    """Watchdog event handler that processes new .prompt.txt files."""

    def __init__(self, adapter: "LocalFileAdapter") -> None:
        super().__init__()
        self._adapter = adapter
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def _should_process(self, src_path: str) -> bool:
        if not (src_path.endswith(_PROMPT_SUFFIX) or _is_image_prompt(src_path)):
            return False
        with self._lock:
            if src_path in self._seen:
                return False
            self._seen.add(src_path)
        return True

    def on_created(self, event: Any) -> None:  # type: ignore[override]
        if getattr(event, "is_directory", False):
            return
        src = str(event.src_path)
        if self._should_process(src):
            self._adapter._schedule_file(src)

    def on_closed(self, event: Any) -> None:  # type: ignore[override]
        # Some platforms fire on_closed instead of on_created for atomic writes
        if getattr(event, "is_directory", False):
            return
        src = str(event.src_path)
        if self._should_process(src):
            self._adapter._schedule_file(src)


class LocalFileAdapter(ProtocolAdapter):
    """Drop-folder adapter: processes .prompt.txt files through the LLM pipeline.

    Args:
        in_dir:       Directory to watch for incoming ``.prompt.txt`` files.
        out_dir:      Directory where ``.result.txt`` files are written.
        default_tool: Tool name used when not encoded in the filename.
        pipeline:     Optional ``(firewall, summarizer, llm, validator)`` tuple
                      for dependency injection (testing).  When *None* the
                      adapter builds one from environment variables.
    """

    def __init__(
        self,
        in_dir: str | Path = "llmesh_in",
        out_dir: str | Path = "llmesh_out",
        default_tool: str = _DEFAULT_TOOL,
        pipeline: tuple | None = None,
    ) -> None:
        if not _WATCHDOG_AVAILABLE:
            raise ImportError(
                "watchdog is required for LocalFileAdapter: "
                "pip install llmesh[localfile]"
            )
        self._in_dir = Path(in_dir).resolve()
        self._out_dir = Path(out_dir).resolve()
        self._processed_dir = self._in_dir / _PROCESSED_DIR
        self._default_tool = default_tool
        self._pipeline = pipeline
        self._observer: Any = None
        self._running = False
        self._handler: MessageHandler | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # ProtocolAdapter interface
    # ------------------------------------------------------------------

    @property
    def protocol_name(self) -> str:
        return "localfile"

    @property
    def is_running(self) -> bool:
        return self._running

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self, host: str = "", port: int = 0) -> None:
        """Start watching *in_dir* for prompt files."""
        self._in_dir.mkdir(parents=True, exist_ok=True)
        self._out_dir.mkdir(parents=True, exist_ok=True)
        self._processed_dir.mkdir(parents=True, exist_ok=True)

        self._loop = asyncio.get_event_loop()

        if self._pipeline is None:
            self._pipeline = self._build_pipeline()

        event_handler = _PromptEventHandler(self)
        self._observer = Observer()
        self._observer.schedule(event_handler, str(self._in_dir), recursive=False)
        self._observer.start()
        self._running = True
        logger.info("LocalFileAdapter watching %s → %s", self._in_dir, self._out_dir)

        # Process any files already present in in_dir at startup
        for p in self._in_dir.glob(f"*{_PROMPT_SUFFIX}"):
            self._schedule_file(str(p))
        for sfx in _IMAGE_PROMPT_SUFFIXES:
            for p in self._in_dir.glob(f"*{sfx}"):
                self._schedule_file(str(p))

    async def stop(self) -> None:
        """Stop the file watcher."""
        self._running = False
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        logger.info("LocalFileAdapter stopped")

    async def send(self, message: UnifiedMessage, target: NodeAddress) -> None:
        raise TransportError("LocalFileAdapter is receive-only", protocol="localfile")

    async def broadcast(self, message: UnifiedMessage, targets=None) -> None:
        raise TransportError("LocalFileAdapter is receive-only", protocol="localfile")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_pipeline(self) -> tuple:
        from ..classifier.data_level import DataLevel
        from ..llm.llamacpp import LlamaCppBackend
        from ..llm.ollama import OllamaBackend
        from ..privacy.firewall import PromptFirewall
        from ..privacy.summarizer import PrivacySummarizer
        from ..mcp.validator import OutputValidator

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
        llm = LlamaCppBackend(**kw) if backend_name == "llamacpp" else OllamaBackend(**kw)
        return firewall, summarizer, llm, validator

    def _schedule_file(self, src_path: str) -> None:
        """Dispatch processing to a worker thread to avoid blocking watchdog."""
        target = self._process_image_file if _is_image_prompt(src_path) else self._process_file
        t = threading.Thread(target=target, args=(src_path,), daemon=True)
        t.start()

    def _process_file(self, src_path: str) -> None:
        p = Path(src_path)
        if not p.exists():
            return

        stem = _safe_stem(p)
        tool_name = _derive_tool_name(stem, self._default_tool)

        # Read prompt
        try:
            raw = p.read_bytes()
        except OSError as exc:
            logger.warning("Cannot read %s: %s", p, exc)
            return

        if len(raw) > _MAX_PROMPT_BYTES:
            self._write_error(stem, f"prompt_too_large:{len(raw)}_bytes")
            self._archive(p)
            return

        prompt = raw.decode("utf-8", errors="replace").strip()

        firewall, summarizer, llm, validator = self._pipeline  # type: ignore[misc]
        task_id = str(uuid.uuid4())
        nonce = secrets.token_hex(16)
        node_id = "localfile"

        # Privacy pipeline
        from ..classifier.data_level import DataLevel
        from ..llm.backend import BackendError
        from ..mcp.validator import ValidationError

        fw = firewall.classify(prompt, node_id=node_id, task_id=task_id)
        if fw.blocked:
            self._write_error(stem, f"blocked:{fw.reason}")
            self._archive(p)
            return

        effective = prompt
        if fw.requires_summarization:
            try:
                sr = summarizer.summarize_text(prompt, DataLevel(fw.level))
                effective = sr.summary
            except Exception as exc:
                self._write_error(stem, f"summarization_failed:{exc}")
                self._archive(p)
                return

        backend_body: dict[str, Any] = {
            "task_id": task_id,
            "caller_nonce": nonce,
            "prompt": effective,
        }
        try:
            llm_result = llm.invoke(tool_name, backend_body)
        except BackendError as exc:
            self._write_error(stem, f"backend_error:{exc}")
            self._archive(p)
            return

        llm_result.setdefault("task_id", task_id)
        llm_result.setdefault("caller_nonce_echo", nonce)

        try:
            validated = validator.validate(
                json.dumps(llm_result), tool_name, nonce,
                node_id=node_id, task_id=task_id,
            )
        except ValidationError as exc:
            self._write_error(stem, f"validation_error:{exc.reason}")
            self._archive(p)
            return

        self._write_result(stem, json.dumps(validated, ensure_ascii=False, indent=2))
        self._archive(p)
        logger.info("Processed %s → %s%s", p.name, stem, _RESULT_SUFFIX)

    def _process_image_file(self, src_path: str) -> None:
        """Process an image prompt file through ImageFirewall → ImageSummarizer → LLM."""
        from ..privacy.image_firewall import ImageFirewall
        from ..privacy.image_summarizer import ImageSummarizer
        from ..llm.backend import BackendError
        from ..mcp.validator import ValidationError

        p = Path(src_path)
        if not p.exists():
            return

        stem = _safe_stem(p)
        tool_name = _derive_tool_name(stem, self._default_tool)

        try:
            raw = p.read_bytes()
        except OSError as exc:
            logger.warning("Cannot read image %s: %s", p, exc)
            return

        if len(raw) > _MAX_IMAGE_BYTES:
            self._write_error(stem, f"image_too_large:{len(raw)}_bytes")
            self._archive(p)
            return

        # Optional sidecar text prompt (same stem + .prompt.txt)
        sidecar = self._in_dir / f"{stem}{_PROMPT_SUFFIX}"
        sidecar_text = ""
        if sidecar.exists():
            try:
                sidecar_text = sidecar.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                pass

        # ImageFirewall classification
        img_fw = ImageFirewall()
        clf = img_fw.classify_bytes(raw, filename=p.name)
        if clf.blocked:
            self._write_error(stem, f"image_blocked:{clf.reason}")
            self._archive(p)
            if sidecar.exists():
                self._archive(sidecar)
            return

        # Build effective prompt
        if clf.requires_summarization:
            summarizer = ImageSummarizer()
            summary = summarizer.summarize(raw, original_level=clf.level)
            if summary.blocked:
                self._write_error(stem, f"image_summarization_blocked:{summary.block_reason}")
                self._archive(p)
                if sidecar.exists():
                    self._archive(sidecar)
                return
            effective = summary.description
            if sidecar_text:
                effective = f"{sidecar_text}\n\n[Image description: {effective}]"
        else:
            # L0/L1 image — use description placeholder + sidecar
            effective = sidecar_text or f"[Image file: {p.name}]"

        firewall, text_summarizer, llm, validator = self._pipeline  # type: ignore[misc]
        task_id = str(uuid.uuid4())
        nonce = secrets.token_hex(16)
        node_id = "localfile"

        # Text firewall on the assembled prompt
        fw = firewall.classify(effective, node_id=node_id, task_id=task_id)
        if fw.blocked:
            self._write_error(stem, f"prompt_blocked:{fw.reason}")
            self._archive(p)
            if sidecar.exists():
                self._archive(sidecar)
            return

        if fw.requires_summarization:
            from ..classifier.data_level import DataLevel
            try:
                sr = text_summarizer.summarize_text(effective, DataLevel(fw.level))
                effective = sr.summary
            except Exception as exc:
                self._write_error(stem, f"text_summarization_failed:{exc}")
                self._archive(p)
                if sidecar.exists():
                    self._archive(sidecar)
                return

        backend_body: dict[str, Any] = {
            "task_id": task_id,
            "caller_nonce": nonce,
            "prompt": effective,
        }
        try:
            llm_result = llm.invoke(tool_name, backend_body)
        except BackendError as exc:
            self._write_error(stem, f"backend_error:{exc}")
            self._archive(p)
            if sidecar.exists():
                self._archive(sidecar)
            return

        llm_result.setdefault("task_id", task_id)
        llm_result.setdefault("caller_nonce_echo", nonce)

        try:
            validated = validator.validate(
                json.dumps(llm_result), tool_name, nonce,
                node_id=node_id, task_id=task_id,
            )
        except ValidationError as exc:
            self._write_error(stem, f"validation_error:{exc.reason}")
            self._archive(p)
            if sidecar.exists():
                self._archive(sidecar)
            return

        self._write_result(stem, json.dumps(validated, ensure_ascii=False, indent=2))
        self._archive(p)
        if sidecar.exists():
            self._archive(sidecar)
        logger.info("Processed image %s → %s%s", p.name, stem, _RESULT_SUFFIX)

    def _write_result(self, stem: str, content: str) -> None:
        out = self._out_dir / f"{stem}{_RESULT_SUFFIX}"
        out.write_text(content, encoding="utf-8")

    def _write_error(self, stem: str, reason: str) -> None:
        out = self._out_dir / f"{stem}{_RESULT_SUFFIX}"
        out.write_text(json.dumps({"error": reason}), encoding="utf-8")

    def _archive(self, p: Path) -> None:
        dest = self._processed_dir / p.name
        try:
            p.rename(dest)
        except OSError:
            pass
