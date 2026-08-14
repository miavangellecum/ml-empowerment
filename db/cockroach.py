"""
CockroachDB connection layer.

This replaces sqlite3 (db/db.py + backend/plaid_db.py both opened their own
"db/receipts.db" file) with a single pooled connection to a CockroachDB
cluster. Putting receipts and Plaid transactions in the same distributed
database is what lets db/matcher.py join and vector-search across both in
one query, instead of stitching two SQLite files together in app code.

Env vars (put these in your .env):

    COCKROACH_DATABASE_URL
        Connection string from CockroachDB Cloud Console -> your cluster ->
        Connect -> "Connection string". Looks like:
        postgresql://<user>:<password>@<host>:26257/<db>?sslmode=verify-full

Setup, once, from the SQL shell (`cockroach sql --url $COCKROACH_DATABASE_URL`
or via the Cloud Console SQL shell) if your cluster hasn't got the vector
index feature flag on:

    SET CLUSTER SETTING feature.vector_index.enabled = true;
"""
import os
from contextlib import contextmanager

import psycopg2
from psycopg2 import pool

DATABASE_URL = os.getenv("COCKROACH_DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "COCKROACH_DATABASE_URL is not set. Copy the connection string from "
        "the CockroachDB Cloud console (Cluster -> Connect) into your .env."
    )

# ThreadedConnectionPool because FastAPI's default worker model can service
# requests concurrently (even with a single uvicorn worker, async route
# handlers can interleave blocking DB calls across threads via run_in_threadpool).
_pool = psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=10, dsn=DATABASE_URL)


@contextmanager
def get_conn():
    """Yield a live connection; commits on success, rolls back on error,
    always returns the connection to the pool."""
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def close_pool():
    _pool.closeall()