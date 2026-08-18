# Quipe — an agentic tax-prep memory layer for receipts + bank transactions

Quipe is a small-business tax-season assistant. It ingests receipts (photos/PDFs) and live bank transactions over time, stores everything as persistent, queryable memory in CockroachDB, uses an LLM agent to reconcile the two, classify spend against IRS Schedule C categories, and generate an audit-ready expense report a business owner can hand to their accountant.

The core idea: an agent's memory isn't just a vector store bolted onto a chatbot. It's the receipts, the bank transactions, the confidence-scored links between them, and the audit trail of every decision the agent made — all living in one distributed, transactional database that the agent reads from and writes to over the entire lifetime of a business, not just one session.

---

## Real-world impact

Every small business owner or freelancer hits the same wall every April: a shoebox of receipts, a bank statement full of ambiguous line items ("SQ *THE GRILL HOUSE"), and no idea which of it is actually deductible. Quipe automates the unglamorous but high-stakes part of that process — matching, categorizing, and flagging — while keeping a human in the loop for anything the agent isn't confident about, and keeping every intermediate decision inspectable for an actual audit.

Expense categorization follows IRS Schedule C, Part II line items directly (Form 1040 Schedule C instructions, irs.gov), including the 50% meals deduction limitation — the categories in the extraction agent aren't invented, they map onto what a business owner would actually file under.

---

## Architecture


