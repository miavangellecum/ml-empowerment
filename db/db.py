# db.py
import sqlite3

def init_db():
    conn = sqlite3.connect("receipts.db")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_name TEXT,
            date TEXT,
            payment_method TEXT,
            currency TEXT,
            subtotal REAL,
            tax REAL,
            total REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS line_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id INTEGER,
            name TEXT,
            quantity REAL,
            unit_price REAL,
            total_price REAL,
            category TEXT,
            FOREIGN KEY (receipt_id) REFERENCES receipts (id)
        )
    """)
    conn.commit()
    conn.close()


def save_receipt(receipt: dict) -> int:
    conn = sqlite3.connect("receipts.db")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO receipts (store_name, date, payment_method, currency, subtotal, tax, total)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        receipt["store_name"], receipt.get("date"), receipt.get("payment_method"),
        receipt.get("currency", "EUR"), receipt.get("subtotal"), receipt.get("tax"), receipt["total"]
    ))
    receipt_id = cur.lastrowid

    for item in receipt["items"]:
        cur.execute("""
            INSERT INTO line_items (receipt_id, name, quantity, unit_price, total_price, category)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            receipt_id, item["name"], item.get("quantity", 1),
            item.get("unit_price"), item["total_price"], item.get("category", "other")
        ))

    conn.commit()
    conn.close()
    return receipt_id

if __name__ == "__main__":
    init_db()