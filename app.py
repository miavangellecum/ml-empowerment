from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import shutil, os


from dotenv import load_dotenv
load_dotenv()

from extraction.extraction import process_receipt
from db.db import save_receipt, get_receipts, init_db
from db.matcher import match_receipt
from db.tax_rules import init_tax_rules
from backend.aws_clients import upload_file_to_s3

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


@app.post("/extract")
async def extract_receipt(file: UploadFile = File(...)):
    temp_path = f"temp_{file.filename}"
    with open(temp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    s3_key = f"receipts/{file.filename}"
    s3_url = upload_file_to_s3(temp_path, s3_key)

    receipt = process_receipt(temp_path)  # returns Pydantic model
    receipt_dict = receipt.model_dump()

    receipt_id = save_receipt(receipt_dict, s3_url=s3_url)
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