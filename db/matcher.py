"""
The matching agent.

This is what "combines the two tables" for real: given a receipt (or a
freshly-synced Plaid transaction), it vector-searches the other table for
merchants that read as semantically similar ("Trader Joe's #412" vs.
"TRADER JOE S 412 AMSTERDA"), narrows candidates by amount tolerance and a
date window, scores each candidate, and either auto-confirms a match or
leaves it pending for a human to confirm/reject via /matches.

Every candidate considered gets a row in receipt_transaction_matches —
nothing is silently discarded, so a human reviewer (or a future smarter
agent) can always see what the matcher considered and why.
"""
from db.cockroach import get_conn
from db.embeddings import EMBEDDING_DIM

AMOUNT_TOLERANCE_PCT = 0.03     # how close amounts need to be to score as a perfect amount match
AMOUNT_SEARCH_WINDOW = 0.12     # how far out we'll even look before giving up on a candidate
DATE_WINDOW_DAYS = 4            # how many days apart a receipt and charge can be (pending auth vs. posted, etc.)
AUTO_CONFIRM_THRESHOLD = 0.87   # confidence needed to mark a match 'confirmed' with no human involved
CANDIDATE_LIMIT = 5             # top-N nearest neighbors to consider per item


def _score(cosine_dist: float, amount_diff_pct: float, date_diff_days: int) -> float:
    """Blends three signals into one 0-1 confidence score. Amount weighted
    heaviest because two different merchants charging the exact same cent
    amount on the same day is rare; semantic name similarity second;
    date proximity least, since pending-vs-posted dates can legitimately
    drift a couple of days."""
    similarity = max(0.0, 1 - cosine_dist)
    amount_score = max(0.0, 1 - amount_diff_pct / AMOUNT_TOLERANCE_PCT)
    date_score = max(0.0, 1 - date_diff_days / DATE_WINDOW_DAYS)
    return 0.45 * amount_score + 0.35 * similarity + 0.20 * date_score


def _upsert_match(cur, receipt_id, transaction_id, cosine_dist, amount_diff_pct, date_diff_days, confidence):
    status = "confirmed" if confidence >= AUTO_CONFIRM_THRESHOLD else "pending"
    cur.execute(
        """
        INSERT INTO receipt_transaction_matches
            (receipt_id, transaction_id, cosine_distance, amount_diff_pct,
             date_diff_days, confidence, status, matched_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'agent')
        ON CONFLICT (receipt_id, transaction_id) DO UPDATE SET
            cosine_distance = excluded.cosine_distance,
            amount_diff_pct = excluded.amount_diff_pct,
            date_diff_days = excluded.date_diff_days,
            confidence = excluded.confidence,
            updated_at = now()
        RETURNING id
        """,
        (receipt_id, transaction_id, cosine_dist, amount_diff_pct, date_diff_days, confidence, status),
    )
    match_id = cur.fetchone()[0]
    return match_id, status


def match_receipt(receipt_id: str) -> list[dict]:
    """Run right after a receipt is saved (see app.py /extract). Finds
    candidate Plaid transactions and records match rows for each."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT store_embedding, total, date FROM receipts WHERE id = %s",
            (receipt_id,),
        )
        row = cur.fetchone()
        if not row or row[0] is None or row[2] is None:
            cur.close()
            return []
        embedding, total, receipt_date = row
        total = float(total)

        cur.execute(
            f"""
            SELECT id, name, merchant_name, amount, date,
                   merchant_embedding <=> %s::VECTOR({EMBEDDING_DIM}) AS dist
            FROM plaid_transactions
            WHERE merchant_embedding IS NOT NULL
              AND date BETWEEN %s::DATE - INTERVAL '{DATE_WINDOW_DAYS} days'
                           AND %s::DATE + INTERVAL '{DATE_WINDOW_DAYS} days'
              AND ABS(amount) BETWEEN %s AND %s
            ORDER BY dist ASC
            LIMIT {CANDIDATE_LIMIT}
            """,
            (
                embedding, receipt_date, receipt_date,
                total * (1 - AMOUNT_SEARCH_WINDOW), total * (1 + AMOUNT_SEARCH_WINDOW),
            ),
        )
        candidates = cur.fetchall()

        results = []
        for tx_id, name, merchant_name, amount, tx_date, dist in candidates:
            amount_diff_pct = abs(abs(float(amount)) - total) / max(total, 0.01)
            date_diff_days = abs((tx_date - receipt_date).days)
            confidence = _score(dist, amount_diff_pct, date_diff_days)

            match_id, status = _upsert_match(
                cur, receipt_id, tx_id, float(dist), amount_diff_pct, date_diff_days, confidence
            )
            results.append({
                "match_id": str(match_id),
                "transaction_id": str(tx_id),
                "label": merchant_name or name,
                "confidence": round(confidence, 3),
                "status": status,
            })
        cur.close()

    return results


def match_transaction(transaction_id: str) -> list[dict]:
    """Mirror of match_receipt, run right after a new Plaid transaction
    lands (see backend/plaid_routes.py), so receipts already on file that
    predate the bank posting still get linked automatically."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT merchant_embedding, amount, date FROM plaid_transactions WHERE id = %s",
            (transaction_id,),
        )
        row = cur.fetchone()
        if not row or row[0] is None or row[2] is None:
            cur.close()
            return []
        embedding, amount, tx_date = row
        amount = abs(float(amount))

        cur.execute(
            f"""
            SELECT id, store_name, total, date,
                   store_embedding <=> %s::VECTOR({EMBEDDING_DIM}) AS dist
            FROM receipts
            WHERE store_embedding IS NOT NULL
              AND date BETWEEN %s::DATE - INTERVAL '{DATE_WINDOW_DAYS} days'
                           AND %s::DATE + INTERVAL '{DATE_WINDOW_DAYS} days'
              AND total BETWEEN %s AND %s
            ORDER BY dist ASC
            LIMIT {CANDIDATE_LIMIT}
            """,
            (
                embedding, tx_date, tx_date,
                amount * (1 - AMOUNT_SEARCH_WINDOW), amount * (1 + AMOUNT_SEARCH_WINDOW),
            ),
        )
        candidates = cur.fetchall()

        results = []
        for receipt_id, store_name, total, receipt_date, dist in candidates:
            total = float(total)
            amount_diff_pct = abs(amount - total) / max(total, 0.01)
            date_diff_days = abs((tx_date - receipt_date).days)
            confidence = _score(dist, amount_diff_pct, date_diff_days)

            match_id, status = _upsert_match(
                cur, receipt_id, transaction_id, float(dist), amount_diff_pct, date_diff_days, confidence
            )
            results.append({
                "match_id": str(match_id),
                "receipt_id": str(receipt_id),
                "label": store_name,
                "confidence": round(confidence, 3),
                "status": status,
            })
        cur.close()

    return results
