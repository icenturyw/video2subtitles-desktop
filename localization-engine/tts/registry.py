"""Thread-safe registry for constructing TTS providers by stable name."""
from __future__ import annotations

import threading
from typing import Callable, Dict, Iterable

from tts.base import BaseTTSProvider, TTSCapabilities


ProviderFactory = Callable[..., BaseTTSProvider]


class ProviderRegistry:
    def __init__(self) -> None:
        self._factories: Dict[str, ProviderFactory] = {}
        self._aliases: Dict[str, str] = {}
        self._capabilities: Dict[str, TTSCapabilities] = {}
        self._lock = threading.RLock()

    @staticmethod
    def normalize(name: str) -> str:
        return str(name or "").strip().lower().replace("_", "-")

    def register(
        self,
        name: str,
        factory: ProviderFactory,
        *,
        aliases: Iterable[str] = (),
        capabilities: TTSCapabilities | None = None,
        replace: bool = False,
    ) -> None:
        canonical = self.normalize(name)
        if not canonical:
            raise ValueError("Provider name is required")
        if not callable(factory):
            raise TypeError("Provider factory must be callable")
        normalized_aliases = {self.normalize(alias) for alias in aliases}
        normalized_aliases.discard("")
        normalized_aliases.discard(canonical)
        with self._lock:
            occupied = canonical in self._factories or canonical in self._aliases
            if occupied and not replace:
                raise ValueError(f"TTS provider is already registered: {canonical}")
            if replace:
                self.unregister(canonical)
            self._factories[canonical] = factory
            if capabilities is not None:
                self._capabilities[canonical] = capabilities
            self._aliases[canonical] = canonical
            for alias in normalized_aliases:
                existing = self._aliases.get(alias)
                if existing is not None and existing != canonical and not replace:
                    raise ValueError(f"TTS provider alias is already registered: {alias}")
                self._aliases[alias] = canonical

    def unregister(self, name: str) -> bool:
        normalized = self.normalize(name)
        with self._lock:
            canonical = self._aliases.get(normalized, normalized)
            removed = self._factories.pop(canonical, None) is not None
            self._capabilities.pop(canonical, None)
            for alias, target in list(self._aliases.items()):
                if target == canonical:
                    self._aliases.pop(alias, None)
            return removed

    def canonical_name(self, name: str) -> str:
        normalized = self.normalize(name)
        with self._lock:
            canonical = self._aliases.get(normalized)
            if canonical is None or canonical not in self._factories:
                raise ValueError(f"Unknown TTS provider: {name}")
            return canonical

    def create(self, name: str, **kwargs) -> BaseTTSProvider:
        canonical = self.canonical_name(name)
        with self._lock:
            factory = self._factories[canonical]
        provider = factory(**kwargs)
        if not isinstance(provider, BaseTTSProvider):
            raise TypeError(
                f"Provider factory '{canonical}' must return BaseTTSProvider, "
                f"got {type(provider).__name__}"
            )
        return provider

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._factories)

    def aliases(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._aliases)

    def set_capabilities(self, name: str, capabilities: TTSCapabilities) -> None:
        canonical = self.canonical_name(name)
        if not isinstance(capabilities, TTSCapabilities):
            raise TypeError("capabilities must be TTSCapabilities")
        with self._lock:
            self._capabilities[canonical] = capabilities

    def capabilities(self, name: str) -> TTSCapabilities:
        canonical = self.canonical_name(name)
        with self._lock:
            return self._capabilities.get(canonical, TTSCapabilities())


provider_registry = ProviderRegistry()
