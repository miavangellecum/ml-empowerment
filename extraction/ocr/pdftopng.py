# we try to solve the recurrent paddlepaddle bug on windows (often triggered by pdf)
import pypdfium2 as pdfium

pdf = pdfium.PdfDocument("sample_receipt.pdf")
page = pdf[0]
bitmap = page.render(scale=2)  # scale up for better OCR resolution
pil_image = bitmap.to_pil()
pil_image.save("sample_receipt.png")