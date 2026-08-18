from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
import tempfile

from dotenv import load_dotenv
load_dotenv()

from extraction.llm.extract import parse_receipt
from db.db import save_receipt, get_receipts, get_receipt, init_db
from db.matcher import match_receipt
from db.tax_rules import init_tax_rules
from backend.aws_clients import upload_file_to_s3, get_presigned_url

from backend.plaid_routes import router as plaid_router
from backend.matches_routes import router as matches_router
from backend.reports_routes import router as reports_router
from backend.agent_routes import router as agent_router

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
        s3_key = f"receipts/{file.filename}"
        s3_url = upload_file_to_s3(temp_path, s3_key)

        # Direct LLM Vision extraction via Bedrock
        receipt = parse_receipt(temp_path)
        receipt_dict = receipt.model_dump()

        receipt_id = save_receipt(receipt_dict, s3_url=s3_url)
        matches = match_receipt(receipt_id)

        return {
            "receipt_id": receipt_id,
            "s3_url": s3_url,
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
        raise HTTPException(status_code=400, detail=f"Could not process the receipt: {str(e)}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

def _to_s3_key(s3_url: str) -> str:
    if s3_url.startswith("s3://"):
        return "/".join(s3_url.split("/")[3:])
    return s3_url

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
