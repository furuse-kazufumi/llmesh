"""Append-only HMAC audit trace for LLMesh (v0.2.0+).

Each entry is written as a JSONL line and chained with HMAC-SHA256 so that
tampering, deletion, or reordering is detectable via verify_chain().

v0.2.0 additions:
- Cross-process file locking: fcntl (Unix) or msvcrt (Windows).
- Fails closed at construction time when locking is unavailable, unless
  ``LLMESH_UNSAFE_AUDIT_NO_LOCK=1`` is set explicitly for dev environments.
- verify_chain_detailed() returns structured result with first-failure info.

Security constraints:
- Prompt body is NEVER stored. For data_level >= L3 only sha256(prompt) is
  recorded in the ``prompt_sha256`` field.
- The HMAC key is passed at construction time and never written to disk.
- Chain: entry_hmac = HMAC(key, prev_hmac_hex || json(entry_without_hmac))
- Guard failures and lock failures always fail closed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

# ---------------------------------------------------------------------------
# Platform locking
# ---------------------------------------------------------------------------

try:
    import fcntl as _fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

try:
    import msvcrt as _msvcrt
    _HAS_MSVCRT = True
except ImportError:
    _HAS_MSVCRT = False

_LOCKING_AVAILABLE = _HAS_FCNTL or _HAS_MSVCRT


@contextmanager
def _platform_lock(lock_path: Path) -> Generator[None, None, None]:
    """Acquire an exclusive cross-process file lock for the critical section."""
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        if _HAS_FCNTL:
            _fcntl.flock(fd, _fcntl.LOCK_EX)
            try:
                yield
            finally:
                _fcntl.flock(fd, _fcntl.LOCK_UN)
        else:
            # Windows: lock first byte of the lock file as a process mutex.
            # LK_LOCK retries for ~10 s before raising OSError.
            os.lseek(fd, 0, os.SEEK_SET)
            _msvcrt.locking(fd, _msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Verification result
# ---------------------------------------------------------------------------

@dataclass
class VerifyResult:
    """Structured result from verify_chain_detailed()."""
    valid: bool
    entry_count: int
    first_error_seq: int | None   # None when valid
    error_detail: str             # empty when valid

    def __bool__(self) -> bool:
        return self.valid


# ---------------------------------------------------------------------------
# AuditTrace
# ---------------------------------------------------------------------------

class AuditTrace:
    """Append-only HMAC-chained JSONL audit log with cross-process locking.

    Args:
        path: Path to the JSONL audit file.
        hmac_key: Secret key bytes for HMAC chaining.
        unsafe_no_lock: Override for dev environments lacking platform locking.
            Production code must never set this to True.
    """

    def __init__(
        self,
        path: str | Path,
        hmac_key: bytes,
        *,
        unsafe_no_lock: bool = False,
    ) -> None:
        self._path = Path(path)
        self._key = hmac_key
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        self._thread_lock = threading.Lock()

        # Fail closed if platform locking is unavailable and no explicit override.
        if not _LOCKING_AVAILABLE and not unsafe_no_lock:
            raise RuntimeError(
                "audit_locking_unavailable: neither fcntl nor msvcrt found. "
                "Set LLMESH_UNSAFE_AUDIT_NO_LOCK=1 to bypass (dev only)."
            )
        self._use_file_lock = _LOCKING_AVAILABLE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log(
        self,
        event_type: str,
        node_id: str,
        task_id: str,
        policy_decision: str,
        output_sha256: str,
        data_level: int = 0,
        prompt_sha256: str = "",
    ) -> None:
        """Append one entry to the audit log.

        For data_level >= 3 (L3/L4), prompt body must never be stored.
        Only prompt_sha256 is recorded; callers must supply it themselves.
        """
        with self._thread_lock:
            if self._use_file_lock:
                with _platform_lock(self._lock_path):
                    self._append_entry(
                        event_type, node_id, task_id,
                        policy_decision, output_sha256,
                        data_level, prompt_sha256,
                    )
            else:
                self._append_entry(
                    event_type, node_id, task_id,
                    policy_decision, output_sha256,
                    data_level, prompt_sha256,
                )

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    @staticmethod
    def verify_chain(path: str | Path, hmac_key: bytes) -> bool:
        """Verify HMAC chain integrity. Returns True if intact."""
        return bool(AuditTrace.verify_chain_detailed(path, hmac_key))

    @staticmethod
    def verify_chain_detailed(path: str | Path, hmac_key: bytes) -> VerifyResult:
        """Verify HMAC chain and return structured result with failure details."""
        p = Path(path)
        if not p.exists():
            return VerifyResult(valid=False, entry_count=0,
                                first_error_seq=None, error_detail="file_not_found")

        try:
            prev_hmac = "0" * 64
            expected_seq = 0
            count = 0

            with p.open("r", encoding="utf-8") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue

                    count += 1
                    entry = json.loads(line)
                    recorded_hmac = entry.pop("entry_hmac", None)
                    if recorded_hmac is None:
                        return VerifyResult(
                            valid=False, entry_count=count,
                            first_error_seq=expected_seq,
                            error_detail="missing_entry_hmac",
                        )

                    if entry.get("seq_no") != expected_seq:
                        return VerifyResult(
                            valid=False, entry_count=count,
                            first_error_seq=expected_seq,
                            error_detail=f"seq_mismatch:got={entry.get('seq_no')}",
                        )

                    entry_json = json.dumps(entry, sort_keys=True, ensure_ascii=False)
                    chain_input = (prev_hmac + entry_json).encode()
                    expected_hmac = hmac.new(
                        hmac_key, chain_input, hashlib.sha256
                    ).hexdigest()

                    if not hmac.compare_digest(recorded_hmac, expected_hmac):
                        return VerifyResult(
                            valid=False, entry_count=count,
                            first_error_seq=expected_seq,
                            error_detail="hmac_mismatch",
                        )

                    prev_hmac = recorded_hmac
                    expected_seq += 1

            if count == 0:
                return VerifyResult(valid=False, entry_count=0,
                                    first_error_seq=None, error_detail="empty_file")

            return VerifyResult(valid=True, entry_count=count,
                                first_error_seq=None, error_detail="")

        except Exception as exc:
            return VerifyResult(valid=False, entry_count=0,
                                first_error_seq=None,
                                error_detail=f"exception:{exc}")

    # ------------------------------------------------------------------
    # Internal append (must be called within lock)
    # ------------------------------------------------------------------

    def _append_entry(
        self,
        event_type: str,
        node_id: str,
        task_id: str,
        policy_decision: str,
        output_sha256: str,
        data_level: int,
        prompt_sha256: str,
    ) -> None:
        seq_no = self._next_seq_no()
        entry: dict = {
            "seq_no": seq_no,
            "event_type": event_type,
            "node_id": node_id,
            "task_id": task_id,
            "policy_decision": policy_decision,
            "output_sha256": output_sha256,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if data_level >= 3 and prompt_sha256:
            entry["prompt_sha256"] = prompt_sha256

        prev_hmac = self._load_last_hmac()
        entry_json = json.dumps(entry, sort_keys=True, ensure_ascii=False)
        chain_input = (prev_hmac + entry_json).encode()
        entry_hmac = hmac.new(self._key, chain_input, hashlib.sha256).hexdigest()
        entry["entry_hmac"] = entry_hmac

        line = json.dumps(entry, sort_keys=True, ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _next_seq_no(self) -> int:
        if not self._path.exists():
            return 0
        count = 0
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    count += 1
        return count

    def _load_last_hmac(self) -> str:
        last_hmac = "0" * 64
        if not self._path.exists():
            return last_hmac
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        entry = json.loads(line)
                        h = entry.get("entry_hmac", "")
                        if h:
                            last_hmac = h
        except Exception:
            pass
        return last_hmac
