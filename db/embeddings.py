"""
Text embeddings for the matching agent.

We embed each receipt's store_name and each Plaid transaction's
merchant_name/name so db/matcher.py can vector-search "Trader Joe's #412"
against "TRADER JOE S 412 AMSTERDA" and find they're the same merchant even
though the strings don't match textually. Uses the same AWS Bedrock account
already configured for the extraction LLM (extraction/llm/extract.py).

Env vars:
    BEDROCK_EMBEDDING_MODEL_ID   default "amazon.titan-embed-text-v2:0"
    AWS_REGION                   already set for the rest of the app
"""
import json
import os

import boto3

EMBEDDING_MODEL_ID = os.getenv("BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
EMBEDDING_DIM = 1024  # Titan Text Embeddings V2 default output size

_bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION"))


def embed_text(text: str | None) -> list[float]:
    """Returns a normalized EMBEDDING_DIM-length vector for `text`.
    Empty/None input still returns a valid zero-ish vector so callers don't
    need special-casing before an INSERT."""
    text = (text or "").strip()
    if not text:
        text = "unknown"

    body = json.dumps({"inputText": text, "dimensions": EMBEDDING_DIM, "normalize": True})
    response = _bedrock.invoke_model(modelId=EMBEDDING_MODEL_ID, body=body)
    payload = json.loads(response["body"].read())
    return payload["embedding"]


def to_vector_literal(vector: list[float]) -> str:
    """CockroachDB VECTOR columns accept the pgvector-style text literal
    '[0.1,0.2,...]', which we then cast with ::VECTOR(n) in SQL."""
    return "[" + ",".join(f"{v:.8f}" for v in vector) + "]"