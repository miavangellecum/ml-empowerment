"""
Persistent tax-rule memory. Previously, deduction rates and the
non-deductible flag lived as hardcoded Python dicts in db/expenses.py
(PARTIALLY_DEDUCTIBLE = {"meals": 0.5}, etc.) — correct, but not actually
"memory" in any meaningful sense: changing a rule meant a code change and
a redeploy. This table makes it real: db/expenses.py and
db/reporting_agent.py both read these rules at request time, so updating
a row changes behavior immediately, and the reporting agent can cite the
actual rule_description in its audit notes instead of guessing from
general tax knowledge.

Call init_tax_rules() once at app startup (see app.py). Idempotent —
safe to call on every boot, re-seeding just upserts the same rows.
"""
from db.cockroach import get_conn

# (category, rule_description, deduction_rate, documentation_threshold, requires_receipt)
# documentation_threshold: dollar amount above which the IRS expects
# retained proof of payment for this category specifically; NULL where
# the $75 general rule (see db/expenses.py's LARGE_TRANSACTION_THRESHOLD)
# is the only applicable guidance.
_SEED_RULES = [
    ("advertising", "Fully deductible ordinary business advertising expense.", 1.0, 75.0, True),
    ("car_and_truck_expenses", "Deductible based on business-use percentage; mileage or actual-expense method must be applied consistently and documented.", 1.0, 75.0, True),
    ("commissions_and_fees", "Fully deductible.", 1.0, 75.0, True),
    ("contract_labor", "Fully deductible; payments over $600/year to one contractor require a Form 1099-NEC.", 1.0, 75.0, True),
    ("insurance", "Fully deductible business insurance premiums. Does not cover personal health insurance, which follows separate rules.", 1.0, 75.0, True),
    ("interest", "Deductible interest on business loans or business credit.", 1.0, 75.0, True),
    ("legal_and_professional_services", "Fully deductible.", 1.0, 75.0, True),
    ("office_expense", "Fully deductible.", 1.0, 75.0, True),
    ("rent_or_lease", "Fully deductible business rent or lease payments.", 1.0, 75.0, True),
    ("repairs_and_maintenance", "Fully deductible.", 1.0, 75.0, True),
    ("supplies", "Fully deductible.", 1.0, 75.0, True),
    ("taxes_and_licenses", "Fully deductible business taxes and license fees.", 1.0, 75.0, True),
    ("travel", "Fully deductible if a clear business purpose is documented.", 1.0, 75.0, True),
    ("meals", "Only 50% deductible under IRC Section 274(n). Requires an itemized receipt and a documented business purpose — do not assume full deductibility.", 0.5, 75.0, True),
    ("utilities", "Fully deductible business utilities.", 1.0, 75.0, True),
    ("wages", "Fully deductible employee wages. Payroll tax filings (941/940) are handled separately from this deduction.", 1.0, None, True),
    ("other_expenses", "Catch-all category. Recategorize into a more specific line item where possible — this category draws more audit scrutiny.", 1.0, 75.0, True),
    ("personal_non_deductible", "Not a business expense. Not deductible under any circumstance.", 0.0, None, False),
]


def init_tax_rules():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tax_rules (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                jurisdiction TEXT NOT NULL DEFAULT 'US_FEDERAL',
                category TEXT NOT NULL UNIQUE,
                rule_description TEXT,
                deduction_rate DECIMAL NOT NULL DEFAULT 1.0,
                documentation_threshold DECIMAL,
                requires_receipt BOOL NOT NULL DEFAULT true,
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            )
            """
        )
        for category, description, rate, threshold, requires_receipt in _SEED_RULES:
            cur.execute(
                """
                INSERT INTO tax_rules (category, rule_description, deduction_rate, documentation_threshold, requires_receipt)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (category) DO UPDATE SET
                    rule_description = excluded.rule_description,
                    deduction_rate = excluded.deduction_rate,
                    documentation_threshold = excluded.documentation_threshold,
                    requires_receipt = excluded.requires_receipt,
                    updated_at = now()
                """,
                (category, description, rate, threshold, requires_receipt),
            )
        cur.close()


def get_tax_rules() -> list[dict]:
    """List form — used by the reporting agent, which wants the full
    rule_description text to cite in its audit notes."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT category, rule_description, deduction_rate, documentation_threshold, requires_receipt "
            "FROM tax_rules ORDER BY category"
        )
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()

    for r in rows:
        r["deduction_rate"] = float(r["deduction_rate"])
        r["documentation_threshold"] = float(r["documentation_threshold"]) if r["documentation_threshold"] is not None else None
    return rows


def get_tax_rules_map() -> dict[str, dict]:
    """Dict form, keyed by category — used by db/expenses.py's arithmetic,
    which wants O(1) lookup per line item rather than the full row list."""
    return {r["category"]: r for r in get_tax_rules()}
