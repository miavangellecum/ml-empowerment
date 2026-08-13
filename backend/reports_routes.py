import csv
import io

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from db.expenses import get_unified_ledger, get_expense_report, LARGE_TRANSACTION_THRESHOLD
from db.reporting_agent import generate_audit_report

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
    involved — this is the raw numbers."""
    return get_expense_report(start_date, end_date)


@router.get("/summary/ai")
async def expense_summary_ai(start_date: str | None = None, end_date: str | None = None):
    """The accounting-assistant agent's narrative report: same numbers as
    /summary, plus IRS-category classification of unreceipted transactions
    and the three-section writeup. Slower (one Bedrock call) — use /summary
    if you just need the numbers for a UI."""
    return generate_audit_report(start_date, end_date)


@router.get("/summary/csv")
async def expense_summary_csv(start_date: str | None = None, end_date: str | None = None):
    """Same numbers as /summary, flattened to a CSV a business owner (or
    their accountant) can drop straight into a filing workflow."""
    report = get_expense_report(start_date, end_date)
    flags = report["audit_flags"]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["category", "total_amount", "deductible_amount", "percent_of_total", "line_item_count"])
    for category, stats in sorted(report["by_category"].items()):
        writer.writerow([
            category, f"{stats['total_amount']:.2f}", f"{stats['deductible_amount']:.2f}",
            f"{stats['percent_of_total']:.2f}", stats["count"],
        ])

    writer.writerow([])
    writer.writerow(["TOTAL DEDUCTIBLE", f"{report['total_deductible']:.2f}"])
    writer.writerow(["TOTAL NON-DEDUCTIBLE (personal)", f"{report['total_non_deductible']:.2f}"])
    writer.writerow(["TOTAL EXPENSE AMOUNT", f"{report['total_expense_amount']:.2f}"])
    writer.writerow(["AUDIT READINESS SCORE (%)", f"{report['audit_readiness_score_pct']:.2f}"])
    writer.writerow([])

    writer.writerow(["-- Anomalies (matched but amount differs >2%) --"])
    writer.writerow(["date", "source", "amount"])
    for a in flags["anomalies"]:
        writer.writerow([a["date"], a["source"], f"{a['amount']:.2f}"])

    writer.writerow([])
    writer.writerow([f"-- Unreceipted transactions over ${LARGE_TRANSACTION_THRESHOLD:.0f} --"])
    writer.writerow(["date", "source", "amount"])
    for u in flags["large_unreceipted_transactions_over_75"]:
        writer.writerow([u["date"], u["source"], f"{u['amount']:.2f}"])

    buffer.seek(0)
    filename = f"expense_report_{start_date or 'all'}_to_{end_date or 'all'}.csv"
    return StreamingResponse(
        buffer,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
