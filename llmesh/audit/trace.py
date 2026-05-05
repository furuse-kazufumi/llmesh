"""Append-only HMAC audit trace for LLMesh.

Each entry is written as a JSONL line and chained with HMAC-SHA256 so that
tampering, deletion, or reordering is detectable via verify_chain().

Security constraints:
- Prompt body is NEVER stored. For data_level >= L3 only sha256(prompt) is
  recorded in the ``output_sha256`` field.
- The HMAC key is passed at construction time and never written to disk.
- Chain integrity: entry_hmac = HMAC(key, prev_hmac_hex || json(entry_without_hmac))
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class AuditTrace:
    """Append-only HMAC-chained JSONL audit log."""

    def __init__(self, path: str | Path, hmac_key: bytes) -> None:
        self._path = Path(path)
        self._key = hmac_key
        self._lock = threading.Lock()
        # Load last hmac from existing file to allow resuming a chain
        self._prev_hmac: str = "0" * 64
        if self._path.exists():
            self._prev_hmac = self._load_last_hmac()

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
        with self._lock:
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

            # L3/L4: record only the sha256 of the prompt — never the body
            if data_level >= 3 and prompt_sha256:
                entry["prompt_sha256"] = prompt_sha256

            # Compute HMAC chain link
            entry_json = json.dumps(entry, sort_keys=True, ensure_ascii=False)
            chain_input = (self._prev_hmac + entry_json).encode()
            entry_hmac = hmac.new(self._key, chain_input, hashlib.sha256).hexdigest()
            entry["entry_hmac"] = entry_hmac

            line = json.dumps(entry, sort_keys=True, ensure_ascii=False)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

            self._prev_hmac = entry_hmac

    @staticmethod
    def verify_chain(path: str | Path, hmac_key: bytes) -> bool:
        """Verify the HMAC chain of an audit log file.

        Returns True if the chain is intact. Returns False if any entry's
        HMAC is wrong, the sequence numbers are non-contiguous, or the file
        is unreadable.
        """
        p = Path(path)
        if not p.exists():
            return False

        try:
            prev_hmac = "0" * 64
            expected_seq = 0
            found_entries = 0

            with p.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue

                    found_entries += 1
                    entry = json.loads(line)
                    recorded_hmac = entry.pop("entry_hmac", None)
                    if recorded_hmac is None:
                        return False

                    # Sequence number check
                    if entry.get("seq_no") != expected_seq:
                        return False
                    expected_seq += 1

                    # Recompute HMAC
                    entry_json = json.dumps(entry, sort_keys=True, ensure_ascii=False)
                    chain_input = (prev_hmac + entry_json).encode()
                    expected_hmac = hmac.new(
                        hmac_key, chain_input, hashlib.sha256
                    ).hexdigest()

                    if not hmac.compare_digest(recorded_hmac, expected_hmac):
                        return False

                    prev_hmac = recorded_hmac

            # An empty file (zero entries) is not a valid audit log
            if found_entries == 0:
                return False

            return True

        except Exception:
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _next_seq_no(self) -> int:
        """Return next sequence number by counting existing lines."""
        if not self._path.exists():
            return 0
        count = 0
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    count += 1
        return count

    def _load_last_hmac(self) -> str:
        """Read the last entry_hmac from an existing log file."""
        last_hmac = "0" * 64
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