```

┌─────────────────┐      ┌──────────────────────┐      ┌───────────────────────┐
│   Frontend      │      │   FastAPI backend    │      │ CockroachDB           │
│  (vanilla JS/   │◄────►│  /extract  /plaid/*  │◄────►│  receipts             │
│   HTML/CSS,     │      │  /matches  /reports/* │      │  line_items           │
│   Node/Express) │      │  /agent/ask          │      │  plaid_items          │
└─────────────────┘      └──────────┬───────────┘      │  plaid_accounts       │
                                    │                  │  plaid_transactions   │
                ┌───────────────────┼──────────────────┤  receipt_transaction_ │
                │                   │                  │    matches            │
                ▼                   ▼                  │  (+ VECTOR indexes)   │
       ┌──────────────────┐  ┌──────────────────┐      └───────────────────────┘
       │  Plaid           │  │  AWS Bedrock     │
       │  bank sync       │  │  Claude Haiku    │
       └──────────────────┘  │  4.5 (EU Model)- │
                             │  extraction,     │
                             │  matching, the   │
                             │  reporting agent,│
                             │  and the query   │
                             │  agent           │
                             └────────┬─────────┘
                                      │
                        ┌─────────────┴───────────────┐
                        │  AWS S3 — original receipt  │
                        │  images/PDFs (permanent,    │
                        │  IRS documentary evidence)  │
                        └─────────────────────────────┘

      Receipt image/PDF → AWS Bedrock (Claude Haiku 4.5 vision, structured extraction directly from the image/PDF) → Pydantic schema validation → CockroachDB + S3
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
````

There is no separate vector store, no reindexing job, and no consistency gap — the embedding lives in the same row and the same transaction as the financial data it describes. Every candidate the agent considers (not just the winner) is persisted to `receipt_transaction_matches` with its cosine distance, amount-diff %, date-diff, and a blended confidence score, so a human reviewer can see exactly what the agent weighed and why (`/matches` endpoint). Matches above `AUTO_CONFIRM_THRESHOLD` (0.87) are auto-confirmed; everything else stays `pending` for human review — the agent never silently guesses on money.

### 2. CockroachDB as the single transactional source of truth

Receipts, line items, Plaid items/accounts/transactions, and the match graph between them all live in one distributed relational schema (`db/db.py`, `backend/plaid_db.py`), connected through a pooled `psycopg2` connection (`db/cockroach.py`). This is what lets `db/expenses.py` join receipts and bank charges in a single `UNION ALL` query to build a unified ledger, compute an audit-readiness score, and flag anomalies — all as plain deterministic SQL/Python, specifically so the LLM layer (`db/reporting_agent.py`, `db/query_agent.py`) is never asked to compute a dollar figure, only to classify and narrate. Every dollar in the final report traces back to a row Cockroach can produce on demand.

During development, this project also used the **CockroachDB Cloud Managed MCP Server** to give the coding agent direct, read-only access to live cluster/schema state (inspecting tables, indexes, and query plans against the real cluster instead of guessing from migration files) — see Prerequisites below.

## AWS integration
- **Amazon Bedrock** — the model layer for four distinct agent tasks:
  - **Extraction** (`extraction/llm/extract.py`): Sends the receipt image/PDF (first page, rendered to PNG via PyMuPDF when its a PDF) directly to Claude Haiku 4.5 (`eu.anthropic.claude-haiku-4-5-20251001-v1:0`) as a vision input, structuring it into typed Pydantic `Receipt` objects, classifying line items into IRS Schedule C tax categories, normalizing merchant names, and handling single-pass validation retries.
  - **Matching embeddings** (`db/embeddings.py`): Amazon Titan Text Embeddings V2 (`amazon.titan-embed-text-v2:0`) queried directly in `eu-central-1`.
  - **Reporting agent** (`db/reporting_agent.py`): given only pre-computed, already-correct numbers from `db/expenses.py`, classifies unreceipted bank charges into IRS categories (or flags `[NEEDS_REVIEW]`) and writes the three-section audit report — explicitly forbidden from recomputing any total or percentage itself. Also answers report follow-up questions (`/reports/summary/ai/ask`) grounded in that same computed data.
  - **Query agent** (`db/query_agent.py`, `backend/agent_routes.py`): a separate, constrained agent for ad-hoc questions ("how much did I spend on meals last quarter?") that answers only by calling fixed, pre-validated SQL tools (`db/query_tools.py`) — it never writes its own SQL, and every dollar figure in its answer is checked against the raw tool output before being returned.
- **Amazon S3** (`backend/aws_clients.py`) — every original receipt image/PDF is uploaded to S3 on ingestion and the `s3_url` is stored alongside the structured data in CockroachDB (`receipts.s3_url`). This is the permanent documentary evidence the IRS requires — binary files never touch the database itself, only their path does.

## What the agent actually does, end to end

1. **Ingest** — a receipt photo/PDF is uploaded (`/extract`). The file goes straight to Bedrock/Claude Haiku 4.5 as a vision input (PDFs are rendered to a PNG of the first page via PyMuPDF first, since Bedrocks image payload only accepts image media types), which returns a structured, IRS-categorized Pydantic `Receipt` schema; the original file goes to S3; the structured data + embedding goes to CockroachDB.
2. **Sync** — Plaid Sandbox transactions stream in (`backend/plaid_routes.py`), each embedded and upserted into `plaid_transactions`.
3. **Match** — every new receipt or transaction immediately triggers the matching agent (`db/matcher.py`), which vector-searches the other table and records every candidate it considered.
4. **Reconcile** — `/reports/summary` computes a unified ledger, category totals, deductible amounts (with the 50% meals limitation applied), an audit-readiness score, and flags (amount mismatches, missing receipts over $75, categories needing business-use verification) — all deterministically.
5. **Report** — `/reports/summary/ai` hands those exact numbers to the reporting agent, which classifies the remaining unreceipted charges and writes the audit-ready three-section Markdown report, downloadable as CSV or PDF, with an `/apply_classifications` endpoint to persist its category assignments back to the database and an `/ask` endpoint for follow-up questions.
6. **Ask** — `/agent/ask` answers one-off questions about spend, audit readiness, and pending reviews through the constrained query agent, independent of the full report flow.

## Tech stack

| **Layer**      | **Tech**                                                                                                 |
| -------------- | -------------------------------------------------------------------------------------------------------- |
| Backend        | FastAPI (Python 3.11), uvicorn                                                                           |
| Database       | CockroachDB (managed, vector-indexed)                                                                    |
| Frontend       | Vanilla JS/HTML/CSS, served via Node.js/Express                                                          |
| PDF handling   | PyMuPDF (`fitz`), first-page-to-PNG conversion for receipt PDFs                                          |
| LLM            | AWS Bedrock — Claude Haiku 4.5 (`eu.` Cross-Region Profile), Amazon Titan Embed Text v2 (`eu-central-1`) |
| Object storage | Amazon S3 (original receipt files)                                                                       |
| Banking        | Plaid (Sandbox)                                                                                          |
| Dev tools      | ngrok (Plaid webhooks), CockroachDB Cloud Managed MCP Server (dev-time cluster access)                  |

## Repo layout

```
app.py                       FastAPI entrypoint — /extract, /receipts
backend/
  aws_clients.py             S3 upload/download
  plaid_client.py            Plaid SDK client config
  plaid_routes.py            Plaid Link, sync, accounts, unlink
  plaid_db.py                CockroachDB-backed Plaid data access
  matches_routes.py          /matches — review the agent's match candidates
  reports_routes.py          /reports/* — ledger, summary, AI report, CSV/PDF export
  agent_routes.py            /agent/ask — the constrained query agent
db/
  cockroach.py               CockroachDB connection pool
  db.py                      Schema (incl. VECTOR indexes) + receipt CRUD
  embeddings.py              Bedrock Titan embeddings helper
  matcher.py                 The matching agent (vector search + scoring)
  expenses.py                Deterministic ledger/report math
  reporting_agent.py         The audit-report LLM agent
  query_agent.py             The ad-hoc question-answering agent
  query_tools.py             Fixed, pre-validated SQL tools the query agent may call
  tax_rules.py                Persistent tax-rule memory (deduction rates, etc.)
  pdf_report.py               Deterministic PDF export
extraction/
  ocr/                       Base64/media-type helpers for the vision payload
  llm/                       Bedrock structured vision extraction
  schema/                    Pydantic Receipt/LineItem schema
frontend/                    index.html (dashboard), receipts.html,
                             reciept.html (detail), scan.html, reports.html
scripts/seed_mock_data.py    Seeds demo receipts + transactions + matches

```

## Setup

### Prerequisites

- Python 3.11
- Node.js (for the frontend static server)
- A CockroachDB Cloud cluster
- An AWS account with Bedrock model access enabled in `eu-central-1` (Claude Haiku 4.5, Titan Embed Text v2) and an S3 bucket
- A Plaid developer account (Sandbox)
- (Dev-time only) The CockroachDB Cloud Managed MCP Server, connected to your cluster, if you want an agent/IDE to inspect live schema and cluster state the way this project's development flow did — not required to run the app itself

### 1. Clone and set up the backend

Bash

```
git clone [https://github.com/miavangellecum/ml-empowerment.git](https://github.com/miavangellecum/ml-empowerment.git)
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
AWS_REGION=eu-central-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
S3_BUCKET_NAME=receipt-storage-hackathon-925234975135-eu-central-1-an
BEDROCK_MODEL_ID=eu.anthropic.claude-haiku-4-5-20251001-v1:0
BEDROCK_EMBEDDING_MODEL_ID=amazon.titan-embed-text-v2:0

# Plaid (Sandbox)
PLAID_CLIENT_ID=...
PLAID_SECRET=...
PLAID_ENV=sandbox
PLAID_WEBHOOK_URL=...           # ngrok URL, optional
```

One-time cluster setup (SQL shell):

SQL

```
SET CLUSTER SETTING feature.vector_index.enabled = true;
```

### 3. Run the backend

Bash

```
uvicorn app:app --reload
```

This calls `init_db()` on startup, which creates every table (receipts, line\_items, plaid\_items/accounts/transactions, receipt\_transaction\_matches) and both vector indexes if they don't already exist.

### 4. (Optional) seed demo data

Bash

```
python scripts/seed_mock_data.py
```

Idempotently seeds 5 mock receipts + 6 mock transactions and runs the matcher, so `/reports/summary` and `/matches` are populated without waiting on live OCR/Plaid timing.

### 5. Run the frontend

Bash

```
cd frontend
npm install
npm start

```

Serves the dashboard at `http://localhost:3000`, talking to the API at `http://localhost:8000`.

## Key API endpoints

| **Endpoint**                                                    | **What it does**                                                         |
| --------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `POST /extract`                                                 | Upload a receipt image/PDF → Bedrock vision extraction → S3 + CockroachDB |
| `GET /receipts`, `GET /receipts/{id}`                           | List / fetch structured receipts                                         |
| `POST /plaid/create_link_token`, `/plaid/exchange_public_token` | Plaid Link flow                                                          |
| `POST /plaid/sync/{item_id}`                                    | Pull new transactions, auto-triggers matching                            |
| `GET /matches`                                                  | Every candidate match the agent has considered, with confidence + status |
| `GET /reports/ledger`                                           | Unified receipt + bank-charge ledger                                     |
| `GET /reports/summary`                                          | Deterministic category totals, audit-readiness score, flags              |
| `GET /reports/summary/ai`                                       | The reporting agent's narrative audit report                             |
| `POST /reports/summary/ai/apply_classifications`                | Persists the AI report's assigned categories back to `plaid_transactions` |
| `POST /reports/summary/ai/ask`                                  | Follow-up question about the generated AI report                        |
| `GET /reports/summary/csv`                                      | Filing-ready CSV export                                                  |
| `GET /reports/summary/pdf`                                      | Filing-ready PDF export (built from the deterministic numbers)          |
| `POST /agent/ask`                                                | Ask the constrained financial query agent a one-off question             |

## Design principles behind the agentic memory

- **Nothing is silently discarded.** Every match candidate, confident or not, gets a persisted row — the audit trail is the point.
- **The LLM never computes money.** Totals, percentages, and the audit-readiness score are always plain deterministic code; the model only classifies and narrates.
- **Files are not database blobs.** Receipt images live in S3; CockroachDB stores only the path — keeping the transactional store fast and the documentary evidence permanent and independently retrievable.
- **Embeddings live next to the data they describe**, not in a bolt-on vector store, so there's no consistency gap between "what the agent remembers" and "what actually happened."

## Roadmap

- Deeper CockroachDB Cloud Managed MCP Server integration for direct, read-only, audit-logged *agent* (not just dev-time) access to cluster state
- `ccloud` CLI integration for agent-driven cluster/backup management
- Plaid-to-receipt matching UI polish (confirm/reject from `/matches` in the frontend)
- Multi-user auth (currently a single-user demo `DEMO_USER_ID`)

## License

MIT — see `LICENSE`.