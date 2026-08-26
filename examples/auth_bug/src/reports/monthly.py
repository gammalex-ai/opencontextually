"""Monthly summary reports."""

from __future__ import annotations


def summarize_revenue(daily_totals_cents: list[int]) -> dict:
    total = sum(daily_totals_cents)
    average = total / len(daily_totals_cents) if daily_totals_cents else 0
    return {"total_cents": total, "average_daily_cents": average}


def top_days(daily_totals_cents: list[int], n: int = 3) -> list[int]:
    return sorted(daily_totals_cents, reverse=True)[:n]
