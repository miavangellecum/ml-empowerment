import tempfile

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
import logging

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger("uvicorn.error")

from extraction.llm.extract import parse_receipt
...
from db.matcher import match_receipt
from db.tax_rules import init_tax_rules
from backend.aws_clients import upload_file_to_s3, get_presigned_url

from backend.plaid_routes import router as plaid_router
from backend.matches_routes import router as matches_router
from backend.reports_routes import router as reports_router
from backend.agent_routes import router as agent_router
from db.db import save_receipt, get_receipts, get_receipt, init_db, check_duplicate_receipt

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

init_db()
init_tax_rules()

app.include_router(plaid_router)
app.include_router(matches_router)
app.include_router(reports_router)
app.include_router(agent_router)

MAX_UPLOAD_MB = 15
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

@app.post("/extract")
async def extract_receipt(file: UploadFile = File(...)):
    suffix = os.path.splitext(file.filename or "")[1] or ""
    fd, temp_path = tempfile.mkstemp(suffix=suffix, prefix="receipt_")
    os.close(fd)

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
        # Parse the receipt FIRST (before S3 upload) to check for duplicates
        receipt = parse_receipt(temp_path)
        receipt_dict = receipt.model_dump()

        # Check for duplicate BEFORE uploading to S3 and saving to DB
        existing_id = check_duplicate_receipt(
            receipt_dict.get("store_name"),
            receipt_dict.get("date"),
            receipt_dict.get("total"),
        )
        if existing_id:
            raise HTTPException(
                status_code=409,
                detail=f"This receipt looks like a duplicate of one already on file (same store, date, and total) — receipt_id {existing_id}.",
            )

        # Generate a clean S3 key - use timestamp to avoid filename collisions
        import time
        timestamp = int(time.time())
        original_filename = os.path.basename(file.filename or "receipt")
        name, ext = os.path.splitext(original_filename)
        # Clean the filename - remove special characters
        clean_name = "".join(c for c in name if c.isalnum() or c in "._- ")
        s3_key = f"receipts/{timestamp}_{clean_name}{ext}"
        
        # Upload to S3
        s3_url = upload_file_to_s3(temp_path, s3_key)
        
        receipt_id = save_receipt(receipt_dict, s3_url=s3_url)
        matches = match_receipt(receipt_id)

        # Also return the presigned URL directly for immediate viewing
        from backend.aws_clients import get_presigned_url
        presigned_url = get_presigned_url(s3_key)

        return {
            "receipt_id": receipt_id,
            "s3_url": presigned_url,  # Return the presigned URL
            "s3_key": s3_key,         # Also return the key for debugging
            "data": receipt_dict,
            "matches": matches,
        }
    except HTTPException:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        logger.exception("Failed to process receipt")
        raise HTTPException(status_code=400, detail=f"Could not process the receipt: {str(e)}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@app.get("/receipts")
async def list_receipts_route():
    # get_receipts() already presigns s3_url internally (see db/db.py) —
    # presigning again here double-encoded the URL and broke the link.
    return get_receipts()


@app.get("/receipts/{receipt_id}")
async def get_receipt_route(receipt_id: str):
    receipt = get_receipt(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return receipt


# Static files mount must remain last
app.mount("/", StaticFiles(directory="frontend", html=True), name="static")
