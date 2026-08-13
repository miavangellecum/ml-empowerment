from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import shutil, os


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

@app.post("/extract")
async def extract_receipt(file: UploadFile = File(...)):
    temp_path = f"temp_{file.filename}"
    with open(temp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Upload original file to S3 for permanent storage
    s3_key = f"receipts/{file.filename}"
    s3_url = upload_file_to_s3(temp_path, s3_key)

    receipt = process_receipt(temp_path)  # returns Pydantic model
    receipt_dict = receipt.model_dump()

    receipt_id = save_receipt(receipt_dict)

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