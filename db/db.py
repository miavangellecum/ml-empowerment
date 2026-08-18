from db.cockroach import get_conn
from db.embeddings import EMBEDDING_DIM, embed_text, to_vector_literal
from backend.aws_clients import get_presigned_url

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
            CREATE UNIQUE INDEX IF NOT EXISTS receipts_dedupe_idx
            ON receipts (store_name, date, total)
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS line_items (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                receipt_id UUID NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
                name TEXT,
                quantity DECIMAL DEFAULT 1,
                unit_price DECIMAL,
                total_price DECIMAL,
                category TEXT DEFAULT 'other_expenses'
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

        cur.execute("""
            CREATE TABLE IF NOT EXISTS plaid_accounts
            (
                id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                account_id        TEXT UNIQUE NOT NULL,
                item_id           TEXT        NOT NULL REFERENCES plaid_items (item_id),
                name              TEXT,
                official_name     TEXT,
                mask              TEXT,
                type              TEXT,
                subtype           TEXT,
                current_balance   DECIMAL,
                available_balance DECIMAL,
                iso_currency_code TEXT,
                updated_at        TIMESTAMPTZ      DEFAULT now()
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

        # Remove duplicate CREATE TABLE for plaid_accounts (it's already defined above)
        # The second CREATE TABLE plaid_accounts in your file is a duplicate - remove it

        cur.execute("""
            CREATE TABLE IF NOT EXISTS receipt_transaction_matches (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                receipt_id UUID NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
                transaction_id UUID NOT NULL REFERENCES plaid_transactions(id) ON DELETE CASCADE,
                cosine_distance FLOAT,
                amount_diff_pct FLOAT,
                date_diff_days INT,
                confidence FLOAT,
                status TEXT NOT NULL DEFAULT 'pending',
                matched_by TEXT NOT NULL DEFAULT 'agent',
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now(),
                UNIQUE (receipt_id, transaction_id)
            )
        """)

        for stmt in (
            "CREATE VECTOR INDEX IF NOT EXISTS receipts_store_embedding_idx ON receipts (store_embedding)",
            "CREATE VECTOR INDEX IF NOT EXISTS plaid_tx_merchant_embedding_idx ON plaid_transactions (merchant_embedding)",
        ):
            try:
                cur.execute(stmt)
            except Exception as e:
                print(f"[schema] skipping vector index ({e})")

        cur.close()

def derive_receipt_category(items: list[dict] | None) -> str:
    """Return one category for the receipt from its line items."""
    categories = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        category = item.get("category")
        if category:
            categories.append(str(category).strip())

    unique = {category.lower() for category in categories if category}
    if not unique:
        return "other_expenses"
    if len(unique) == 1:
        return next(iter(unique))
    return "other_expenses"

def check_duplicate_receipt(store_name: str | None, date, total) -> str | None:
    """Returns the existing receipt's id if a receipt with the same
    store_name + date + total already exists, else None."""
    if not store_name or not date or total is None:
        return None

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id FROM receipts
            WHERE store_name = %s AND date = %s AND total = %s
            LIMIT 1
            """,
            (store_name, date, total),
        )
        row = cur.fetchone()
        cur.close()

    return str(row[0]) if row else None

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
                    item.get("category", "other_expenses"),
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

        for r in rows:
            r["id"] = str(r["id"])
            r["s3_url"] = _presign_url(r["s3_url"])
            cur2 = conn.cursor()
            cur2.execute(
                "SELECT id, name, quantity, unit_price, total_price, category "
                "FROM line_items WHERE receipt_id = %s",
                (r["id"],),
            )
            item_cols = [d.name for d in cur2.description]
            items = [dict(zip(item_cols, row)) for row in cur2.fetchall()]
            for item in items:
                item["id"] = str(item["id"])
            r["items"] = items
            r["category"] = derive_receipt_category(items)
            cur2.close()
        cur.close()

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
        receipt["s3_url"] = _presign_url(receipt["s3_url"])

        cur.execute(
            "SELECT id, name, quantity, unit_price, total_price, category "
            "FROM line_items WHERE receipt_id = %s",
            (receipt_id,),
        )
        item_cols = [d.name for d in cur.description]
        receipt["items"] = [dict(zip(item_cols, r)) for r in cur.fetchall()]
        for item in receipt["items"]:
            item["id"] = str(item["id"])
        receipt["category"] = derive_receipt_category(receipt["items"])
        cur.close()

    return receipt

def _presign_url(s3_url: str | None) -> str | None:
    """Converts a stored s3://bucket/key URL into a short-lived HTTPS
    URL the frontend can actually load."""
    if not s3_url:
        return None
    if not s3_url.startswith("s3://"):
        return s3_url  # already a usable URL
    
    # Extract the key from s3://bucket-name/path/to/file
    # s3://my-bucket/receipts/image.jpg -> receipts/image.jpg
    parts = s3_url.split("/")
    if len(parts) < 4:
        return s3_url
    key = "/".join(parts[3:])  # Skip "s3:", "", "bucket-name"
    
    try:
        return get_presigned_url(key)
    except Exception as e:
        print(f"Failed to generate presigned URL for {key}: {e}")
        return s3_url  # Fall back to the original URL