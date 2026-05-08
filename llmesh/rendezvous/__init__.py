"""Rendezvous — signed node-discovery service for LLMesh.

Phase 1: endpoints stored in plaintext; signature prevents impersonation.
Phase 2 (future): per-peer ECDH encryption via llmesh.identity.x25519.
"""
from .client import AnnounceError, LookupError, announce, lookup
from .server import make_app

__all__ = ["announce", "lookup", "make_app", "AnnounceError", "LookupError"]
