"""
CockroachDB-backed replacement for the old db/db.py (sqlite3).
Same public functions (save_receipt, get_receipts) plus get_receipt, so
app.py's call sites barely change — just the import.
"""
from db.cockroach import get_conn
from db.embeddings import EMBEDDING_DIM, embed_text, to_vector_literal


def save_receipt(receipt: dict, s3_url: str | None = None) -> str:
    """Inserts the receipt + its line items, embedding store_name for
    later vector matching against Plaid transactions. Returns the new
    receipt's UUID as a string."""
    vec_literal = to_vector_literal(embed_text(receipt.get("store_name")))

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            INSERT INTO receipts
                (store_name, store_embedding, date, payment_method, currency,
                 subtotal, tax, total, s3_url)
            VALUES (%s, %s::VECTOR({EMBEDDING_DIM}), %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                receipt["store_name"], vec_literal, receipt.get("date"),
                receipt.get("payment_method"), receipt.get("currency", "EUR"),
                receipt.get("subtotal"), receipt.get("tax"), receipt["total"], s3_url,
            ),
        )
        receipt_id = cur.fetchone()[0]

        for item in receipt.get("items", []):
            cur.execute(
                """
                INSERT INTO line_items
                    (receipt_id, name, quantity, unit_price, total_price, category)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    receipt_id, item["name"], item.get("quantity", 1),
                    item.get("unit_price"), item["total_price"],
                    item.get("category", "other"),
                ),
            )
        cur.close()

    return str(receipt_id)


def get_receipts() -> list[dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, store_name, date, payment_method, currency,
                   subtotal, tax, total, s3_url, created_at
            FROM receipts
            ORDER BY created_at DESC
            """
        )
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()

    for r in rows:
        r["id"] = str(r["id"])
    return rows


def get_receipt(receipt_id: str) -> dict | None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, store_name, date, payment_method, currency,
                   subtotal, tax, total, s3_url, created_at
            FROM receipts WHERE id = %s
            """,
            (receipt_id,),
        )
        row = cur.fetchone()
        if not row:
            cur.close()
            return None
        cols = [d.name for d in cur.description]
        receipt = dict(zip(cols, row))
        receipt["id"] = str(receipt["id"])

        cur.execute(
            "SELECT id, name, quantity, unit_price, total_price, category "
            "FROM line_items WHERE receipt_id = %s",
            (receipt_id,),
        )
        item_cols = [d.name for d in cur.description]
        receipt["items"] = [dict(zip(item_cols, r)) for r in cur.fetchall()]
        for item in receipt["items"]:
            item["id"] = str(item["id"])
        cur.close()

    return receipt