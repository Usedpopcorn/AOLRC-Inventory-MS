"""Validate required local configuration before starting the app.

This script verifies that `.env` exists, confirms `DATABASE_URL` is present and
uses a Supabase pooler host, and performs a real Postgres connectivity test
using `psycopg2`. It exits immediately with a `[FAIL]` message on the first
problem, or prints `[OK]` checks and a final success message when setup is
ready.
"""

import os
import sys

from dotenv import load_dotenv


def fail(msg: str) -> None:
    print(f"\n[FAIL] {msg}\n")
    sys.exit(1)

def ok(msg: str) -> None:
    print(f"[OK] {msg}")

def main() -> None:
    load_dotenv(override=True)

    if not os.path.exists(".env"):
        fail("No .env file found. Copy .env.example -> .env and fill in DATABASE_URL + SECRET_KEY.")

    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        fail("DATABASE_URL is missing in .env")

    if "pooler.supabase.com" not in db_url:
        fail("DATABASE_URL does not look like a Supabase POOLER URL (pooler.supabase.com). "
             "Use the SESSION pooler connection string from Supabase Connect.")

    if "db." in db_url and ".supabase.co" in db_url:
        fail("DATABASE_URL appears to use the direct db.<ref>.supabase.co host. "
             "Docker may fail due to IPv6 routing. Use SESSION POOLER instead.")

    ok(".env exists")
    ok("DATABASE_URL looks like a Supabase pooler URL")

    # DB connectivity test
    try:
        import psycopg2  # type: ignore
    except Exception:
        fail("psycopg2 not installed in container env? (This check should be run inside Docker.)")

    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("select current_database(), current_user;")
        row = cur.fetchone()
        cur.close()
        conn.close()
        ok(f"Connected to Postgres: db={row[0]}, user={row[1]}")
    except Exception as e:
        fail(f"Could not connect to Postgres using DATABASE_URL.\nError: {e}")

    print("\nAll checks passed ✅\n")

if __name__ == "__main__":
    main()
