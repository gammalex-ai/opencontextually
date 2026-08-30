from src.billing.invoice import LineItem, generate_invoice


def test_generate_invoice_totals_line_items():
    items = [LineItem("Widget", 2, 500), LineItem("Gadget", 1, 1500)]
    invoice = generate_invoice("cust-1234", items)

    assert invoice.total_cents() == 2500
