from db.cockroach import get_conn


def set_plaid_transaction_category(transaction_row_id: str, category: str) -> None:
    """Update the category for a plaid_transactions row by its UUID string."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE plaid_transactions SET category = %s WHERE id = %s",
            (category, transaction_row_id),
        )
        cur.close()
        conn.commit()