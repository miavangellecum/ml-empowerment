"""
Minimal backend to receive receipt uploads from receipt-upload.html.

Run:
    pip install fastapi uvicorn python-multipart --break-system-packages
    python server.py

Then open receipt-upload.html in your browser. Files land in ./uploads/.
"""

import shutil
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Allow the HTML file (opened via file:// or any local origin) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)


@app.post("/upload")
async def upload_receipt(receipt: UploadFile = File(...)):
    dest = UPLOAD_DIR / receipt.filename
    with dest.open("wb") as f:
        shutil.copyfileobj(receipt.file, f)

    # TODO: your processing logic goes here
    # e.g. OCR the receipt, parse totals, save to a database, etc.

    return {"filename": receipt.filename, "status": "received"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)