from schema import Receipt

test = Receipt(
    store_name="Albert Heijn",
    date="2025-04-29",
    invoice_number="155881-00001",
    payment_method="iDEAL",
    items=[
        {"name": "Boodschappen, zie specificatie", "total_price": 50.80, "vat_rate": "9%", "category": "groceries"},
        {"name": "Bezorgkosten", "total_price": 5.70, "vat_rate": "21%", "category": "delivery"},
    ],
    subtotal=54.30,
    vat_total=5.20,
    total=59.50
)

print(test.model_dump_json(indent=2))