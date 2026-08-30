"""Generic date helpers used across the project."""

from __future__ import annotations

import datetime


def is_weekend(date: datetime.date) -> bool:
    return date.weekday() >= 5


def days_between(start: datetime.date, end: datetime.date) -> int:
    return (end - start).days
