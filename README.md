# ML Empowerment — Receipts & Transactions Matching

Pitch
-----
ML Empowerment is a receipts/transactions matching app that combines AWS Bedrock (Titan) embeddings with CockroachDB's SQL-native vector columns and indexes to power ultra-fast, server-side nearest-neighbor searches. It matches Plaid transactions to receipt/store names for reconciliation, insights, and intelligent reporting.

Highlights
----------
- Embeddings: AWS Bedrock (default model amazon.titan-embed-text-v2:0), 1024-d normalized vectors (db/embeddings.py).
- Storage & Search: CockroachDB VECTOR columns and VECTOR INDEX for pgvector-style literals and SQL-native nearest-neighbor queries (db/db.py, db/matcher.py).
- Matching: Embeds merchant/store text and stores vectors in CockroachDB; queries use the <=> operator for distance.
- Not included: No MCP/audit server integration detected and no ccloud/Confluent CLI provisioning scripts.

Architecture & Key Files
------------------------
- db/embeddings.py — boto3 Bedrock client usage, embed_text(), to_vector_literal() (pgvector-style strings).
- db/cockroach.py — CockroachDB connection layer; reads COCKROACH_DATABASE_URL from env.
- db/db.py — schema creation using VECTOR columns and CREATE VECTOR INDEX; requires CockroachDB v25.2+ and vector_index feature enabled.
- db/matcher.py — nearest-neighbor SQL queries using ::VECTOR casts and <=> distance operator.
- backend/plaid_db.py — syncs Plaid transactions and writes merchant_embedding vectors to DB.
- scripts/seed_mock_data.py — seed data helper for local/dev testing.
- frontend/reports.html & db/reporting_agent.py — sample Bedrock LLM usage for summaries/reports.

Requirements
------------
- Python 3.10+ (recommended)
- CockroachDB v25.2+ with vector index feature enabled (SET CLUSTER SETTING feature.vector_index.enabled = true;)
- AWS account with Bedrock access and valid AWS credentials
- Recommended Python deps (install via requirements.txt if present): boto3, psycopg2-binary (or compatible Postgres driver), flask (or the web framework used)

Environment Variables
---------------------
- COCKROACH_DATABASE_URL — CockroachDB connection string (from Cloud Console or self-hosted cluster).
- BEDROCK_EMBEDDING_MODEL_ID — (optional) default: amazon.titan-embed-text-v2:0
- BEDROCK_MODEL_ID — model used for LLM tasks (reporting/extraction)
- AWS_REGION — AWS region for Bedrock calls
- Any other env vars used by the app (check .env or code for additions)

Setup (developer)
------------------
1. Provision CockroachDB (Cloud Console or self-hosted). If using Cockroach Cloud, copy the connection string -> set COCKROACH_DATABASE_URL.
2. Ensure cluster has vector index feature enabled (CockroachDB v25.2+):
   SET CLUSTER SETTING feature.vector_index.enabled = true;
3. Ensure AWS Bedrock access and set AWS credentials + AWS_REGION.
4. Create a virtual env and install deps:
   python -m venv .venv
   .\.venv\Scripts\activate
   pip install -r requirements.txt
5. Run DB schema/init (the app creates schema via db/db.py on startup or use provided script).
6. Optionally seed mock data: python scripts\seed_mock_data.py
7. Start app (see project-specific run command; look at package.json or app startup script).

Usage
-----
- Ingest receipts or Plaid transactions; embeddings are computed via Bedrock and stored in CockroachDB VECTOR columns.
- Matching and reconciliation are performed via SQL nearest-neighbor queries (<=> operator) in db/matcher.py.
- Reports and summaries use Bedrock LLM calls (see frontend/reports.html and db/reporting_agent.py).

Security & Notes
----------------
- Do not commit AWS or Cockroach credentials. Use environment variables or secret managers.
- The code expects a CockroachDB Cloud Console-style URL but does not enforce a specific cloud tier.
- No MCP/audit server or Confluent Cloud provisioning scripts detected — provisioning is manual or via your infra tooling.

Want a short investor one-pager or a CONTRIBUTING.md next? If yes, specify which and any tone preferences.
