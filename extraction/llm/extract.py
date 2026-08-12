from extraction.schema.schema import Receipt
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_aws import ChatBedrock
from extraction.schema.schema import Receipt
import os

llm = ChatBedrock(
    model_id=os.getenv("BEDROCK_MODEL_ID"),
    region_name=os.getenv("AWS_REGION"),
    model_kwargs={"temperature": 0},
)
structured_llm = llm.with_structured_output(Receipt)

from pydantic import ValidationError

FEW_SHOT_EXAMPLE = """Example OCR text:
Albert Heijn
Factuur
Datum 29 april 2025
Factuurnummer 155881-00001
Totaal inclusief btw 59,50
Boodschappen, zie specificatie 9% 46,59 4,21 50,80
Bezorgkosten 21% 4,71 0,99 5,70
Totaal 54,30 5,20 59,50

Example output:
{"store_name": "Albert Heijn", "date": "2025-04-29", "invoice_number": "155881-00001",
 "currency": "EUR", "items": [
   {"name": "Boodschappen, zie specificatie", "total_price": 50.80, "vat_rate": "9%", "category": "groceries"},
   {"name": "Bezorgkosten", "total_price": 5.70, "vat_rate": "21%", "category": "delivery"}
 ], "subtotal": 54.30, "vat_total": 5.20, "total": 59.50}"""


def parse_receipt(ocr_texts: list[str]) -> Receipt:
    ocr_text = "\n".join(ocr_texts)
    prompt = f"""You are extracting structured data from a receipt/invoice that was read via OCR.
The text below may be out of order since OCR doesn't preserve layout perfectly.
Categorize each item into one of: groceries, dining, transport, household, delivery, other.

{FEW_SHOT_EXAMPLE}

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