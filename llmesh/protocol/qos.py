"""QoS helpers: deadline checking and priority ordering."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .message import UnifiedMessage


def is_expired(deadline: float | None) -> bool:
    """Return True if *deadline* is set and has already passed."""
    return deadline is not None and time.time() > deadline


def check_deadline(msg: "UnifiedMessage") -> None:
    """Raise DeadlineExpiredError if the message deadline has passed."""
    if is_expired(msg.deadline):
        raise DeadlineExpiredError(
            f"message {msg.id!r} deadline {msg.deadline} already passed"
        )


class DeadlineExpiredError(Exception):
    """Raised when a message's deadline has passed before it could be sent."""
