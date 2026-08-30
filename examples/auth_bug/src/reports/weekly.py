"""Weekly summary reports."""

from __future__ import annotations

from src.reports.monthly import summarize_revenue


def summarize_week(daily_totals_cents: list[int]) -> dict:
    return summarize_revenue(daily_totals_cents)
