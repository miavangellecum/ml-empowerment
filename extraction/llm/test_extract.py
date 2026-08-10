# test_extract.py
from extraction.llm.extract import parse_receipt
# paste in the rec_texts list you got from the Albert Heijn OCR run
ocr_output = [
    'on', 'Factuur', 'M. van Gellecum', 'Datum', '29 april 2025',
    'Melchior Treublaan 19', 'Factuurnummer', '155881-00001',
    '2313 VG LEIDEN', 'Totaal inclusief btw 59,50',
    'Boodschappen, zie specificatie', '9%', '46,59', '4,21', '50,80',
    'Bezorgkosten', '21%', '4,71', '0,99', '5,70',
    'Totaal', '54,30', '5,20', '59,50',
    # ... include the rest of your actual rec_texts list here
]

receipt = parse_receipt(ocr_output)
print(receipt.model_dump_json(indent=2))