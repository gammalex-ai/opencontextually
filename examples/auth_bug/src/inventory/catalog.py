"""Product catalog lookups."""

from __future__ import annotations


class Catalog:
    def __init__(self) -> None:
        self._items: dict[str, dict] = {}

    def add_item(self, sku: str, name: str, price_cents: int) -> None:
        self._items[sku] = {"name": name, "price_cents": price_cents}

    def get_item(self, sku: str) -> dict | None:
        return self._items.get(sku)
