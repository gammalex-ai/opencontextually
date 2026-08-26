"""Generic string helpers used across the project."""

from __future__ import annotations


def slugify(value: str) -> str:
    return "-".join(value.lower().split())


def truncate(value: str, max_len: int) -> str:
    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "…"
