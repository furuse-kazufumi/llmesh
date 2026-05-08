"""AdapterRegistry — create protocol adapters by name.

Built-in adapters (http, tcp, udp) are auto-registered on import.

External adapters can be loaded from any installed Python package::

    # Runtime registration
    AdapterRegistry.register("grpc", MyGRPCAdapter)

    # Plugin loading by dotted spec: "module.path:ClassName=protocol_name"
    AdapterRegistry.load_plugin("mypackage.adapters:GRPCAdapter=grpc")

    # Then use as normal
    adapter = AdapterRegistry.create("grpc", timeout=10)

Security invariants:
- No shell=True, eval, exec, or pickle in this module
- Plugin loading uses importlib only — no exec/eval of user strings
"""
from __future__ import annotations

import importlib
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .adapter import ProtocolAdapter


class AdapterRegistry:
    """Maps protocol names to ProtocolAdapter classes.

    Built-in adapters (http, tcp, udp) are registered automatically
    when this module is imported. Custom adapters can be added::

        AdapterRegistry.register("grpc", MyGRPCAdapter)
        adapter = AdapterRegistry.create("grpc", timeout=10)
    """

    _registry: dict[str, type["ProtocolAdapter"]] = {}
    # Tracks which names were loaded from external plugin specs
    _plugin_specs: dict[str, str] = {}   # protocol_name -> "module:Class=name"

    @classmethod
    def register(cls, name: str, adapter_cls: type["ProtocolAdapter"]) -> None:
        """Register *adapter_cls* under *name*."""
        cls._registry[name] = adapter_cls

    @classmethod
    def load_plugin(cls, spec: str) -> str:
        """Load and register an adapter from a dotted spec string.

        Spec format: ``"module.path:ClassName=protocol_name"``

        Example::

            AdapterRegistry.load_plugin("mypackage.adapters:GRPCAdapter=grpc")

        Args:
            spec: ``"module:ClassName=protocol_name"``

        Returns:
            The protocol name that was registered.

        Raises:
            ValueError:    Malformed spec string.
            ImportError:   Module cannot be imported.
            AttributeError: Class not found in module.
            TypeError:     Class is not a ProtocolAdapter subclass.
        """
        from .adapter import ProtocolAdapter as _PA

        if "=" not in spec or ":" not in spec:
            raise ValueError(
                f"Plugin spec must be 'module:ClassName=protocol_name', got {spec!r}"
            )
        module_class, _, protocol_name = spec.rpartition("=")
        protocol_name = protocol_name.strip()
        if ":" not in module_class:
            raise ValueError(
                f"Plugin spec must be 'module:ClassName=protocol_name', got {spec!r}"
            )
        module_name, _, class_name = module_class.rpartition(":")
        module_name = module_name.strip()
        class_name  = class_name.strip()

        module = importlib.import_module(module_name)
        adapter_cls = getattr(module, class_name)

        if not (isinstance(adapter_cls, type) and issubclass(adapter_cls, _PA)):
            raise TypeError(
                f"{spec!r}: {class_name!r} is not a ProtocolAdapter subclass"
            )

        cls._registry[protocol_name] = adapter_cls
        cls._plugin_specs[protocol_name] = spec
        return protocol_name

    @classmethod
    def create(cls, protocol: str, **kwargs: Any) -> "ProtocolAdapter":
        """Instantiate the adapter for *protocol*, forwarding **kwargs**.

        Raises KeyError if *protocol* is not registered.
        """
        if protocol not in cls._registry:
            available = sorted(cls._registry)
            raise KeyError(
                f"Unknown protocol {protocol!r}. Available: {available}"
            )
        return cls._registry[protocol](**kwargs)

    @classmethod
    def available(cls) -> list[str]:
        """Return sorted list of registered protocol names."""
        return sorted(cls._registry)

    @classmethod
    def plugin_specs(cls) -> dict[str, str]:
        """Return a snapshot of {protocol_name: spec} for all loaded plugins."""
        return dict(cls._plugin_specs)

    @classmethod
    def load_entrypoints(cls) -> list[str]:
        """Load adapters declared as ``llmesh.adapters`` entry-points.

        Third-party packages declare adapters in ``pyproject.toml``::

            [project.entry-points."llmesh.adapters"]
            grpc = "mypackage.adapters:GRPCAdapter"

        Returns:
            List of protocol names that were successfully loaded.
            Entry-points that fail to import or are not ProtocolAdapter
            subclasses are skipped silently.
        """
        from .adapter import ProtocolAdapter as _PA

        loaded: list[str] = []
        try:
            eps = entry_points(group="llmesh.adapters")
        except Exception:
            return loaded

        for ep in eps:
            try:
                adapter_cls = ep.load()
                if isinstance(adapter_cls, type) and issubclass(adapter_cls, _PA):
                    cls._registry[ep.name] = adapter_cls
                    loaded.append(ep.name)
            except Exception:
                pass
        return loaded

    @classmethod
    def unregister(cls, name: str) -> None:
        """Remove *name* from the registry (mainly for testing)."""
        cls._registry.pop(name, None)
        cls._plugin_specs.pop(name, None)
