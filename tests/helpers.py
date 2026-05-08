"""Shared test helpers (importable as a regular module)."""
from __future__ import annotations

import socket


def _alloc_port(sock_type: int = socket.SOCK_STREAM) -> int:
    """Allocate a free local port. Call directly when a test needs multiple ports."""
    with socket.socket(socket.AF_INET, sock_type) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
