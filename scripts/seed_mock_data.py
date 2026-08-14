"""
Seeds realistic-ish mock receipts + Plaid transactions and runs the
matcher over them, so you can see /matches and /reports/* populated
without depending on live OCR/Bedrock/Plaid-sandbox timing lining up.

Run from the project root (so the `db`/`backend` packages resolve):
    python scripts/seed_mock_data.py

Safe to re-run — uses ON CONFLICT upserts, so it won't duplicate rows if
you run it twice with the same MOCK_ITEM_ID.

Design: 5 receipts, 6 Plaid transactions. 4 of the receipts have a
deliberately close counterpart transaction (same rough amount, merchant
name close enough for the embedding to catch it, date within a few days)
so the matcher auto-confirms or at least surfaces them as pending. One
receipt and two transactions are left with no counterpart, so /reports
has something to flag under audit_flags.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.db import init_db, save_receipt
from backend.plaid_db import save_item, upsert_transactions
from db.matcher import match_receipt, match_transaction

MOCK_ITEM_ID = "mock-item-001"

MOCK_RECEIPTS = [
    {
        "store_name": "Staples",
        "date": "2026-07-02",
        "payment_method": "card",
        "currency": "USD",
        "subtotal": 84.50,
        "tax": 6.76,
        "total": 91.26,
        "items": [
            {"name": "Printer paper (5 reams)", "quantity": 5, "unit_price": 8.50, "total_price": 42.50, "category": "office_expense"},
            {"name": "Ink cartridges", "quantity": 2, "unit_price": 21.00, "total_price": 42.00, "category": "office_expense"},
        ],
    },
    {
        "store_name": "Delta Airlines",
        "date": "2026-07-05",
        "payment_method": "card",
        "currency": "USD",
        "subtotal": 412.00,
        "tax": 0,
        "total": 412.00,
        "items": [
            {"name": "Round-trip flight, client meeting", "quantity": 1, "unit_price": 412.00, "total_price": 412.00, "category": "travel"},
        ],
    },
    {
        "store_name": "The Grill House",
        "date": "2026-07-06",
        "payment_method": "card",
        "currency": "USD",
        "subtotal": 68.20,
        "tax": 5.46,
        "total": 78.66,  # includes tip
        "items": [
            {"name": "Client dinner", "quantity": 1, "unit_price": 68.20, "total_price": 68.20, "category": "meals"},
        ],
    },
    {
        "store_name": "Verizon Wireless",
        "date": "2026-07-10",
        "payment_method": "bank transfer",
        "currency": "USD",
        "subtotal": 145.00,
        "tax": 0,
        "total": 145.00,
        "items": [
            {"name": "Business line, monthly", "quantity": 1, "unit_price": 145.00, "total_price": 145.00, "category": "utilities"},
        ],
    },
    {
        # deliberately unmatched: no corresponding transaction below
        "store_name": "Whole Foods Market",
        "date": "2026-07-11",
        "payment_method": "cash",
        "currency": "USD",
        "subtotal": 52.10,
        "tax": 0,
        "total": 52.10,
        "items": [
            {"name": "Groceries", "quantity": 1, "unit_price": 52.10, "total_price": 52.10, "category": "personal_non_deductible"},
        ],
    },
]

MOCK_TRANSACTIONS = [
    # matches receipt[0] Staples
    {"transaction_id": "mock-tx-1", "account_id": "acct-1", "name": "STAPLES 00012938 BOSTON MA",
     "merchant_name": "Staples", "amount": 91.26, "iso_currency_code": "USD",
     "category": ["Shops", "Office Supplies"], "date": "2026-07-03", "pending": False},
    # matches receipt[1] Delta
    {"transaction_id": "mock-tx-2", "account_id": "acct-1", "name": "DELTA AIR 0067192834571",
     "merchant_name": "Delta Air Lines", "amount": 412.00, "iso_currency_code": "USD",
     "category": ["Travel", "Airlines"], "date": "2026-07-05", "pending": False},
    # matches receipt[2] The Grill House
    {"transaction_id": "mock-tx-3", "account_id": "acct-1", "name": "SQ *THE GRILL HOUSE",
     "merchant_name": "The Grill House", "amount": 78.66, "iso_currency_code": "USD",
     "category": ["Food and Drink", "Restaurants"], "date": "2026-07-07", "pending": False},
    # matches receipt[3] Verizon
    {"transaction_id": "mock-tx-4", "account_id": "acct-1", "name": "VERIZON WIRELESS PAYMENTS",
     "merchant_name": "Verizon", "amount": 145.00, "iso_currency_code": "USD",
     "category": ["Service", "Telecommunication Services"], "date": "2026-07-10", "pending": False},
    # deliberately unmatched: no receipt uploaded for these
    {"transaction_id": "mock-tx-5", "account_id": "acct-1", "name": "AWS  AMZN.COM/BILL WA",
     "merchant_name": "Amazon Web Services", "amount": 63.40, "iso_currency_code": "USD",
     "category": ["Service", "Cloud Computing"], "date": "2026-07-08", "pending": False},
    {"transaction_id": "mock-tx-6", "account_id": "acct-1", "name": "ADOBE  CREATIVE CLOUD",
     "merchant_name": "Adobe", "amount": 54.99, "iso_currency_code": "USD",
     "category": ["Service", "Subscription"], "date": "2026-07-09", "pending": False},
]


def main():
    print("Initializing schema...")
    init_db()

    print(f"Ensuring mock Plaid item '{MOCK_ITEM_ID}' exists...")
    save_item(MOCK_ITEM_ID, access_token="mock-access-token", institution_name="Mock Bank")

    print(f"Inserting {len(MOCK_RECEIPTS)} mock receipts...")
    receipt_ids = []
    for r in MOCK_RECEIPTS:
        receipt_id = save_receipt(r)
        receipt_ids.append(receipt_id)
        print(f"  saved receipt: {r['store_name']} (${r['total']}) -> {receipt_id}")

    print(f"Inserting {len(MOCK_TRANSACTIONS)} mock transactions...")
    new_tx_ids = upsert_transactions(MOCK_ITEM_ID, MOCK_TRANSACTIONS)
    print(f"  {len(new_tx_ids)} newly inserted")

    print("Running matcher over new receipts...")
    for receipt_id, r in zip(receipt_ids, MOCK_RECEIPTS):
        results = match_receipt(receipt_id)
        if results:
            for m in results:
                print(f"  {r['store_name']} -> {m['label']} (confidence={m['confidence']}, status={m['status']})")
        else:
            print(f"  {r['store_name']} -> no candidates found")

    print("Running matcher over new transactions (covers any receipt->tx pairs missed above)...")
    for tx_id in new_tx_ids:
        match_transaction(tx_id)

    print("\nDone. Try:")
    print("  GET /matches")
    print("  GET /reports/ledger")
    print("  GET /reports/summary")


if __name__ == "__main__":
    main()
