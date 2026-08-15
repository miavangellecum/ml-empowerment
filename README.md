# [NAME OF PRODUCT] — an agentic tax-prep memory layer for receipts + bank transactions

[NAME OF PRODUCT] is a small-business tax-season assistant. It ingests receipts (photos/PDFs) and live bank transactions over time, stores everything as persistent, queryable memory in CockroachDB, uses an LLM agent to reconcile the two, classify spend against IRS Schedule C categories, and generate an audit-ready expense report a business owner can hand to their accountant.

The core idea: an agent's memory isn't just a vector store bolted onto a chatbot. It's the receipts, the bank transactions, the confidence-scored links between them, and the audit trail of every decision the agent made — all living in one distributed, transactional database that the agent reads from and writes to over the entire lifetime of a business, not just one session.

---

## Real-world impact

Every small business owner or freelancer hits the same wall every April: a shoebox of receipts, a bank statement full of ambiguous line items ("SQ *THE GRILL HOUSE"), and no idea which of it is actually deductible. Spend automates the unglamorous but high-stakes part of that process — matching, categorizing, and flagging — while keeping a human in the loop for anything the agent isn't confident about, and keeping every intermediate decision inspectable for an actual audit.

---

## Architecture

```
┌─────────────────┐      ┌──────────────────────┐      ┌───────────────────────┐
│   Frontend      │      │   FastAPI backend    │      │ CockroachDB           │
│  (vanilla JS/   │◄────►│  /extract  /plaid/*  │◄────►│  receipts             │
│   HTML/CSS,     │      │  /matches  /reports/*│      │  line_items           │
│   Node/Express) │      │                      │      │  plaid_items          │
└─────────────────┘      └──────────┬───────────┘      │  plaid_accounts       │
                                    │                  │  plaid_transactions   │
                ┌───────────────────┼──────────────────┤  receipt_transaction_ │
                │                   │                  │    matches            │
                ▼                   ▼                  │  (+ VECTOR indexes)   │
       ┌──────────────────┐  ┌──────────────────┐      └───────────────────────┘
       │  Plaid           │  │  AWS Bedrock     │
       │  bank sync       │  │  Claude 3.5 —    │
       └──────────────────┘  │  extraction,     │
                             │  matching, and   │
                             │  the reporting   │
                             │  agent           │
                             └────────┬─────────┘
                                      │
                        ┌─────────────┴───────────────┐
                        │  AWS S3 — original receipt  │
                        │  images/PDFs (permanent,    │
                        │  IRS documentary evidence)  │
                        └─────────────────────────────┘

      Receipt image → PaddleOCR (local) → Bedrock (Claude) structured extraction 
      → Pydantic schema → CockroachDB
```

---

## CockroachDB as the agent's memory layer

CockroachDB isn't a side database here — it *is* the agent's memory. Two of the required CockroachDB tools are used, and used for real production-shaped work, not toy queries:

### 1. Distributed Vector Indexing — semantic merchant matching
`db/matcher.py` is the matching agent. Every receipt's `store_name` and every Plaid transaction's `merchant_name`/`name` is embedded (Amazon Titan Text Embeddings V2 via Bedrock, see `db/embeddings.py`) and stored directly on the row in a `VECTOR(1024)` column — `receipts.store_embedding` and `plaid_transactions.merchant_embedding`. `CREATE VECTOR INDEX` on both columns (`db/db.py`) lets the agent do a single cosine-distance nearest-neighbor query to find "Trader Joe's #412" and "TRADER JOE S 412 AMSTERDA" are the same merchant, scoped further by an amount tolerance window and a date window, entirely in SQL:

```sql
SELECT id, name, merchant_name, amount, date,
       merchant_embedding <=> %s::VECTOR(1024) AS dist
FROM plaid_transactions
WHERE date BETWEEN %s::DATE - INTERVAL '4 days' AND %s::DATE + INTERVAL '4 days'
  AND ABS(amount) BETWEEN %s AND %s
ORDER BY dist ASC LIMIT 5
```

There is no separate vector store, no reindexing job, and no consistency gap — the embedding lives in the same row and the same transaction as the financial data it describes. Every candidate the agent considers (not just the winner) is persisted to `receipt_transaction_matches` with its cosine distance, amount-diff %, date-diff, and a blended confidence score, so a human reviewer can see exactly what the agent weighed and why (`/matches` endpoint). Matches above `AUTO_CONFIRM_THRESHOLD` (0.87) are auto-confirmed; everything else stays `pending` for human review — the agent never silently guesses on money.

