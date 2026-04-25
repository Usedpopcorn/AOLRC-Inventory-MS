"""Validate required local configuration before starting the app.

Feature branches are expected to use local SQLite so they do not touch the
shared Postgres database. Main branch work may use either SQLite or the shared
Supabase/Postgres configuration. This script enforces that policy and performs
the relevant connection checks for the configured database type.
"""

import os
import subprocess
import sys

from dotenv import load_dotenv


def fail(msg: str) -> None:
    print(f"\n[FAIL] {msg}\n")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"[OK] {msg}")


def current_branch() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "branch", "--show-current"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None

    if completed.returncode != 0:
        return None
    branch_name = (completed.stdout or "").strip()
    if not branch_name or branch_name == "HEAD":
        return None
    return branch_name


def is_sqlite_url(value: str) -> bool:
    return value.strip().lower().startswith("sqlite:")


def validate_postgres_url(db_url: str) -> None:
    if "pooler.supabase.com" not in db_url:
        fail(
            "DATABASE_URL does not look like a Supabase POOLER URL (pooler.supabase.com). "
            "Use the SESSION pooler connection string from Supabase Connect."
        )

    if "db." in db_url and ".supabase.co" in db_url:
        fail(
            "DATABASE_URL appears to use the direct db.<ref>.supabase.co host. "
            "Docker may fail due to IPv6 routing. Use SESSION POOLER instead."
        )

    ok("DATABASE_URL looks like a Supabase pooler URL")

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
    except Exception as exc:
        fail(f"Could not connect to Postgres using DATABASE_URL.\nError: {exc}")


def main() -> None:
    load_dotenv(override=True)

    if not os.path.exists(".env"):
        fail("No .env file found. Copy .env.example -> .env and fill in DATABASE_URL + SECRET_KEY.")

    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        fail("DATABASE_URL is missing in .env")

    ok(".env exists")

    branch_name = current_branch()
    if branch_name and branch_name.lower() != "main" and not is_sqlite_url(db_url):
        fail(
            f"Current branch '{branch_name}' is configured with a non-SQLite DATABASE_URL. "
            "Feature branches must use local SQLite rather than the shared Postgres database."
        )

    if is_sqlite_url(db_url):
        ok("DATABASE_URL is configured for local SQLite")
        print("\nAll checks passed.\n")
        return

    validate_postgres_url(db_url)
    print("\nAll checks passed.\n")


if __name__ == "__main__":
    main()
