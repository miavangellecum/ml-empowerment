import os
from pathlib import Path

os.environ["FLAGS_use_mkldnn"] = "false"

from paddleocr import PaddleOCR

ocr = PaddleOCR(
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    lang='en',
    device='cpu',
    enable_mkldnn=False
)

def convert_pdf_to_png(pdf_path: str) -> str:
    import pypdfium2 as pdfium
    png_path = str(Path(pdf_path).with_suffix(".png"))
    pdf = pdfium.PdfDocument(pdf_path)
    bitmap = pdf[0].render(scale=2)
    bitmap.to_pil().save(png_path)
    pdf.close()
    return png_path

def get_image_path(input_path: str) -> str:
    if input_path.lower().endswith(".pdf"):
        return convert_pdf_to_png(input_path)
    return input_path

def extract_text(image_path: str) -> tuple[list[str], float]:
    """
    Returns (line_texts, mean_confidence).
    mean_confidence is the average PaddleOCR recognition score across
    all detected lines, 0.0-1.0. Low confidence signals a faded,
    skewed, or handwritten receipt that OCR struggled with.
    """
    image_path = get_image_path(image_path)
    result = ocr.predict(image_path)
    for res in result:
        texts = res["rec_texts"]
        scores = res.get("rec_scores", [])
        mean_conf = sum(scores) / len(scores) if scores else 0.0
        return texts, mean_conf
    return [], 0.0