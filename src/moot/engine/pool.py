"""One provider connection per provider preset, shared by every agent that uses it.

Every provider is wrapped by `budgeted`, so each request fits its model's context window."""

from __future__ import annotations

from collections.abc import Callable

from moot.core import ModelRef
from moot.providers import BudgetBook, BudgetedProvider, Provider, budgeted, create_provider

ProviderFactory = Callable[[str], Provider]


class ProviderPool:
    def __init__(self, factory: ProviderFactory | None = None, *, book: BudgetBook | None = None):
        self._factory = factory or create_provider
        self._book = book
        self._providers: dict[str, BudgetedProvider] = {}

    def get(self, ref: ModelRef) -> BudgetedProvider:
        """The provider for a model reference, created on first use."""
        provider = self._providers.get(ref.provider)
        if provider is None:
            provider = budgeted(self._factory(ref.provider), book=self._book)
            self._providers[ref.provider] = provider
        return provider

    async def aclose(self) -> None:
        providers, self._providers = list(self._providers.values()), {}
        for provider in providers:
            await provider.aclose()
