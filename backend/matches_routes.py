"""
Endpoints for reviewing what the matching agent found, and a tax-season
overview: how much is documented, how much is still uncategorized, and
which bank transactions still have no receipt behind them.
"""
from fastapi import APIRouter, HTTPException

from db.cockroach import get_conn

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("")
async def list_matches(status: str | None = None):
    """status: 'pending' | 'confirmed' | 'rejected' | omit for all."""
    with get_conn() as conn:
        cur = conn.cursor()
        query = """
            SELECT m.id, m.status, m.confidence, m.matched_by, m.created_at,
                   r.id AS receipt_id, r.store_name, r.total AS receipt_total,
                   r.date AS receipt_date, r.s3_url AS receipt_s3_url,
                   t.id AS transaction_id, t.name, t.merchant_name, t.amount, t.date AS tx_date
            FROM receipt_transaction_matches m
            JOIN receipts r ON r.id = m.receipt_id
            JOIN plaid_transactions t ON t.id = m.transaction_id
        """
        params: tuple = ()
        if status:
            query += " WHERE m.status = %s"
            params = (status,)
        query += " ORDER BY m.created_at DESC"

        cur.execute(query, params)
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()

    for r in rows:
        r["id"] = str(r["id"])
        r["receipt_id"] = str(r["receipt_id"])
        r["transaction_id"] = str(r["transaction_id"])
    return rows


@router.post("/{match_id}/confirm")
async def confirm_match(match_id: str):
    return _set_status(match_id, "confirmed")


@router.post("/{match_id}/reject")
async def reject_match(match_id: str):
    return _set_status(match_id, "rejected")


def _set_status(match_id: str, status: str):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE receipt_transaction_matches "
            "SET status = %s, matched_by = 'user', updated_at = now() "
            "WHERE id = %s RETURNING id",
            (status, match_id),
        )
        row = cur.fetchone()
        cur.close()

    if not row:
        raise HTTPException(status_code=404, detail="Match not found")
    return {"match_id": match_id, "status": status}


@router.get("/overview")
async def overview():
    """Quick tax-recordkeeping snapshot: what's on file, what's still
    unmatched on the bank side, and how much spend has no category yet."""
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*), COALESCE(SUM(total), 0) FROM receipts")
        receipt_count, receipt_total = cur.fetchone()

        cur.execute(
            """
            SELECT COUNT(*) FROM plaid_transactions t
            WHERE NOT EXISTS (
                SELECT 1 FROM receipt_transaction_matches m
                WHERE m.transaction_id = t.id AND m.status = 'confirmed'
            )
            """
        )
        unmatched_transactions = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM line_items WHERE category IS NULL OR category = 'other_expenses'"
        )
        uncategorized_count = cur.fetchone()[0]

        cur.execute(
            "SELECT COALESCE(SUM(total_price), 0) FROM line_items "
            "WHERE category IS NULL OR category = 'other_expenses'"
        )
        uncategorized_amount = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM receipt_transaction_matches WHERE status = 'pending'"
        )
        pending_review = cur.fetchone()[0]

        cur.close()

    return {
        "receipt_count": receipt_count,
        "receipt_total": float(receipt_total),
        "unmatched_transactions": unmatched_transactions,
        "uncategorized_line_items": uncategorized_count,
        "uncategorized_amount": float(uncategorized_amount),
        "matches_pending_review": pending_review,
    }