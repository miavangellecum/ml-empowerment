from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import shutil, os

from extraction.extraction import extract_text, parse_receipt  # your OCR + LLM functions
from db.db import save_receipt, init_db

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

init_db()

@app.post("/extract")
async def extract_receipt(file: UploadFile = File(...)):
    temp_path = f"temp_{file.filename}"
    with open(temp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    ocr_text = extract_text(temp_path)
    receipt = parse_receipt(ocr_text)  # returns Pydantic model
    receipt_dict = receipt.model_dump()

    receipt_id = save_receipt(receipt_dict)

    os.remove(temp_path)
    return {"receipt_id": receipt_id, "data": receipt_dict}


@app.get("/receipts")
async def get_receipts():
    import sqlite3
    conn = sqlite3.connect("db/receipts.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM receipts ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]