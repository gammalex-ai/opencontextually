"""Outbound transactional email sending."""

from __future__ import annotations


def send_welcome_email(to_address: str, first_name: str) -> None:
    _send(to_address, subject="Welcome!", body=f"Hi {first_name}, thanks for joining.")


def _send(to_address: str, subject: str, body: str) -> None:
    # In production this would call out to an email provider. For this
    # fixture it is a no-op placeholder.
    pass