### 2. CockroachDB as the single transactional source of truth
Receipts, line items, Plaid items/accounts/transactions, and the match graph between them all live in one distributed relational schema (`db/db.py`, `backend/plaid_db.py`), connected through a pooled `psycopg2` connection (`db/cockroach.py`). This is what lets `db/expenses.py` join receipts and bank charges in a single `UNION ALL` query to build a unified ledger, compute an audit-readiness score, and flag anomalies — all as plain deterministic SQL/Python, specifically so the LLM layer (`db/reporting_agent.py`) is never asked to compute a dollar figure, only to classify and narrate. Every dollar in the final report traces back to a row Cockroach can produce on demand.

*(Optional/roadmap: the CockroachDB Cloud Managed MCP Server and `ccloud` CLI are natural next steps for giving the agent direct, read-only, audit-logged access to cluster state during development — see Roadmap below.)*

---

## AWS integration

- **Amazon Bedrock** — the model layer for three distinct agent tasks, all on Claude 3.5 via `langchain_aws.ChatBedrock`:
  - **Extraction** (`extraction/llm/extract.py`): turns noisy OCR text into a structured `Receipt` Pydantic object, classifying each line item into an IRS Schedule C category (with a validation retry on malformed output).
  - **Matching embeddings** (`db/embeddings.py`): Amazon Titan Text Embeddings V2 for the vector-matching agent above.
  - **Reporting agent** (`db/reporting_agent.py`): given only pre-computed, already-correct numbers from `db/expenses.py`, classifies unreceipted bank charges into IRS categories (or flags `[NEEDS_REVIEW]`) and writes the three-section audit report — explicitly forbidden from recomputing any total or percentage itself.
- **Amazon S3** (`backend/aws_clients.py`) — every original receipt image/PDF is uploaded to S3 on ingestion and the `s3_url` is stored alongside the structured data in CockroachDB (`receipts.s3_url`). This is the permanent documentary evidence the IRS requires — binary files never touch the database itself, only their path does.

---

## What the agent actually does, end to end

1. **Ingest** — a receipt photo/PDF is uploaded (`/extract`). PaddleOCR extracts raw text (`extraction/ocr/ocr.py`, with PDF→PNG via `pypdfium2`); Bedrock/Claude turns it into a structured, IRS-categorized `Receipt`; the original file goes to S3; the structured data + embedding goes to CockroachDB.
2. **Sync** — Plaid Sandbox transactions stream in (`backend/plaid_routes.py`), each embedded and upserted into `plaid_transactions`.
3. **Match** — every new receipt or transaction immediately triggers the matching agent (`db/matcher.py`), which vector-searches the other table and records every candidate it considered.
4. **Reconcile** — `/reports/summary` computes a unified ledger, category totals, deductible amounts (with the 50% meals limitation applied), an audit-readiness score, and flags (amount mismatches, missing receipts over $75, categories needing business-use verification) — all deterministically.
5. **Report** — `/reports/summary/ai` hands those exact numbers to the reporting agent, which classifies the remaining unreceipted charges and writes the audit-ready three-section Markdown report, downloadable as CSV for an accountant.

---

## Tech stack

| Layer | Tech |
|---|---|
| Backend | FastAPI (Python 3.11), uvicorn |
| Database | CockroachDB (managed, vector-indexed) |
| Frontend | Vanilla JS/HTML/CSS, served via Node.js/Express |
| OCR | PaddleOCR 3.x + PaddlePaddle, `pypdfium2` for PDF pages |
| LLM | AWS Bedrock — Claude 3.5 (extraction, matching context, reporting), Amazon Titan Embed Text v2 (embeddings) |
| Object storage | Amazon S3 (original receipt files) |
| Banking | Plaid (Sandbox) |
| Dev tools | ngrok (Plaid webhooks) |

---

## Repo layout

```
app.py                       FastAPI entrypoint — /extract, /receipts
backend/
  aws_clients.py             S3 upload/download
  plaid_client.py            Plaid SDK client config
  plaid_routes.py            Plaid Link, sync, accounts, unlink
  plaid_db.py                CockroachDB-backed Plaid data access
  matches_routes.py          /matches — review the agent's match candidates
  reports_routes.py          /reports/* — ledger, summary, AI report, CSV export
db/
  cockroach.py               CockroachDB connection pool
  db.py                      Schema (incl. VECTOR indexes) + receipt CRUD
  embeddings.py              Bedrock Titan embeddings helper
  matcher.py                 The matching agent (vector search + scoring)
  expenses.py                Deterministic ledger/report math
  reporting_agent.py         The audit-report LLM agent
extraction/
  ocr/                       PaddleOCR + PDF handling
  llm/                       Bedrock structured extraction
  schema/                    Pydantic Receipt/LineItem schema
frontend/                    index.html (dashboard), receipts.html,
                             reciept.html (detail), scan.html, reports.html
scripts/seed_mock_data.py    Seeds demo receipts + transactions + matches
```

