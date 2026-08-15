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


def upsert_accounts(item_id: str, accounts: list[dict]) -> list[str]:
    """Insert/update Plaid account balances and metadata for an item."""
    if not accounts:
        return []

    with get_conn() as conn:
        cur = conn.cursor()
        saved_ids: list[str] = []
        for account in accounts:
            account_id = account.get("account_id")
            if not account_id:
                continue

            balances = account.get("balances") or {}
            cur.execute(
                """
                INSERT INTO plaid_accounts
                    (item_id, account_id, name, official_name, mask, type, subtype,
                     available_balance, current_balance, iso_currency_code,
                     unofficial_currency_code)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (item_id, account_id) DO UPDATE SET
                    name = excluded.name,
                    official_name = excluded.official_name,
                    mask = excluded.mask,
                    type = excluded.type,
                    subtype = excluded.subtype,
                    available_balance = excluded.available_balance,
                    current_balance = excluded.current_balance,
                    iso_currency_code = excluded.iso_currency_code,
                    unofficial_currency_code = excluded.unofficial_currency_code,
                    updated_at = now()
                RETURNING id
                """,
                (
                    item_id,
                    account_id,
                    account.get("name"),
                    account.get("official_name"),
                    account.get("mask"),
                    account.get("type"),
                    account.get("subtype"),
                    balances.get("available"),
                    balances.get("current"),
                    account.get("iso_currency_code") or balances.get("iso_currency_code"),
                    account.get("unofficial_currency_code"),
                ),
            )
            saved_ids.append(str(cur.fetchone()[0]))
        cur.close()

    return saved_ids


def get_accounts(item_id: str | None = None) -> list[dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        if item_id:
            cur.execute(
                "SELECT * FROM plaid_accounts WHERE item_id = %s ORDER BY name ASC",
                (item_id,),
            )
        else:
            cur.execute("SELECT * FROM plaid_accounts ORDER BY item_id, name ASC")
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()

    for r in rows:
        r["id"] = str(r["id"])
    return rows


def upsert_transactions(item_id: str, transactions: list[dict]) -> list[str]:
    """Inserts/updates transactions, embedding merchant_name (falling back
    to name) for vector matching. Returns the UUIDs of rows that were newly
    inserted (as opposed to updated).

    Insert-vs-update is determined by checking which transaction_ids already
    exist *before* the upsert runs, rather than the common Postgres
    `(xmax = 0)` trick — CockroachDB doesn't use Postgres's physical MVCC
    storage internally, so it has no xmin/xmax system columns to query.
    """
    if not transactions:
        return []

    new_ids: list[str] = []
    incoming_tx_ids = [t["transaction_id"] for t in transactions]

    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            "SELECT transaction_id FROM plaid_transactions WHERE transaction_id = ANY(%s)",
            (incoming_tx_ids,),
        )
        existing_tx_ids = {row[0] for row in cur.fetchall()}

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
                RETURNING id
                """,
                (
                    t["transaction_id"], item_id, t.get("account_id"), t.get("name"),
                    t.get("merchant_name"), vec_literal, t.get("amount"),
                    t.get("iso_currency_code"), category, t.get("date"),
                    bool(t.get("pending", False)),
                ),
            )
            row_id = cur.fetchone()[0]
            if t["transaction_id"] not in existing_tx_ids:
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