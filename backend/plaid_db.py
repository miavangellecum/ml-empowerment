import sqlite3

DB_PATH = "db/receipts.db"


def init_plaid_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS plaid_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT UNIQUE,
            access_token TEXT,
            institution_name TEXT,
            cursor TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS plaid_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id TEXT UNIQUE,
            item_id TEXT,
            account_id TEXT,
            name TEXT,
            merchant_name TEXT,
            amount REAL,
            iso_currency_code TEXT,
            category TEXT,
            date TEXT,
            pending INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES plaid_items (item_id)
        )
    """)
    conn.commit()
    conn.close()


def save_item(item_id: str, access_token: str, institution_name: str | None = None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO plaid_items (item_id, access_token, institution_name)
        VALUES (?, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET
            access_token=excluded.access_token,
            institution_name=excluded.institution_name
    """, (item_id, access_token, institution_name))
    conn.commit()
    conn.close()


def get_all_items():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM plaid_items").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_item(item_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    row = cur.execute("SELECT * FROM plaid_items WHERE item_id = ?", (item_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_cursor(item_id: str, cursor: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE plaid_items SET cursor = ? WHERE item_id = ?", (cursor, item_id))
    conn.commit()
    conn.close()


def upsert_transactions(item_id: str, transactions: list[dict]):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for t in transactions:
        category = t.get("category")
        # Plaid returns category as a list (e.g. ["Food and Drink", "Restaurants"]);
        # flatten it to a string since SQLite can't bind lists directly.
        if isinstance(category, list):
            category = ", ".join(category)

        cur.execute("""
            INSERT INTO plaid_transactions
                (transaction_id, item_id, account_id, name, merchant_name,
                 amount, iso_currency_code, category, date, pending)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(transaction_id) DO UPDATE SET
                name=excluded.name,
                merchant_name=excluded.merchant_name,
                amount=excluded.amount,
                iso_currency_code=excluded.iso_currency_code,
                category=excluded.category,
                date=excluded.date,
                pending=excluded.pending
        """, (
            t["transaction_id"], item_id, t.get("account_id"), t.get("name"),
            t.get("merchant_name"), t.get("amount"), t.get("iso_currency_code"),
            category, t.get("date"), int(t.get("pending", False)),
        ))
    conn.commit()
    conn.close()


def remove_transactions(transaction_ids: list[str]):
    if not transaction_ids:
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executemany("DELETE FROM plaid_transactions WHERE transaction_id = ?",
                     [(tid,) for tid in transaction_ids])
    conn.commit()
    conn.close()


def get_transactions(item_id: str | None = None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if item_id:
        rows = cur.execute(
            "SELECT * FROM plaid_transactions WHERE item_id = ? ORDER BY date DESC",
            (item_id,),
        ).fetchall()
    else:
        rows = cur.execute("SELECT * FROM plaid_transactions ORDER BY date DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]