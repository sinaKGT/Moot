"""One provider connection per provider preset, shared by every agent that uses it."""

from __future__ import annotations

from collections.abc import Callable

from moot.core import ModelRef
from moot.providers import Provider, create_provider

ProviderFactory = Callable[[str], Provider]


class ProviderPool:
    def __init__(self, factory: ProviderFactory | None = None) -> None:
        self._factory = factory or create_provider
        self._providers: dict[str, Provider] = {}

    def get(self, ref: ModelRef) -> Provider:
        """The provider for a model reference, created on first use."""
        provider = self._providers.get(ref.provider)
        if provider is None:
            provider = self._factory(ref.provider)
            self._providers[ref.provider] = provider
        return provider

    async def aclose(self) -> None:
        providers, self._providers = list(self._providers.values()), {}
        for provider in providers:
            await provider.aclose()
