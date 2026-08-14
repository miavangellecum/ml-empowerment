from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os


from dotenv import load_dotenv
load_dotenv()

from extraction.extraction import process_receipt
from db.db import save_receipt, init_db
from backend.aws_clients import upload_file_to_s3

from backend.plaid_db import init_plaid_db
from backend.plaid_routes import router as plaid_router

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

init_db()
init_plaid_db()

app.include_router(plaid_router)

# Keep in sync with MAX_UPLOAD_MB in frontend/scan.html. Without this,
# a large receipt photo/PDF would silently sail past OCR (PaddleOCR/pdfium
# on a huge image can take minutes or exhaust memory) and the request would
# just hang with no feedback to the user.
MAX_UPLOAD_MB = 15
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024


@app.post("/extract")
async def extract_receipt(file: UploadFile = File(...)):
    temp_path = f"temp_{file.filename}"

    # Stream to disk in chunks so we can bail out as soon as the size limit
    # is crossed, instead of buffering an arbitrarily large file first.
    written = 0
    try:
        with open(temp_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds the {MAX_UPLOAD_MB}MB upload limit.",
                    )
                f.write(chunk)
    except HTTPException:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise HTTPException(status_code=400, detail="Could not read the uploaded file.")

    try:
        # Upload original file to S3 for permanent storage
        s3_key = f"receipts/{file.filename}"
        s3_url = upload_file_to_s3(temp_path, s3_key)

        receipt = process_receipt(temp_path)  # returns Pydantic model
        receipt_dict = receipt.model_dump()

        receipt_id = save_receipt(receipt_dict)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    return {"receipt_id": receipt_id, "s3_url": s3_url, "data": receipt_dict}


@app.get("/receipts")
async def get_receipts():
    import sqlite3
    conn = sqlite3.connect("db/receipts.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM receipts ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]