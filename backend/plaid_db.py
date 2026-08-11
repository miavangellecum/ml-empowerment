"""
CockroachDB-backed replacement for backend/plaid_db.py (sqlite3).
Function names/signatures match the original so plaid_routes.py's call
sites barely change. upsert_transactions additionally embeds each
transaction's merchant name/name and returns the list of newly-inserted
transaction UUIDs so plaid_routes.py can hand them to db/matcher.py.
"""
from db.cockroach import get_conn
from db.embeddings import EMBEDDING_DIM, embed_text, to_vector_literal


def save_item(item_id: str, access_token: str, institution_name: str | None = None):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO plaid_items (item_id, access_token, institution_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (item_id) DO UPDATE SET
                access_token = excluded.access_token,
                institution_name = excluded.institution_name
            """,
            (item_id, access_token, institution_name),
        )
        cur.close()


def get_all_items() -> list[dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM plaid_items")
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
    return rows


def get_item(item_id: str) -> dict | None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM plaid_items WHERE item_id = %s", (item_id,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None
        cols = [d.name for d in cur.description]
        cur.close()
    return dict(zip(cols, row))


def update_cursor(item_id: str, cursor: str):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE plaid_items SET cursor = %s WHERE item_id = %s", (cursor, item_id)
        )
        cur.close()


def upsert_transactions(item_id: str, transactions: list[dict]) -> list[str]:
    """Inserts/updates transactions, embedding merchant_name (falling back
    to name) for vector matching. Returns the UUIDs of rows that were newly
    inserted (as opposed to updated), so callers can run the matcher only
    against genuinely new transactions."""
    new_ids: list[str] = []

    with get_conn() as conn:
        cur = conn.cursor()
        for t in transactions:
            category = t.get("category")
            if isinstance(category, list):
                category = ", ".join(category)

            embed_source = t.get("merchant_name") or t.get("name")
            vec_literal = to_vector_literal(embed_text(embed_source))

            cur.execute(
                f"""
                INSERT INTO plaid_transactions
                    (transaction_id, item_id, account_id, name, merchant_name,
                     merchant_embedding, amount, iso_currency_code, category,
                     date, pending)
                VALUES (%s, %s, %s, %s, %s, %s::VECTOR({EMBEDDING_DIM}), %s, %s, %s, %s, %s)
                ON CONFLICT (transaction_id) DO UPDATE SET
                    name = excluded.name,
                    merchant_name = excluded.merchant_name,
                    merchant_embedding = excluded.merchant_embedding,
                    amount = excluded.amount,
                    iso_currency_code = excluded.iso_currency_code,
                    category = excluded.category,
                    date = excluded.date,
                    pending = excluded.pending
                RETURNING id, (xmax = 0) AS inserted
                """,
                (
                    t["transaction_id"], item_id, t.get("account_id"), t.get("name"),
                    t.get("merchant_name"), vec_literal, t.get("amount"),
                    t.get("iso_currency_code"), category, t.get("date"),
                    bool(t.get("pending", False)),
                ),
            )
            row_id, inserted = cur.fetchone()
            if inserted:
                new_ids.append(str(row_id))
        cur.close()

    return new_ids


def remove_transactions(transaction_ids: list[str]):
    if not transaction_ids:
        return
    with get_conn() as conn:
        cur = conn.cursor()
        cur.executemany(
            "DELETE FROM plaid_transactions WHERE transaction_id = %s",
            [(tid,) for tid in transaction_ids],
        )
        cur.close()


def get_transactions(item_id: str | None = None) -> list[dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        if item_id:
            cur.execute(
                "SELECT * FROM plaid_transactions WHERE item_id = %s ORDER BY date DESC",
                (item_id,),
            )
        else:
            cur.execute("SELECT * FROM plaid_transactions ORDER BY date DESC")
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()

    for r in rows:
        r["id"] = str(r["id"])
        r.pop("merchant_embedding", None)  # not JSON-serializable-friendly, and irrelevant to the UI
    return rows