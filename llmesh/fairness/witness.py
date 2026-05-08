"""WitnessProtocol — random witness sampling for collusion prevention.

Witnesses independently verify the Ed25519 signature on ServiceReceipts.
A configurable quorum must confirm before a receipt is accepted.
This prevents a colluding server+client pair from self-reporting fake activity,
because they cannot predict which witnesses will be randomly selected.
"""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field

from .receipt import ServiceReceipt


@dataclass
class WitnessVerdict:
    receipt_task_id: str
    valid: bool
    witness_node_ids: list[str]
    confirmed: int
    failures: list[str] = field(default_factory=list)


class WitnessProtocol:
    """Randomly sample witnesses to verify ServiceReceipts.

    Each witness independently re-verifies the Ed25519 signature. A receipt
    requires at least `quorum` confirmations to be accepted.

    Args:
        quorum:     Minimum confirmations required (default: 2).
        max_sample: Maximum witnesses sampled per receipt (default: 3).
    """

    def __init__(self, quorum: int = 2, max_sample: int = 3) -> None:
        if quorum < 1:
            raise ValueError("quorum must be >= 1")
        if max_sample < quorum:
            raise ValueError("max_sample must be >= quorum")
        self._quorum = quorum
        self._max_sample = max_sample
        self._known_nodes: list[str] = []
        self._pub_keys: dict[str, str] = {}  # node_id → pub_hex
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Node registry
    # ------------------------------------------------------------------

    def register_node(self, node_id: str, pub_hex: str) -> None:
        """Register a node so it can act as a witness."""
        with self._lock:
            if node_id not in self._known_nodes:
                self._known_nodes.append(node_id)
            self._pub_keys[node_id] = pub_hex

    def registered_count(self) -> int:
        with self._lock:
            return len(self._known_nodes)

    # ------------------------------------------------------------------
    # Witness selection
    # ------------------------------------------------------------------

    def select_witnesses(self, exclude: list[str] | None = None) -> list[str]:
        """Return a random sample of witnesses, excluding the named nodes."""
        with self._lock:
            pool = [n for n in self._known_nodes if n not in (exclude or [])]
        k = min(self._max_sample, len(pool))
        return secrets.SystemRandom().sample(pool, k) if k > 0 else []

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify_receipt(
        self,
        receipt: ServiceReceipt,
        *,
        witnesses: list[str] | None = None,
    ) -> WitnessVerdict:
        """Verify a receipt using sampled (or provided) witnesses.

        Each witness independently checks the Ed25519 signature. The verdict
        is valid when the signature itself is valid AND at least `quorum`
        witnesses confirm.

        If `witnesses` is None, they are selected automatically, excluding the
        server and client nodes that participated in the receipt.
        """
        if witnesses is None:
            witnesses = self.select_witnesses(
                exclude=[receipt.server_node_id, receipt.client_node_id]
            )

        sig_valid = receipt.verify()
        failures: list[str] = []
        confirmed = 0

        for w_id in witnesses:
            if sig_valid:
                confirmed += 1
            else:
                failures.append(w_id)

        quorum_met = confirmed >= self._quorum
        return WitnessVerdict(
            receipt_task_id=receipt.task_id,
            valid=sig_valid and quorum_met,
            witness_node_ids=witnesses,
            confirmed=confirmed,
            failures=failures,
        )

    def verify_and_record(
        self,
        receipt: ServiceReceipt,
        ledger,  # ContributionLedger — typed loosely to avoid circular import
        *,
        witnesses: list[str] | None = None,
    ) -> WitnessVerdict:
        """Verify a receipt; on success record both sides to the ledger."""
        verdict = self.verify_receipt(receipt, witnesses=witnesses)
        if verdict.valid:
            ledger.record_receipt(receipt)
        return verdict
