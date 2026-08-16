from extraction.schema.schema import Receipt
from langchain_aws import ChatBedrock
from langchain_core.messages import HumanMessage
from pydantic import ValidationError  # was missing — the retry path would have crashed
import base64
import os

llm = ChatBedrock(
    model_id=os.getenv("BEDROCK_MODEL_ID"),
    region_name=os.getenv("AWS_REGION"),
    model_kwargs={"temperature": 0},
)
structured_llm = llm.with_structured_output(Receipt)


OCR_CONFIDENCE_THRESHOLD = 0.75  # TODO: tune against real test receipts

# IRS Schedule C, Part II expense line items (the categories a sole
# proprietor / small business actually files under). Keeping this list in
# one place so the extraction prompt and the expense report line up.
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
    "meals",  # only 50% deductible — flagged separately in the report, not folded into "travel"
    "utilities",
    "wages",
    "other expenses",
    "personal non deductible",  # explicitly NOT a business expense — keeps personal spend out of the deduction totals instead of forcing it into "other"
]

_CATEGORY_INSTRUCTIONS = f"""Categorize each line item into exactly one of these IRS Schedule C expense categories: {", ".join(IRS_CATEGORIES)}.
- Use "meals" only for food/drink at restaurants, cafes, or catering — it is tax-limited to 50% deductible, so it must never be folded into "travel" or "office_expense".
- Use "personal_non_deductible" for anything that reads as personal spending rather than a business expense (e.g. groceries for a household, personal clothing) — do not force these into "other_expenses".
- If a receipt has multiple mixed items, categorize each line item individually rather than giving the whole receipt one category.

If you cannot find a specific field's value, do your best estimate from context (e.g. sum of line items), and only omit it if truly absent.
Always include a numeric 'total' value — if there is no explicit total, sum the line items."""

def parse_receipt(ocr_texts: list[str], ocr_confidence: float, image_path: str) -> Receipt:
    """
    Structures a receipt into the Receipt schema.
    Uses OCR'd text when confidence is high (cheap, fast).
    Falls back to sending the image directly to Claude's vision input
    when OCR confidence is low — layout and spacing that OCR flattens
    away often make faded or handwritten receipts readable to Claude
    even when the OCR text itself is garbage.
    """
    if ocr_confidence >= OCR_CONFIDENCE_THRESHOLD:
        return _parse_from_text(ocr_texts)
    else:
        return _parse_from_image(image_path)

def _parse_from_text(ocr_texts: list[str]) -> Receipt:
    ocr_text = "\n".join(ocr_texts)
    prompt = f"""You are extracting structured data from a receipt/invoice that was read via OCR, for a small business owner's tax recordkeeping.
The text below may be out of order since OCR doesn't preserve layout perfectly.

{_CATEGORY_INSTRUCTIONS}

Now extract structured data from this OCR text:
{ocr_text}
"""
    return _invoke_with_retry(prompt)

def _parse_from_image(image_path: str) -> Receipt:
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    prompt_text = f"""You are looking at a photo of a receipt/invoice for a small business owner's tax recordkeeping. OCR on this image produced low-confidence results, likely due to fading, skew, or handwriting — read the image directly instead.

{_CATEGORY_INSTRUCTIONS}"""

    message = HumanMessage(
        content=[
            {"type": "text", "text": prompt_text},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",  # adjust if you also accept png
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
            # image message case — append the note as an extra text block
            prompt[0].content.append({"type": "text", "text": retry_note})
            return structured_llm.invoke(prompt)