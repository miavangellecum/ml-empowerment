import os
import base64
from extraction.schema.schema import Receipt
from langchain_aws import ChatBedrock
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

llm = ChatBedrock(
    model_id=os.getenv("BEDROCK_MODEL_ID", "eu.anthropic.claude-haiku-4-5-20251001-v1:0"),
    region_name=os.getenv("AWS_REGION", "eu-central-1"),
    model_kwargs={"temperature": 0},
)
structured_llm = llm.with_structured_output(Receipt)

IRS_CATEGORIES = [
    "advertising",
    "car and truck expenses",
    "commissions and fees",
    "contract labor",
    "insurance",
    "interest",
    "legal and professional services",
    "office expense",
    "rent or lease",
    "repairs and maintenance",
    "supplies",
    "taxes and licenses",
    "travel",
    "meals",
    "utilities",
    "wages",
    "other expenses",
    "personal non deductible",
]

_CATEGORY_INSTRUCTIONS = f"""Categorize each line item into exactly one of these IRS Schedule C expense categories: {", ".join(IRS_CATEGORIES)}.
- Use "meals" only for food/drink at restaurants, cafes, or catering — it is tax-limited to 50% deductible, so it must never be folded into "travel" or "office_expense".
- Use "personal_non_deductible" for anything that reads as personal spending rather than a business expense (e.g. groceries for a household, personal clothing) — do not force these into "other_expenses".
- If a receipt has multiple mixed items, categorize each line item individually rather than giving the whole receipt one category.

If you cannot find a specific field's value, do your best estimate from context (e.g. sum of line items), and only omit it if truly absent.
Always include a numeric 'total' value — if there is no explicit total, sum the line items."""

def parse_receipt(image_path: str) -> Receipt:
    """Directly parses receipt images/PDFs into structured data using Claude 3.5 Sonnet Vision."""
    ext = image_path.lower()
    if ext.endswith(".png"):
        media_type = "image/png"
    elif ext.endswith(".pdf"):
        media_type = "application/pdf"
    elif ext.endswith(".webp"):
        media_type = "image/webp"
    else:
        media_type = "image/jpeg"

    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    prompt_text = f"""You are analyzing a photo or PDF of a receipt/invoice for a small business owner's tax recordkeeping. Read the document directly and extract structured data.

{_CATEGORY_INSTRUCTIONS}"""

    message = HumanMessage(
        content=[
            {"type": "text", "text": prompt_text},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": image_b64,
                },
            },
        ]
    )
    return _invoke_with_retry([message])

def _invoke_with_retry(prompt):
    try:
        return structured_llm.invoke(prompt)
    except ValidationError:
        retry_note = "\n\nIMPORTANT: Return each field as a single clean value only — do not include any XML/tool-call tags or extra text inside a field value."
        if isinstance(prompt, str):
            return structured_llm.invoke(prompt + retry_note)
        else:
            prompt[0].content.append({"type": "text", "text": retry_note})
            return structured_llm.invoke(prompt)