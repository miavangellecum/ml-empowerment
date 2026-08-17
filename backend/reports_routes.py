import csv
import io

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from db.expenses import get_unified_ledger, get_expense_report, LARGE_TRANSACTION_THRESHOLD
from db.reporting_agent import generate_audit_report
from db.pdf_report import generate_expense_report_pdf
from db.query_agent import ask
from db.transactions_admin import set_plaid_transaction_category
import re

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/ledger")
async def unified_ledger(start_date: str | None = None, end_date: str | None = None,
                          category: str | None = None):
    """The combined receipt + transaction view: every line item plus every
    unreceipted bank charge, in one list, newest first."""
    return get_unified_ledger(start_date, end_date, category)


@router.get("/summary")
async def expense_summary(start_date: str | None = None, end_date: str | None = None):
    """Deterministic category totals, audit-readiness score, and audit
    flags for a date range (whole history if no dates given). No LLM
    involved — this is the raw numbers, sourced from the tax_rules table
    for deduction rates."""
    return get_expense_report(start_date, end_date)


@router.get("/summary/ai")
async def expense_summary_ai(start_date: str | None = None, end_date: str | None = None):
    """The accounting-assistant agent's narrative report: classifies
    unreceipted transactions against DATA.tax_rules and writes the
    three-section report. One Bedrock call — use /summary if you just
    need the numbers for a UI."""
    return generate_audit_report(start_date, end_date)


@router.post('/summary/ai/apply_classifications')
async def apply_ai_classifications(start_date: str | None = None, end_date: str | None = None):
    """Run the AI audit report, parse Section 2's table for assigned categories,
    and persist non-ambiguous classifications back to plaid_transactions.category.
    Returns a summary of applied updates.
    """
    result = generate_audit_report(start_date, end_date)
    md = result.get('report_markdown', '')
    data = result.get('data', {})

    # Extract lines for Section 2 table
    m = re.search(r"## Section 2: .*?\n(\|[\s\S]*?)\n## Section 3:", md)
    if not m:
        return {"applied": [], "warning": "Couldn't find Section 2 table in AI output."}

    table_md = m.group(1).strip().split('\n')
    # Remove header row and separator
    table_rows = [row for row in table_md if row.strip().startswith('|')]
    if len(table_rows) < 3:
        return {"applied": [], "warning": "Section 2 table appears empty."}

    data_rows = table_rows[2:]  # skip header + separator

    ledger_rows = data.get('ledger_rows', [])

    applied = []

    # Helper to extract 6 columns from a |...| row
    def parse_row_columns(row_md: str):
        parts = [c.strip() for c in row_md.split('|')]
        # split produces leading/trailing empty parts; filter them
        parts = [p for p in parts if p != '']
        # Expect 6 columns: Date, Vendor/Description, Amount ($), Assigned IRS Category, Proof Status, Audit Notes
        if len(parts) < 6:
            # try to pad
            parts += [''] * (6 - len(parts))
        return parts[:6]

    # Iterate ledger_rows in order and match to parsed table rows
    table_index = 0
    for i, ledger in enumerate(ledger_rows):
        # Only care about transaction_only origins (plaid transactions without receipts)
        if ledger.get('origin') != 'transaction_only':
            continue
        if table_index >= len(data_rows):
            break
        cols = parse_row_columns(data_rows[table_index])
        table_index += 1
        assigned_category = cols[3].strip()
        # Skip ambiguous markers
        if not assigned_category or assigned_category.upper().startswith('[NEEDS_REVIEW]') or assigned_category.lower() in ('uncategorized', '—', '-'):
            continue
        # Persist: ledger_rows should include row_id
        row_id = ledger.get('row_id')
        if not row_id:
            continue
        try:
            set_plaid_transaction_category(row_id, assigned_category)
            applied.append({"row_id": row_id, "assigned_category": assigned_category})
        except Exception as e:
            # collect error but continue
            applied.append({"row_id": row_id, "assigned_category": assigned_category, "error": str(e)})

    return {"applied": applied, "total_candidates": len(ledger_rows)}


class AIReportQuestionRequest(BaseModel):
    question: str


@router.post("/summary/ai/ask")
async def expense_summary_ai_ask(req: AIReportQuestionRequest):
    """Ask a follow-up question about the generated AI report using the
    same financial query agent used elsewhere in the app."""
    return ask(req.question)


@router.get("/summary/pdf")
async def expense_summary_pdf(start_date: str | None = None, end_date: str | None = None):
    """Downloadable PDF, built straight from the deterministic numbers
    (not from the AI markdown) so it's reliable every time — see
    db/pdf_report.py for why."""
    pdf_bytes = generate_expense_report_pdf(start_date, end_date)
    filename = f"expense_report_{start_date or 'all'}_to_{end_date or 'all'}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/summary/csv")
async def expense_summary_csv(start_date: str | None = None, end_date: str | None = None):
    """A full itemized ledger (every line item + every unreceipted bank
    charge, with proof status), followed by a category summary and audit
    flags — closer to what an accountant actually wants than a
    category-only export."""
    ledger = get_unified_ledger(start_date, end_date)
    report = get_expense_report(start_date, end_date)
    flags = report["audit_flags"]

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    writer.writerow(["-- Itemized Ledger --"])
    writer.writerow(["Date", "Vendor / Description", "Amount", "Category", "Origin", "Proof Status", "Payment Method"])
    for row in ledger:
        writer.writerow([
            row["date"], row["source"], f"{row['amount']:.2f}", row["category"],
            row["origin"], row["proof_status"], row.get("payment_method") or "",
        ])

    writer.writerow([])
    writer.writerow(["-- Category Summary --"])
    writer.writerow(["Category", "Total Amount", "Deductible Amount", "Deduction Rate", "% of Total", "Line Item Count"])
    for category, stats in sorted(report["by_category"].items()):
        writer.writerow([
            category, f"{stats['total_amount']:.2f}", f"{stats['deductible_amount']:.2f}",
            f"{stats['deduction_rate'] * 100:.0f}%", f"{stats['percent_of_total']:.2f}%", stats["count"],
        ])

    writer.writerow([])
    writer.writerow(["TOTAL DEDUCTIBLE", f"{report['total_deductible']:.2f}"])
    writer.writerow(["TOTAL NON-DEDUCTIBLE (personal)", f"{report['total_non_deductible']:.2f}"])
    writer.writerow(["TOTAL EXPENSE AMOUNT", f"{report['total_expense_amount']:.2f}"])
    writer.writerow(["AUDIT READINESS SCORE (%)", f"{report['audit_readiness_score_pct']:.2f}"])

    writer.writerow([])
    writer.writerow(["-- Anomalies (matched but amount differs >2%) --"])
    writer.writerow(["Date", "Source", "Amount"])
    for a in flags["anomalies"]:
        writer.writerow([a["date"], a["source"], f"{a['amount']:.2f}"])

    writer.writerow([])
    writer.writerow([f"-- Unreceipted transactions over ${LARGE_TRANSACTION_THRESHOLD:.0f} --"])
    writer.writerow(["Date", "Source", "Amount"])
    for u in flags["large_unreceipted_transactions_over_75"]:
        writer.writerow([u["date"], u["source"], f"{u['amount']:.2f}"])

    buffer.seek(0)
    filename = f"expense_report_{start_date or 'all'}_to_{end_date or 'all'}.csv"
    return StreamingResponse(
        buffer,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )