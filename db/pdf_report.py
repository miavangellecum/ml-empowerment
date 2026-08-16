"""
PDF export of the expense report. Deliberately built straight from
db/expenses.py's deterministic data (get_expense_report()), NOT by
converting the AI narrative markdown to PDF — that would make the PDF's
reliability depend on the model producing perfectly-formed markdown every
time, which is exactly the "format is kind of bad" complaint this file is
here to avoid. The numbers are the part that actually matters for a tax
filing; the AI narrative stays a web-only, on-demand thing.
"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from db.expenses import get_expense_report

_styles = getSampleStyleSheet()
_title_style = ParagraphStyle("ReportTitle", parent=_styles["Title"], fontSize=20, spaceAfter=4)
_subtitle_style = ParagraphStyle("ReportSubtitle", parent=_styles["Normal"], fontSize=10, textColor=colors.grey, spaceAfter=18)
_h2_style = ParagraphStyle("ReportH2", parent=_styles["Heading2"], fontSize=13, spaceBefore=16, spaceAfter=8)
_body_style = _styles["Normal"]
_note_style = ParagraphStyle("ReportNote", parent=_styles["Normal"], fontSize=8.5, textColor=colors.grey, spaceBefore=4)

_TABLE_HEADER_BG = colors.HexColor("#2E2A26")
_TABLE_ALT_BG = colors.HexColor("#F4EDE2")


def _styled_table(data, col_widths=None):
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), _TABLE_HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D8D0C4")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for row_idx in range(1, len(data), 2):
        style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), _TABLE_ALT_BG))
    t.setStyle(TableStyle(style))
    return t


def generate_expense_report_pdf(start_date: str | None = None, end_date: str | None = None) -> bytes:
    report = get_expense_report(start_date, end_date)
    period = f"{start_date or 'Inception'} to {end_date or 'Present'}"

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
    )
    story = []

    story.append(Paragraph("Expense Report", _title_style))
    story.append(Paragraph(f"Reporting period: {period}", _subtitle_style))

    # --- Executive summary ---
    story.append(Paragraph("Executive Summary", _h2_style))
    summary_data = [
        ["Metric", "Value"],
        ["Total Business Expenses", f"${report['total_expense_amount']:.2f}"],
        ["Total Deductible", f"${report['total_deductible']:.2f}"],
        ["Total Non-Deductible (personal)", f"${report['total_non_deductible']:.2f}"],
        ["Audit Readiness Score", f"{report['audit_readiness_score_pct']:.2f}%"],
        ["Verified Amount", f"${report['verified_amount']:.2f}"],
        ["Bank Charges Missing a Receipt", str(report["counts"]["unreceipted_transactions"])],
        ["Amount Anomalies (matched, >2% diff)", str(report["counts"]["anomaly"])],
    ]
    story.append(_styled_table(summary_data, col_widths=[3.2 * inch, 2.3 * inch]))

    # --- Category breakdown ---
    story.append(Paragraph("Category Breakdown", _h2_style))
    cat_rows = [["Category", "Total", "Deductible", "Rate", "% of Spend", "Items"]]
    for cat, stats in sorted(report["by_category"].items(), key=lambda kv: -kv[1]["total_amount"]):
        cat_rows.append([
            cat.replace("_", " "),
            f"${stats['total_amount']:.2f}",
            f"${stats['deductible_amount']:.2f}",
            f"{stats['deduction_rate'] * 100:.0f}%",
            f"{stats['percent_of_total']:.1f}%",
            str(stats["count"]),
        ])
    if len(cat_rows) > 1:
        story.append(_styled_table(cat_rows, col_widths=[1.7 * inch, 0.9 * inch, 0.9 * inch, 0.6 * inch, 0.9 * inch, 0.6 * inch]))
    else:
        story.append(Paragraph("No categorized expenses in this period.", _body_style))

    # --- Audit flags ---
    story.append(Paragraph("Audit Flags", _h2_style))
    flags = report["audit_flags"]

    if flags["anomalies"]:
        story.append(Paragraph("Amount mismatches (matched to a bank charge, but differ by more than 2%):", _body_style))
        rows = [["Date", "Source", "Amount"]] + [[a["date"], a["source"], f"${a['amount']:.2f}"] for a in flags["anomalies"]]
        story.append(_styled_table(rows, col_widths=[1.2 * inch, 3 * inch, 1 * inch]))
        story.append(Spacer(1, 8))

    if flags["large_unreceipted_transactions_over_75"]:
        story.append(Paragraph("Bank charges over $75 with no receipt on file:", _body_style))
        rows = [["Date", "Source", "Amount"]] + [
            [u["date"], u["source"], f"${u['amount']:.2f}"] for u in flags["large_unreceipted_transactions_over_75"]
        ]
        story.append(_styled_table(rows, col_widths=[1.2 * inch, 3 * inch, 1 * inch]))
        story.append(Spacer(1, 8))

    if flags["business_use_verification_needed"]:
        story.append(Paragraph("Categories requiring documented business-use percentage:", _body_style))
        rows = [["Category", "Total", "Rule"]]
        for cat, stats in flags["business_use_verification_needed"].items():
            rows.append([cat.replace("_", " "), f"${stats['total_amount']:.2f}", (stats.get("rule_description") or "")[:70]])
        story.append(_styled_table(rows, col_widths=[1.3 * inch, 0.9 * inch, 3 * inch]))
        story.append(Spacer(1, 8))

    if not (flags["anomalies"] or flags["large_unreceipted_transactions_over_75"] or flags["business_use_verification_needed"]):
        story.append(Paragraph("Nothing flagged.", _body_style))

    story.append(Paragraph(
        "Generated from deterministic accounting data (no LLM involved in these figures). "
        "For the AI-classified narrative version of unreceipted charges, see the in-app AI report.",
        _note_style,
    ))

    doc.build(story)
    return buffer.getvalue()
