"""
Combined schema: receipts, Plaid transactions, and the table that links
them. This is the "combine the two tables" piece — rather than merging
receipts and plaid_transactions into one table (they have different shapes
and different sources of truth), we keep both and add a
receipt_transaction_matches join table that the agent in db/matcher.py
populates. That keeps every match auditable and reversible instead of
overwriting either source record.

Call init_db() once at app startup (see app.py).
"""
from db.cockroach import get_conn
from db.embeddings import EMBEDDING_DIM


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