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
        CREATE TABLE IF NOT EXISTS plaid_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT UNIQUE,
            item_id TEXT,
            name TEXT,
            official_name TEXT,
            mask TEXT,
            type TEXT,
            subtype TEXT,
            current_balance REAL,
            available_balance REAL,
            iso_currency_code TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES plaid_items (item_id)
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


def upsert_accounts(item_id: str, accounts: list[dict]):
    """Stores/refreshes the latest balance snapshot for every account on an item."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for a in accounts:
        balances = a.get("balances") or {}
        cur.execute("""
            INSERT INTO plaid_accounts
                (account_id, item_id, name, official_name, mask, type, subtype,
                 current_balance, available_balance, iso_currency_code, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(account_id) DO UPDATE SET
                name=excluded.name,
                official_name=excluded.official_name,
                mask=excluded.mask,
                type=excluded.type,
                subtype=excluded.subtype,
                current_balance=excluded.current_balance,
                available_balance=excluded.available_balance,
                iso_currency_code=excluded.iso_currency_code,
                updated_at=CURRENT_TIMESTAMP
        """, (
            a.get("account_id"), item_id, a.get("name"), a.get("official_name"),
            a.get("mask"), a.get("type"), a.get("subtype"),
            balances.get("current"), balances.get("available"),
            balances.get("iso_currency_code"),
        ))
    conn.commit()
    conn.close()


def get_accounts(item_id: str | None = None):
    """Accounts joined with their parent bank's institution_name, for display."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if item_id:
        rows = cur.execute("""
            SELECT pa.*, pi.institution_name FROM plaid_accounts pa
            JOIN plaid_items pi ON pi.item_id = pa.item_id
            WHERE pa.item_id = ?
        """, (item_id,)).fetchall()
    else:
        rows = cur.execute("""
            SELECT pa.*, pi.institution_name FROM plaid_accounts pa
            JOIN plaid_items pi ON pi.item_id = pa.item_id
        """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_item(item_id: str):
    """Unlinks a bank locally: drops the item plus its accounts and transactions."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM plaid_transactions WHERE item_id = ?", (item_id,))
    cur.execute("DELETE FROM plaid_accounts WHERE item_id = ?", (item_id,))
    cur.execute("DELETE FROM plaid_items WHERE item_id = ?", (item_id,))
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