from langchain_google_genai import ChatGoogleGenerativeAI
from extraction.schema.schema import Receipt

llm = ChatGoogleGenerativeAI(model="gemini-3.6-flash", temperature=0)
structured_llm = llm.with_structured_output(Receipt)

FEW_SHOT_EXAMPLE = """
Example OCR text (jumbled order, from a Dutch grocery receipt):
Factuur | Albert Heijn B.V. | Datum | 29 april 2025 | Boodschappen | 46,59 | 9% | 50,80 | Totaal | 59,50

Example correct output:
{
  "store_name": "Albert Heijn",
  "date": "2025-04-29",
  "items": [
    {"name": "Boodschappen", "total_price": 50.80, "vat_rate": "9%", "category": "groceries"}
  ],
  "subtotal": 46.59,
  "total": 59.50,
  "currency": "EUR"
}
"""

def parse_receipt(ocr_texts: list[str]) -> Receipt:
    ocr_text = "\n".join(ocr_texts)
    prompt = f"""You are extracting structured data from a receipt/invoice that was read via OCR.
The text below may be out of order since OCR doesn't preserve layout perfectly.
Categorize each item into one of: groceries, dining, transport, household, delivery, other.

{FEW_SHOT_EXAMPLE}

Now extract structured data from this OCR text:
{ocr_text}
"""
    return structured_llm.invoke(prompt)