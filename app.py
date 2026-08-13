from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import shutil, os


from dotenv import load_dotenv
load_dotenv()

from extraction.extraction import process_receipt
from db.db import save_receipt, get_receipts, init_db
from db.matcher import match_receipt
from backend.aws_clients import upload_file_to_s3

from backend.plaid_routes import router as plaid_router
from backend.matches_routes import router as matches_router
from backend.reports_routes import router as reports_router

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

init_db()

app.include_router(plaid_router)
app.include_router(matches_router)
app.include_router(reports_router)


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

    receipt_id = save_receipt(receipt_dict, s3_url=s3_url)

    # Ask the matching agent to look for a Plaid transaction that already
    # covers this receipt (e.g. the bank charge posted before you got
    # around to uploading the receipt). Matching a fresh receipt against
    # existing transactions is fast and non-blocking enough to just do
    # inline here; if it ever gets slow, move this to a background task.
    matches = match_receipt(receipt_id)

    os.remove(temp_path)
    return {
        "receipt_id": receipt_id,
        "s3_url": s3_url,
        "data": receipt_dict,
        "matches": matches,
    }


@app.get("/receipts")
async def get_receipts_route():
    return get_receipts()