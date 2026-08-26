"""Invoice generation for billing."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LineItem:
    description: str
    quantity: int
    unit_price_cents: int


@dataclass
class Invoice:
    invoice_id: str
    customer_id: str
    line_items: list[LineItem] = field(default_factory=list)

    def total_cents(self) -> int:
        return sum(item.quantity * item.unit_price_cents for item in self.line_items)


def generate_invoice(customer_id: str, line_items: list[LineItem]) -> Invoice:
    invoice_id = f"INV-{customer_id[:4].upper()}-{len(line_items)}"
    return Invoice(invoice_id=invoice_id, customer_id=customer_id, line_items=line_items)
