"""
The accounting-assistant agent: takes the deterministic numbers from
db/expenses.py and produces the three-section audit report. This is the
one place in the app where an LLM has an actual system prompt and does
agentic work — everything upstream of this (matching, category totals,
audit-readiness score) is plain code specifically so the model is never
asked to compute a dollar figure.

What the model IS asked to do, which genuinely needs judgment:
  - classify each unreceipted bank transaction into an IRS category, or
    flag it [NEEDS_REVIEW] with its top two candidates, using the
    merchant name + Plaid's own coarse category as context
  - write the narrative portions (spending drivers, audit prep notes)
  - format everything into the fixed report structure

What it is NOT asked to do: compute totals, percentages, or the audit
readiness score — those are handed to it as already-final numbers, and
the prompt explicitly forbids recomputing them.
"""
import json
import os

from langchain_aws import ChatBedrock
from langchain_core.messages import HumanMessage, SystemMessage

from extraction.llm.extract import IRS_CATEGORIES
from db.expenses import get_expense_report

_llm = ChatBedrock(
    model_id=os.getenv("BEDROCK_MODEL_ID"),
    region_name=os.getenv("AWS_REGION"),
    model_kwargs={"temperature": 0},
)

SYSTEM_PROMPT_TEMPLATE = """You are an expert AI financial controller and small business tax accounting assistant. Your job is to turn already-computed accounting data into a clear, audit-ready report for a small business owner.

Target IRS Schedule C categories: {categories}
Reporting period: {period}

You will be given a JSON object called DATA containing the reconciled ledger and pre-computed totals. Follow these rules exactly:

1. Zero speculation on monetary values: every dollar figure and percentage in your report must come directly from DATA. Never recompute, re-derive, round differently, or estimate a number that isn't already present in DATA.
2. Classification (the one place you should use judgment): DATA.needs_review_rows lists bank transactions with no receipt on file, category "uncategorized". For each one, assign the single best-fitting category from the list above based on its description/source. If it is genuinely ambiguous (e.g. a merchant like Amazon or Target that sells both business and personal goods, or a description with no clear business purpose), mark it [NEEDS_REVIEW] and list your top two candidate categories instead of guessing.
3. Never invent a vendor, date, or amount that is not present in DATA.
4. Business-use verification: for any category in DATA.audit_flags.business_use_verification_needed (meals, travel, car_and_truck_expenses), note in your audit prep steps that business-use percentage should be confirmed for those line items — do not assume 100% business use.
5. Maintain decimal accuracy exactly as given (two decimal places); do not truncate or round further.
6. Use an authoritative, professional accounting tone. No informal language.

Deliverable format — produce exactly these three sections, in Markdown:

## Section 1: Executive Summary & Audit Readiness
- Total Business Expenses: use DATA.total_expense_amount
- Audit Readiness Score: use DATA.audit_readiness_score_pct exactly as given
- Total Unreceipted / Unverified Expense Value: derive from DATA.counts and the amounts in DATA.needs_review_rows and DATA.audit_flags.anomalies — sum only amounts explicitly present in DATA, do not estimate

## Section 2: Reconciled Expense & Audit Ledger
A table: | Date | Vendor / Description | Amount ($) | Assigned IRS Category | Proof Status | Audit Notes |
- For rows already categorized in DATA.by_category, use their given category and proof status as-is.
- For rows in DATA.needs_review_rows, apply rule 2 above to assign a category (or [NEEDS_REVIEW]).
- Audit Notes: call out anomalies (amount mismatch vs. matched bank charge), missing documentation, or business-use verification needed.

## Section 3: Expenditure Analysis
1. Category Spend Breakdown — for each entry in DATA.by_category, report its total_amount and percent_of_total exactly as given, with one sentence on what's driving that category if the vendor names suggest a pattern.
2. Audit Preparation Steps — itemized bullets:
   - Every transaction in DATA.audit_flags.large_unreceipted_transactions_over_75, by name, needs a receipt
   - Every entry in DATA.audit_flags.anomalies needs reconciliation (amount mismatch)
   - Categories in DATA.audit_flags.business_use_verification_needed need a documented business-use percentage
"""


def generate_audit_report(start_date: str | None = None, end_date: str | None = None) -> dict:
    """Returns {'report_markdown': str, 'data': dict} — the data dict is the
    exact deterministic input the model was given, included so a caller (or
    a human reviewer) can verify the model didn't drift from the source
    numbers."""
    data = get_expense_report(start_date, end_date)

    period = f"{start_date or 'inception'} to {end_date or 'present'}"
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        categories=", ".join(IRS_CATEGORIES),
        period=period,
    )

    response = _llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"DATA:\n{json.dumps(data, indent=2)}"),
    ])

    return {"report_markdown": response.content, "data": data}
