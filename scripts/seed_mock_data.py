"""
Seeds realistic-ish mock receipts + Plaid transactions and runs the
matcher over them, so you can see /matches and /reports/* populated
without depending on live OCR/Bedrock/Plaid-sandbox timing lining up.

Run from the project root (so the `db`/`backend` packages resolve):
    python scripts/seed_mock_data.py

Idempotent: unlike plaid_transactions (deduped via transaction_id +
ON CONFLICT), receipts have no natural key, so a second run of this script
used to insert duplicate rows every time (4x Staples, etc.). Fixed by
deleting any previous rows for these exact mock store names before
reinserting — safe here because the store name list below is fixed and
scoped to this script, not something a real /extract upload would ever
collide with.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.cockroach import get_conn
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
    {"transaction_id": "mock-tx-1", "account_id": "acct-1", "name": "STAPLES 00012938 BOSTON MA",
     "merchant_name": "Staples", "amount": 91.26, "iso_currency_code": "USD",
     "category": ["Shops", "Office Supplies"], "date": "2026-07-03", "pending": False},
    {"transaction_id": "mock-tx-2", "account_id": "acct-1", "name": "DELTA AIR 0067192834571",
     "merchant_name": "Delta Air Lines", "amount": 412.00, "iso_currency_code": "USD",
     "category": ["Travel", "Airlines"], "date": "2026-07-05", "pending": False},
    {"transaction_id": "mock-tx-3", "account_id": "acct-1", "name": "SQ *THE GRILL HOUSE",
     "merchant_name": "The Grill House", "amount": 78.66, "iso_currency_code": "USD",
     "category": ["Food and Drink", "Restaurants"], "date": "2026-07-07", "pending": False},
    {"transaction_id": "mock-tx-4", "account_id": "acct-1", "name": "VERIZON WIRELESS PAYMENTS",
     "merchant_name": "Verizon", "amount": 145.00, "iso_currency_code": "USD",
     "category": ["Service", "Telecommunication Services"], "date": "2026-07-10", "pending": False},
    {"transaction_id": "mock-tx-5", "account_id": "acct-1", "name": "AWS  AMZN.COM/BILL WA",
     "merchant_name": "Amazon Web Services", "amount": 63.40, "iso_currency_code": "USD",
     "category": ["Service", "Cloud Computing"], "date": "2026-07-08", "pending": False},
    {"transaction_id": "mock-tx-6", "account_id": "acct-1", "name": "ADOBE  CREATIVE CLOUD",
     "merchant_name": "Adobe", "amount": 54.99, "iso_currency_code": "USD",
     "category": ["Service", "Subscription"], "date": "2026-07-09", "pending": False},
]


def _reset_previous_mock_receipts():
    """Deletes any receipts left over from a prior run of this exact
    script, identified by store name. line_items and
    receipt_transaction_matches cascade-delete automatically."""
    store_names = [r["store_name"] for r in MOCK_RECEIPTS]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM receipts WHERE store_name = ANY(%s)", (store_names,))
        deleted = cur.rowcount
        cur.close()
    if deleted:
        print(f"  removed {deleted} receipt(s) from a previous seed run")


def main():
    print("Initializing schema...")
    init_db()

    print("Clearing any previously-seeded mock receipts...")
    _reset_previous_mock_receipts()

    print(f"Ensuring mock Plaid item '{MOCK_ITEM_ID}' exists...")
    save_item(MOCK_ITEM_ID, access_token="mock-access-token", institution_name="Mock Bank")

    print(f"Inserting {len(MOCK_RECEIPTS)} mock receipts...")
    receipt_ids = []
    for r in MOCK_RECEIPTS:
        receipt_id = save_receipt(r)
        receipt_ids.append(receipt_id)
        print(f"  saved receipt: {r['store_name']} (${r['total']}) -> {receipt_id}")

    print(f"Upserting {len(MOCK_TRANSACTIONS)} mock transactions (already idempotent via transaction_id)...")
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