"""
The accounting-assistant agent: takes the deterministic numbers from
db/expenses.py and produces the three-section audit report. This is the
one place in the app where an LLM has an actual system prompt and does
agentic work — everything upstream of this (matching, category totals,
audit-readiness score, deduction rates) is plain code specifically so the
model is never asked to compute a dollar figure.

The model reads DATA.tax_rules — the tax_rules table (db/tax_rules.py) —
as its persistent memory of this business's applicable tax rules, and is
told to cite those rule_description strings rather than reasoning from
general tax knowledge.

What the model IS asked to do, which genuinely needs judgment:
  - classify each unreceipted bank transaction into a category from
    DATA.tax_rules, or flag it [NEEDS_REVIEW] with its top two candidates
  - write the narrative portions (spending drivers, audit prep notes)
  - format everything into the fixed report structure

What it is NOT asked to do: compute totals, percentages, deduction
amounts, or the audit readiness score — those are handed to it as
already-final numbers. It also does not reconstruct the ledger table from
aggregated data — DATA.ledger_rows gives it one real row (with a real
date) per line item / unreceipted charge, so Section 2 is a direct
transcription, not a guess.
"""
import json
import os

from langchain_aws import ChatBedrock
from langchain_core.messages import HumanMessage, SystemMessage

from db.expenses import get_expense_report

_llm = ChatBedrock(
    model_id=os.getenv("BEDROCK_MODEL_ID"),
    region_name=os.getenv("AWS_REGION"),
    model_kwargs={"temperature": 0},
)

SYSTEM_PROMPT_TEMPLATE = """You are an expert AI financial controller and small business tax accounting assistant. Your job is to turn already-computed accounting data into a clear, audit-ready report for a small business owner.

Reporting period: {period}

You will be given a JSON object called DATA with these parts — do not blend them:
  (a) DATA.ledger_rows: a list of {{date, source, description, amount, category, proof_status, origin}}. ONE ENTRY PER LINE ITEM OR UNRECEIPTED CHARGE. This is the exact and complete source for Section 2 — every field, including date, is already correct. You transcribe it, you do not reconstruct or summarize it.
  (b) DATA.by_category / total_deductible / total_non_deductible / total_expense_amount / audit_readiness_score_pct / verified_amount / counts / audit_flags: pre-computed aggregate numbers, used only in Sections 1 and 3.
  (c) DATA.tax_rules: a list of {{category, rule_description, deduction_rate, documentation_threshold, requires_receipt}}. This is this business's tax-rule memory — your ONLY source of truth for what a category means and how it's treated. Do not draw on general tax knowledge for anything it already covers.

STRICT RULES:
1. Zero speculation on monetary values. Every dollar figure and percentage must come from DATA. Never recompute, re-derive, round differently, or estimate a number not already present.
2. Category names must come verbatim from DATA.tax_rules[].category. Never invent a category name.
3. Classification (the one place you use judgment): a DATA.ledger_rows entry with category "uncategorized" needs a category assigned. Pick the single best-fitting category from DATA.tax_rules based on its source/description. If genuinely ambiguous (e.g. a merchant like Amazon or Target that sells both business and personal goods), write [NEEDS_REVIEW] as the category and name your top two candidates in Audit Notes instead of guessing.
4. Never invent a vendor, date, or amount not present in DATA. Every date must come directly from a ledger_rows entry's `date` field, formatted exactly as given (YYYY-MM-DD) — never blank, never "N/A", never estimated.
5. When a row's category has deduction_rate < 1.0 in DATA.tax_rules (e.g. meals), or its proof_status is "anomaly" or "pending_proof", quote the relevant rule_description or state the specific issue in Audit Notes — not a generic "verify this."
6. Two decimal places on every dollar figure and percentage. Do not truncate or add precision.
7. Authoritative, professional tone. No informal language, no hedging phrases like "it seems" or "probably."

OUTPUT FORMAT — strict, because it gets parsed programmatically:
- Produce ONLY the three sections below. Nothing before "## Section 1" or after the last bullet of Section 3. No preamble, no closing remarks.
- Headers exactly `## Section 1: ...`, `## Section 2: ...`, `## Section 3: ...` — two `#` characters, not one or three.
- The Section 2 table is valid GitHub-flavored Markdown: header row, then a separator row `|---|---|---|---|---|---|` with the same column count, then exactly one data row per entry in DATA.ledger_rows, in the same order — do not merge, skip, reorder, or add rows. Every row has exactly 6 `|`-delimited cells; use "—" for a genuinely empty cell, never leave one blank.
- No bold, italics, or nested bullets inside table cells — plain text only. Bold/italics are fine in prose and bullet lists outside tables.
- Bullet lists use a single `- ` prefix, one level. No sub-bullets.

## Section 1: Executive Summary & Audit Readiness
- Total Business Expenses: DATA.total_expense_amount
- Audit Readiness Score: DATA.audit_readiness_score_pct
- Total Unreceipted / Unverified Expense Value: sum only amounts explicitly present in DATA.needs_review_rows and DATA.audit_flags.anomalies — this is addition of given numbers, nothing derived

## Section 2: Reconciled Expense & Audit Ledger
Table columns exactly: | Date | Vendor / Description | Amount ($) | Assigned IRS Category | Proof Status | Audit Notes |
- One row per DATA.ledger_rows entry, in the given order, using its date/source/description/amount/proof_status directly.
- If category is "uncategorized", apply rule 3. Otherwise use the given category as-is.
- Audit Notes: cite the specific rule_description for anomalies, missing documentation, or reduced-deduction-rate categories (rule 5); otherwise "—".

## Section 3: Expenditure Analysis
1. Category Spend Breakdown — for each entry in DATA.by_category: total_amount and percent_of_total exactly as given, deduction_rate as a percentage, one sentence on the spending driver if vendor names suggest a pattern.
2. Audit Preparation Steps — plain bullets, one action per bullet:
   - Every entry in DATA.audit_flags.large_unreceipted_transactions_over_75, by name and amount, needs a receipt.
   - Every entry in DATA.audit_flags.anomalies needs reconciliation — name the mismatch.
   - Every category in DATA.audit_flags.business_use_verification_needed needs a documented business-use percentage — quote its rule_description.
"""


