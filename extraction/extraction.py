from extraction.ocr.ocr import extract_text
from extraction.llm.extract import parse_receipt

def process_receipt(image_path: str):
    ocr_texts = extract_text(image_path)
    receipt = parse_receipt(ocr_texts)
    return receipt