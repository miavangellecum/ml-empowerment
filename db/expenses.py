"""
Unified ledger + expense report. All numbers here are computed in plain
Python/SQL — deliberately, so that db/reporting_agent.py (the LLM layer)
never has to do arithmetic. It only narrates and classifies; it never
computes a dollar figure.

Deduction rates and the non-deductible flag come from the tax_rules table
(db/tax_rules.py) rather than hardcoded constants — change a rate there
and this report reflects it immediately, no redeploy.

Proof-status taxonomy:
  verified       receipt with a confirmed bank match, amounts within 2%
  anomaly        receipt with a confirmed bank match, amounts differ >2%
  pending_proof  receipt with no confirmed bank match, OR a bank charge
                 with no receipt on file (see `missing_side`)
"""
from db.cockroach import get_conn
from db.tax_rules import get_tax_rules, get_tax_rules_map

BUSINESS_USE_VERIFICATION_CATEGORIES = {"meals", "travel", "car_and_truck_expenses"}
AMOUNT_MATCH_TOLERANCE_PCT = 0.02   # the "within 2%" verified/anomaly cutoff
LARGE_TRANSACTION_THRESHOLD = 75.0  # general IRS documentation-threshold rule of thumb; not category-specific


def get_unified_ledger(start_date: str | None = None, end_date: str | None = None,
                        category: str | None = None) -> list[dict]:
    with get_conn() as conn:
        cur = conn.cursor()

        where_clauses_a, where_clauses_b = [], []
        params_a: list = []
        params_b: list = []

        if start_date:
            where_clauses_a.append("r.date >= %s")
            where_clauses_b.append("t.date >= %s")
            params_a.append(start_date)
            params_b.append(start_date)
        if end_date:
            where_clauses_a.append("r.date <= %s")
            where_clauses_b.append("t.date <= %s")
            params_a.append(end_date)
            params_b.append(end_date)
        if category:
            where_clauses_a.append("li.category = %s")
            params_a.append(category)

        where_sql_a = ("WHERE " + " AND ".join(where_clauses_a)) if where_clauses_a else ""
        where_sql_b_extra = (" AND " + " AND ".join(where_clauses_b)) if where_clauses_b else ""

        cur.execute(
            f"""
            SELECT li.id AS row_id, li.category, li.total_price AS amount,
                   r.date, r.store_name AS source, li.name AS description,
                   'receipt' AS origin, r.payment_method, r.s3_url,
                   cm.amount_diff_pct AS matched_amount_diff_pct,
                   NULL AS item_id
            FROM line_items li
            JOIN receipts r ON r.id = li.receipt_id
            LEFT JOIN LATERAL (
                SELECT m.amount_diff_pct
                FROM receipt_transaction_matches m
                WHERE m.receipt_id = r.id AND m.status = 'confirmed'
                ORDER BY m.amount_diff_pct ASC
                LIMIT 1
            ) cm ON true
            {where_sql_a}

            UNION ALL

            SELECT t.id AS row_id, 'uncategorized' AS category, ABS(t.amount) AS amount,
                   t.date, COALESCE(t.merchant_name, t.name) AS source, t.name AS description,
                   'transaction_only' AS origin, NULL AS payment_method, NULL AS s3_url,
                   NULL AS matched_amount_diff_pct,
                   t.item_id
            FROM plaid_transactions t
            WHERE NOT EXISTS (
                SELECT 1 FROM receipt_transaction_matches m
                WHERE m.transaction_id = t.id AND m.status = 'confirmed'
            )
            {where_sql_b_extra}

            ORDER BY date DESC
            """,
            params_a + params_b,
        )
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()

    for r in rows:
        r["row_id"] = str(r["row_id"])
        r["amount"] = float(r["amount"]) if r["amount"] is not None else 0.0

        if r["origin"] == "transaction_only":
            r["proof_status"] = "pending_proof"
            r["missing_side"] = "receipt"
        elif r["matched_amount_diff_pct"] is not None:
            diff = float(r["matched_amount_diff_pct"])
            r["proof_status"] = "verified" if diff <= AMOUNT_MATCH_TOLERANCE_PCT else "anomaly"
            r["missing_side"] = None
        else:
            r["proof_status"] = "pending_proof"
            r["missing_side"] = "transaction"
        r.pop("matched_amount_diff_pct", None)

    return rows