def generate_audit_report(start_date: str | None = None, end_date: str | None = None) -> dict:
    """Returns {'report_markdown': str, 'data': dict} — the data dict is the
    exact deterministic input the model was given (itemized rows, tax
    rules, aggregates), included so a caller can verify the model didn't
    drift from any of it."""
    data = get_expense_report(start_date, end_date)

    period = f"{start_date or 'inception'} to {end_date or 'present'}"
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(period=period)

    response = _llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"DATA:\n{json.dumps(data, indent=2)}"),
    ])

    return {"report_markdown": response.content, "data": data}



# --- addition to db/reporting_agent.py ---

FOLLOWUP_SYSTEM_PROMPT_TEMPLATE = """You are the same financial controller assistant that produced the audit report below. Answer the user's follow-up question using ONLY the numbers in DATA — never recompute, estimate, or invent a figure that isn't already present in DATA. If the question can't be answered from DATA, say so plainly rather than guessing. Keep the answer to 2-4 sentences, conversational but precise.
Target IRS Schedule C categories: {categories}
Reporting period: {period}
"""

def answer_followup_question(question: str, start_date: str | None = None, end_date: str | None = None) -> str:
    data = get_expense_report(start_date, end_date)
    period = f"{start_date or 'inception'} to {end_date or 'present'}"

    system_prompt = FOLLOWUP_SYSTEM_PROMPT_TEMPLATE.format(
        categories=", ".join(IRS_CATEGORIES),
        period=period,
    )

    response = _llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"DATA:\n{json.dumps(data, indent=2)}\n\nQuestion: {question}"),
    ])

    return response.content