---

## Setup

### Prerequisites
- Python 3.11 (PaddleOCR does not support 3.13+)
- Node.js (for the frontend static server)
- A CockroachDB Cloud cluster
- An AWS account with Bedrock model access enabled (Claude 3.5 Sonnet/Haiku, Titan Embed Text v2) and an S3 bucket
- A Plaid developer account (Sandbox)

### 1. Clone and set up the backend
```bash
git clone https://github.com/miavangellecum/ml-empowerment.git
cd ml-empowerment
python -m venv venv311
venv311\Scripts\activate        # Windows
pip install -r requirements.txt --break-system-packages
```

### 2. Environment variables (`.env` in project root)
```
# CockroachDB
COCKROACH_DATABASE_URL=postgresql://<user>:<password>@<host>:26257/<db>?sslmode=verify-full

# AWS
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
S3_BUCKET_NAME=...
BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0
BEDROCK_EMBEDDING_MODEL_ID=amazon.titan-embed-text-v2:0

# Plaid (Sandbox)
PLAID_CLIENT_ID=...
PLAID_SECRET=...
PLAID_ENV=sandbox
PLAID_WEBHOOK_URL=...           # ngrok URL, optional

```

One-time cluster setup (SQL shell):
```sql
SET CLUSTER SETTING feature.vector_index.enabled = true;
```

### 3. Run the backend
```bash
uvicorn app:app --reload
```
This calls `init_db()` on startup, which creates every table (receipts, line_items, plaid_items/accounts/transactions, receipt_transaction_matches) and both vector indexes if they don't already exist.

### 4. (Optional) seed demo data
```bash
python scripts/seed_mock_data.py
```
Idempotently seeds 5 mock receipts + 6 mock transactions and runs the matcher, so `/reports/summary` and `/matches` are populated without waiting on live OCR/Plaid timing.

### 5. Run the frontend
```bash
cd frontend
npm install
npm start
```
Serves the dashboard at `http://localhost:3000`, talking to the API at `http://localhost:8000`.

---

## Key API endpoints

| Endpoint | What it does |
|---|---|
| `POST /extract` | Upload a receipt image/PDF → OCR → Bedrock extraction → S3 + CockroachDB |
| `GET /receipts`, `GET /receipts/{id}` | List / fetch structured receipts |
| `POST /plaid/create_link_token`, `/plaid/exchange_public_token` | Plaid Link flow |
| `POST /plaid/sync/{item_id}` | Pull new transactions, auto-triggers matching |
| `GET /matches` | Every candidate match the agent has considered, with confidence + status |
| `GET /reports/ledger` | Unified receipt + bank-charge ledger |
| `GET /reports/summary` | Deterministic category totals, audit-readiness score, flags |
| `GET /reports/summary/ai` | The reporting agent's narrative audit report |
| `GET /reports/summary/csv` | Filing-ready CSV export |

---

## Design principles behind the agentic memory

- **Nothing is silently discarded.** Every match candidate, confident or not, gets a persisted row — the audit trail is the point.
- **The LLM never computes money.** Totals, percentages, and the audit-readiness score are always plain deterministic code; the model only classifies and narrates.
- **Files are not database blobs.** Receipt images live in S3; CockroachDB stores only the path — keeping the transactional store fast and the documentary evidence permanent and independently retrievable.
- **Embeddings live next to the data they describe**, not in a bolt-on vector store, so there's no consistency gap between "what the agent remembers" and "what actually happened."

---

## Roadmap

- CockroachDB Cloud Managed MCP Server integration for direct, read-only, audit-logged agent access to cluster state
- `ccloud` CLI integration for agent-driven cluster/backup management
- Plaid-to-receipt matching UI polish (confirm/reject from `/matches` in the frontend)
- PDF export of the audit report (currently Markdown/CSV)
- Multi-user auth (currently a single-user demo `DEMO_USER_ID`)

---

## License

MIT — see `LICENSE`.