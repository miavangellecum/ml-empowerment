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

def parse_receipt(ocr_texts: list[str]) -> Receipt:
    ocr_text = "\n".join(ocr_texts)
    prompt = f"""You are extracting structured data from a receipt/invoice that was read via OCR.
The text below may be out of order since OCR doesn't preserve layout perfectly.
Categorize each item into one of: groceries, dining, transport, household, delivery, other.

#FEW_SHOT_EXAMPLE

Now extract structured data from this OCR text:
{ocr_text}

If you cannot find a specific field's value in the text, do your best estimate from context (e.g. sum of line items), and only omit it if truly absent.
Always include a numeric 'total' value — if there is no explicit total, sum the line items.
"""
    return structured_llm.invoke(prompt)