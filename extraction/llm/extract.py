from extraction.schema.schema import Receipt
from langchain_aws import ChatBedrock
import os

llm = ChatBedrock(
    model_id=os.getenv("BEDROCK_MODEL_ID"),
    region_name=os.getenv("AWS_REGION"),
    model_kwargs={"temperature": 0},
)
structured_llm = llm.with_structured_output(Receipt)

# IRS Schedule C, Part II expense line items (the categories a sole
# proprietor / small business actually files under). Keeping this list in
# one place so the extraction prompt and the expense report line up.
IRS_CATEGORIES = [
    "advertising",
    "car_and_truck_expenses",
    "commissions_and_fees",
    "contract_labor",
    "insurance",
    "interest",
    "legal_and_professional_services",
    "office_expense",
    "rent_or_lease",
    "repairs_and_maintenance",
    "supplies",
    "taxes_and_licenses",
    "travel",
    "meals",  # only 50% deductible — flagged separately in the report, not folded into "travel"
    "utilities",
    "wages",
    "other_expenses",
    "personal_non_deductible",  # explicitly NOT a business expense — keeps personal spend out of the deduction totals instead of forcing it into "other"
]


def parse_receipt(ocr_texts: list[str]) -> Receipt:
    ocr_text = "\n".join(ocr_texts)
    category_list = ", ".join(IRS_CATEGORIES)
    prompt = f"""You are extracting structured data from a receipt/invoice that was read via OCR, for a small business owner's tax recordkeeping.
The text below may be out of order since OCR doesn't preserve layout perfectly.

Categorize each line item into exactly one of these IRS Schedule C expense categories: {category_list}.
- Use "meals" only for food/drink at restaurants, cafes, or catering — it is tax-limited to 50% deductible, so it must never be folded into "travel" or "office_expense".
- Use "personal_non_deductible" for anything that reads as personal spending rather than a business expense (e.g. groceries for a household, personal clothing) — do not force these into "other_expenses".
- If a receipt has multiple mixed items, categorize each line item individually rather than giving the whole receipt one category.

Now extract structured data from this OCR text:
{ocr_text}

If you cannot find a specific field's value in the text, do your best estimate from context (e.g. sum of line items), and only omit it if truly absent.
Always include a numeric 'total' value — if there is no explicit total, sum the line items.
"""
    try:
        return structured_llm.invoke(prompt)
    except ValidationError as e:
        # one retry with an explicit nudge toward clean JSON args
        retry_prompt = prompt + "\n\nIMPORTANT: Return each field as a single clean value only — do not include any XML/tool-call tags or extra text inside a field value."
        return structured_llm.invoke(retry_prompt)