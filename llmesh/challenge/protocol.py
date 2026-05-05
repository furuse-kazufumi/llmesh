"""Challenge-Response protocol for LLMesh node capability verification.

Flow:
  1. Challenger calls issue(difficulty) → ChallengeToken (HMAC-signed, TTL-bound)
  2. Token is sent to the target node along with the task prompt
  3. Target node invokes its LLM and returns a response dict
  4. Challenger calls verify(token, response) → ChallengeResult

Security:
  - Token is HMAC-SHA256 signed with a per-protocol secret key
  - TTL enforced: expired tokens are rejected
  - Token ID is stored to prevent replay (one-use)
  - No shell=True, eval, exec, pickle anywhere in this module
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any

from .bank import TASK_BANK, TASK_BY_ID, ChallengeTask, Difficulty
from .evaluator import ChallengeEvaluator, ChallengeResult


class ProtocolError(Exception):
    """Raised when a challenge token is invalid, expired, or replayed."""


@dataclass
class ChallengeToken:
    """Signed challenge token issued by ChallengeProtocol.

    Attributes:
        token_id:   Random 16-byte hex identifier (one-use).
        task_id:    The task the node must solve.
        issued_at:  Unix timestamp (float) when the token was issued.
        expires_at: Unix timestamp after which the token is invalid.
        hmac_sig:   HMAC-SHA256 hex signature over the canonical payload.
    """

    token_id: str
    task_id: str
    issued_at: float
    expires_at: float
    hmac_sig: str

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "task_id": self.task_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "hmac_sig": self.hmac_sig,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChallengeToken":
        return cls(
            token_id=d["token_id"],
            task_id=d["task_id"],
            issued_at=float(d["issued_at"]),
            expires_at=float(d["expires_at"]),
            hmac_sig=d["hmac_sig"],
        )

    def _signable(self) -> bytes:
        """Canonical bytes that are HMAC-signed — excludes hmac_sig."""
        payload = {
            "token_id": self.token_id,
            "task_id": self.task_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }
        return json.dumps(payload, sort_keys=True).encode()


class ChallengeProtocol:
    """Issues and verifies challenge tokens for node capability assessment.

    Args:
        secret_key: 32+ byte secret used for HMAC signing.
                    If None, a random key is generated (ephemeral per process).
        ttl_seconds: How long a token remains valid after issuance.
        pass_threshold: Minimum score for a challenge to count as passed.
    """

    def __init__(
        self,
        secret_key: bytes | None = None,
        ttl_seconds: int = 300,
        pass_threshold: float = 0.6,
    ) -> None:
        self._key = secret_key or os.urandom(32)
        self._ttl = ttl_seconds
        self._evaluator = ChallengeEvaluator(pass_threshold=pass_threshold)
        # One-use token registry: token_id → True (used)
        self._used_tokens: set[str] = set()

    # ------------------------------------------------------------------
    # Issue
    # ------------------------------------------------------------------

    def issue(
        self,
        difficulty: Difficulty = Difficulty.EASY,
        task_id: str | None = None,
    ) -> ChallengeToken:
        """Issue a signed challenge token.

        Args:
            difficulty: Filter tasks by difficulty level.
            task_id: Pin to a specific task (overrides difficulty filter).

        Returns:
            A ChallengeToken the challenger can send to the target node.
        """
        task = self._select_task(difficulty, task_id)
        now = time.time()
        token = ChallengeToken(
            token_id=os.urandom(16).hex(),
            task_id=task.id,
            issued_at=now,
            expires_at=now + self._ttl,
            hmac_sig="",
        )
        token.hmac_sig = self._sign(token)
        return token

    def get_task(self, token: ChallengeToken) -> ChallengeTask:
        """Return the ChallengeTask referenced by the token (after validation).

        Raises ProtocolError if the token is invalid or expired.
        Does NOT mark the token as used — call verify() for that.
        """
        self._validate_token(token, mark_used=False)
        return TASK_BY_ID[token.task_id]

    # ------------------------------------------------------------------
    # Verify
    # ------------------------------------------------------------------

    def verify(
        self, token: ChallengeToken, response: dict[str, Any]
    ) -> ChallengeResult:
        """Verify a token and evaluate the node's response.

        Raises ProtocolError if the token is invalid, expired, or replayed.
        Returns a ChallengeResult with score and pass/fail verdict.
        """
        self._validate_token(token, mark_used=True)
        task = TASK_BY_ID[token.task_id]
        return self._evaluator.evaluate(task, response)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _select_task(
        self, difficulty: Difficulty, task_id: str | None
    ) -> ChallengeTask:
        if task_id is not None:
            if task_id not in TASK_BY_ID:
                raise ProtocolError(f"unknown task_id: {task_id!r}")
            return TASK_BY_ID[task_id]
        pool = [t for t in TASK_BANK if t.difficulty == difficulty]
        if not pool:
            raise ProtocolError(f"no tasks available for difficulty={difficulty!r}")
        return random.choice(pool)

    def _sign(self, token: ChallengeToken) -> str:
        return hmac.new(self._key, token._signable(), hashlib.sha256).hexdigest()

    def _validate_token(self, token: ChallengeToken, *, mark_used: bool) -> None:
        # 1. HMAC verification (fail-closed)
        expected = self._sign(token)
        if not hmac.compare_digest(expected, token.hmac_sig):
            raise ProtocolError("token_signature_invalid")

        # 2. Expiry
        if token.is_expired():
            raise ProtocolError(
                f"token_expired (expired_at={token.expires_at:.0f}, "
                f"now={time.time():.0f})"
            )

        # 3. Replay prevention
        if token.token_id in self._used_tokens:
            raise ProtocolError(f"token_replayed: {token.token_id}")

        # 4. Task existence
        if token.task_id not in TASK_BY_ID:
            raise ProtocolError(f"unknown_task_id: {token.task_id!r}")

        if mark_used:
            self._used_tokens.add(token.token_id)
