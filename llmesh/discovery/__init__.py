"""P2P node discovery via HTTP registry and DNS-SD for LLMesh."""
from .client import DiscoveryClient, DiscoveryError
from .dns_sd import DnsSdAnnouncer, DnsSdConfig
from .registry import NodeEntry, NodeRegistry, RegistryError

__all__ = [
    "NodeEntry",
    "NodeRegistry",
    "RegistryError",
    "DiscoveryClient",
    "DiscoveryError",
    "DnsSdAnnouncer",
    "DnsSdConfig",
]
