"""
Safe, constrained tools for the query agent (db/query_agent.py) to call.

CRITICAL DESIGN CONSTRAINT: the LLM never writes SQL. It picks a tool
name and typed arguments; each tool runs one fixed, already-audited,
parameterized query (the same ones db/expenses.py and
backend/matches_routes.py already use elsewhere). Arguments are validated
against known-safe values *before* any query executes — an invalid
argument returns an error to the model instead of ever reaching
CockroachDB. This is the "structured process, not free rein" the design
is built around.
"""
from datetime import datetime

from langchain_core.tools import tool

from db.cockroach import get_conn
from db.expenses import get_expense_report
from db.tax_rules import get_tax_rules_map


def _valid_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


@tool
def query_category_spend(category: str, start_date: str | None = None, end_date: str | None = None) -> dict:
    """Get total spend, deductible amount, and item count for one IRS Schedule C
    category (e.g. 'meals', 'travel', 'office_expense') in a date range. Use this
    for questions like 'how much did I spend on meals last quarter'. category must
    be one of this business's known tax_rules categories — call lookup_tax_rule
    with no arguments first if unsure of the exact category name."""
    known = get_tax_rules_map()
    if category not in known:
        return {"error": f"'{category}' is not a known category.", "known_categories": list(known.keys())}
    if start_date and not _valid_date(start_date):
        return {"error": f"start_date '{start_date}' is not in YYYY-MM-DD format."}
    if end_date and not _valid_date(end_date):
        return {"error": f"end_date '{end_date}' is not in YYYY-MM-DD format."}

    report = get_expense_report(start_date, end_date)
    stats = report["by_category"].get(category)
    if not stats:
        return {
            "category": category, "total_amount": 0.0, "deductible_amount": 0.0, "count": 0,
            "note": "No spend recorded in this category for the given period.",
        }
    return {"category": category, **stats}


@tool
def list_pending_matches(limit: int = 10) -> list[dict]:
    """List receipt-to-bank-transaction matches still awaiting human confirmation
    (status='pending'), most recent first. Use this for 'what needs my review'."""
    limit = max(1, min(int(limit), 50))  # hard cap regardless of what the model requests
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT r.store_name, r.total AS receipt_total, t.merchant_name, t.amount AS tx_amount,
                   m.confidence, m.created_at
            FROM receipt_transaction_matches m
            JOIN receipts r ON r.id = m.receipt_id
            JOIN plaid_transactions t ON t.id = m.transaction_id
            WHERE m.status = 'pending'
            ORDER BY m.created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()

    for r in rows:
        r["receipt_total"] = float(r["receipt_total"])
        r["tx_amount"] = float(r["tx_amount"])
        r["confidence"] = float(r["confidence"])
        r["created_at"] = str(r["created_at"])
    return rows


@tool
def lookup_tax_rule(category: str | None = None):
    """Look up this business's tax rule for one category (deduction_rate,
    requires_receipt, rule_description), or omit category to list every known
    category and its rule."""
    rules = get_tax_rules_map()
    if category:
        if category not in rules:
            return {"error": f"'{category}' is not a known category.", "known_categories": list(rules.keys())}
        return rules[category]
    return list(rules.values())


@tool
def get_audit_readiness_summary(start_date: str | None = None, end_date: str | None = None) -> dict:
    """Get the business's overall audit readiness score (% of total spend backed by a
    confirmed, amount-matching bank charge), verified vs. total expense amounts, and
    counts of anomalies/pending reviews/unreceipted transactions for a date range. Use
    this for questions like 'what is my audit score' or 'how ready am I for an audit'."""
    if start_date and not _valid_date(start_date):
        return {"error": f"start_date '{start_date}' is not in YYYY-MM-DD format."}
    if end_date and not _valid_date(end_date):
        return {"error": f"end_date '{end_date}' is not in YYYY-MM-DD format."}

    report = get_expense_report(start_date, end_date)
    return {
        "audit_readiness_score_pct": report["audit_readiness_score_pct"],
        "verified_amount": report["verified_amount"],
        "total_expense_amount": report["total_expense_amount"],
        "total_deductible": report["total_deductible"],
        "counts": report["counts"],
    }


ALL_TOOLS = [query_category_spend, list_pending_matches, lookup_tax_rule, get_audit_readiness_summary]