def get_expense_report(start_date: str | None = None, end_date: str | None = None) -> dict:
    ledger = get_unified_ledger(start_date, end_date)
    tax_rules = get_tax_rules_map()  # {category: {deduction_rate, requires_receipt, ...}}

    by_category: dict[str, dict] = {}
    total_deductible = 0.0
    total_non_deductible = 0.0
    total_expense_amount = 0.0
    verified_amount = 0.0
    anomaly_rows, unreceipted_rows, pending_receipt_rows = [], [], []

    for row in ledger:
        amount = row["amount"]
        category = row["category"]
        total_expense_amount += amount

        if row["proof_status"] == "verified":
            verified_amount += amount
        elif row["proof_status"] == "anomaly":
            anomaly_rows.append(row)
        elif row["origin"] == "transaction_only":
            unreceipted_rows.append(row)
        else:
            pending_receipt_rows.append(row)

        if row["origin"] == "transaction_only":
            continue  # not yet categorizable with confidence — left to the LLM layer as NEEDS_REVIEW

        rule = tax_rules.get(category)
        deduction_rate = rule["deduction_rate"] if rule else 1.0  # unknown category: treat as fully deductible rather than silently dropping it
        deductible_amount = amount * deduction_rate
        if deduction_rate == 0.0:
            total_non_deductible += amount
        total_deductible += deductible_amount

        bucket = by_category.setdefault(category, {"total_amount": 0.0, "deductible_amount": 0.0, "count": 0})
        bucket["total_amount"] += amount
        bucket["deductible_amount"] += deductible_amount
        bucket["count"] += 1

    total_category_spend = sum(b["total_amount"] for b in by_category.values())
    for cat, stats in by_category.items():
        stats["percent_of_total"] = round(
            (stats["total_amount"] / total_category_spend * 100) if total_category_spend else 0.0, 2
        )
        stats["total_amount"] = round(stats["total_amount"], 2)
        stats["deductible_amount"] = round(stats["deductible_amount"], 2)
        rule = tax_rules.get(cat)
        stats["deduction_rate"] = rule["deduction_rate"] if rule else 1.0
        stats["rule_description"] = rule["rule_description"] if rule else None

    audit_readiness_score = round(
        (verified_amount / total_expense_amount * 100) if total_expense_amount else 0.0, 2
    )

    large_unreceipted = [
        {"date": str(r["date"]), "source": r["source"], "amount": round(r["amount"], 2)}
        for r in unreceipted_rows if r["amount"] > LARGE_TRANSACTION_THRESHOLD
    ]
    # Every unreceipted charge, not just the $75+ ones — large_unreceipted
    # above stayed the ONLY place these surfaced anywhere (report JSON,
    # PDF, reports.html), which meant sub-$75 unreceipted transactions
    # were silently invisible everywhere, not just deprioritized.
    all_unreceipted = [
        {"date": str(r["date"]), "source": r["source"], "amount": round(r["amount"], 2),
         "over_threshold": r["amount"] > LARGE_TRANSACTION_THRESHOLD}
        for r in sorted(unreceipted_rows, key=lambda r: -r["amount"])
    ]

    business_use_categories = {
        cat: stats for cat, stats in by_category.items() if cat in BUSINESS_USE_VERIFICATION_CATEGORIES
    }

    return {
        "start_date": start_date,
        "end_date": end_date,
        "by_category": by_category,
        "total_deductible": round(total_deductible, 2),
        "total_non_deductible": round(total_non_deductible, 2),
        "total_expense_amount": round(total_expense_amount, 2),
        "audit_readiness_score_pct": audit_readiness_score,
        "verified_amount": round(verified_amount, 2),
        "counts": {
            "verified": sum(1 for r in ledger if r["proof_status"] == "verified"),
            "anomaly": len(anomaly_rows),
            "pending_proof_receipts": len(pending_receipt_rows),
            "unreceipted_transactions": len(unreceipted_rows),
        },
        "audit_flags": {
            "anomalies": [
                {"date": str(r["date"]), "source": r["source"], "amount": round(r["amount"], 2)}
                for r in anomaly_rows
            ],
            "large_unreceipted_transactions_over_75": large_unreceipted,
            "all_unreceipted_transactions": all_unreceipted,
            "business_use_verification_needed": business_use_categories,
        },
        "needs_review_rows": [
            {"row_id": r["row_id"], "date": str(r["date"]), "source": r["source"],
             "description": r["description"], "amount": round(r["amount"], 2)}
            for r in unreceipted_rows
        ],
        # NEW: the actual itemized rows, one per receipt line item or
        # unreceipted charge, each with its real date. This is what
        # db/reporting_agent.py's Section 2 table should be built from —
        # by_category has no per-row dates to give it, which was the root
        # cause of dates going missing in the generated report.
        "ledger_rows": [
            {"date": str(r["date"]), "source": r["source"], "description": r["description"],
             "amount": round(r["amount"], 2), "category": r["category"],
             "proof_status": r["proof_status"], "origin": r["origin"]}
            for r in ledger
        ],
        "tax_rules": get_tax_rules(),
    }