"""
CockroachDB-backed replacement for the old db/db.py (sqlite3).
Same public functions (save_receipt, get_receipts) plus get_receipt, so
app.py's call sites barely change — just the import.
"""
from db.cockroach import get_conn
from db.embeddings import EMBEDDING_DIM, embed_text, to_vector_literal

def init_db():
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS receipts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                store_name TEXT,
                store_embedding VECTOR({EMBEDDING_DIM}),
                date DATE,
                payment_method TEXT,
                currency TEXT DEFAULT 'EUR',
                subtotal DECIMAL,
                tax DECIMAL,
                total DECIMAL NOT NULL,
                s3_url TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS line_items (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                receipt_id UUID NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
                name TEXT,
                quantity DECIMAL DEFAULT 1,
                unit_price DECIMAL,
                total_price DECIMAL,
                category TEXT DEFAULT 'other'
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS plaid_items (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                item_id TEXT UNIQUE NOT NULL,
                access_token TEXT NOT NULL,
                institution_name TEXT,
                cursor TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS plaid_transactions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                transaction_id TEXT UNIQUE NOT NULL,
                item_id TEXT NOT NULL REFERENCES plaid_items(item_id),
                account_id TEXT,
                name TEXT,
                merchant_name TEXT,
                merchant_embedding VECTOR({EMBEDDING_DIM}),
                amount DECIMAL,
                iso_currency_code TEXT,
                category TEXT,
                date DATE,
                pending BOOL DEFAULT false,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)

        # The join table: one row per candidate (receipt, transaction) pair
        # the agent has considered. status starts 'pending' unless the
        # confidence score clears AUTO_CONFIRM_THRESHOLD in matcher.py.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS receipt_transaction_matches (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                receipt_id UUID NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
                transaction_id UUID NOT NULL REFERENCES plaid_transactions(id) ON DELETE CASCADE,
                cosine_distance FLOAT,
                amount_diff_pct FLOAT,
                date_diff_days INT,
                confidence FLOAT,
                status TEXT NOT NULL DEFAULT 'pending', -- pending | confirmed | rejected
                matched_by TEXT NOT NULL DEFAULT 'agent', -- agent | user
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now(),
                UNIQUE (receipt_id, transaction_id)
            )
        """)

        # Vector indexes power the nearest-neighbor lookups in matcher.py.
        # Requires CockroachDB v25.2+ with the vector index feature enabled
        # (SET CLUSTER SETTING feature.vector_index.enabled = true;).
        for stmt in (
            "CREATE VECTOR INDEX IF NOT EXISTS receipts_store_embedding_idx "
            "ON receipts (store_embedding)",
            "CREATE VECTOR INDEX IF NOT EXISTS plaid_tx_merchant_embedding_idx "
            "ON plaid_transactions (merchant_embedding)",
        ):
            try:
                cur.execute(stmt)
            except Exception as e:
                # Non-fatal: matcher.py still works (just does a full scan)
                # without the index, e.g. on clusters where the feature
                # flag above hasn't been set yet.
                print(f"[schema] skipping vector index ({e})")

        cur.close()

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
                   subtotal, tax, total, s3_url, created_at, category
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