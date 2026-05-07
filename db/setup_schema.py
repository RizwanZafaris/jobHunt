"""
setup_schema.py — Initialize Supabase tables.

Run once to set up the database:
    python db/setup_schema.py

Prerequisites:
    - SUPABASE_URL and SUPABASE_SERVICE_KEY set in .env
    - pgvector extension enabled on your Supabase project
      (Dashboard → Database → Extensions → vector)
"""

import os
import sys
import pathlib
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("❌ SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
    sys.exit(1)


def run_sql_via_rest(sql: str) -> dict:
    """Execute raw SQL via Supabase REST API."""
    import httpx
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    # Use Supabase's /rest/v1/rpc or direct SQL endpoint
    url = f"{SUPABASE_URL}/rest/v1/rpc/exec_sql"
    resp = httpx.post(url, headers=headers, json={"query": sql}, timeout=30)
    return resp


def main():
    """Run the schema SQL against Supabase."""
    print("🚀 Setting up Job Hunt v2 Supabase schema...\n")

    schema_path = pathlib.Path(__file__).parent / "schema.sql"
    if not schema_path.exists():
        print(f"❌ Schema file not found: {schema_path}")
        sys.exit(1)

    schema_sql = schema_path.read_text()

    # Split into individual statements
    statements = [s.strip() for s in schema_sql.split(";") if s.strip()]

    print(f"   Found {len(statements)} SQL statements to execute")
    print("   Note: Some statements may fail if objects already exist (this is OK)\n")

    try:
        from supabase import create_client
        client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    except ImportError:
        print("❌ supabase-py not installed. Run: pip install supabase")
        sys.exit(1)

    # The Supabase Python client doesn't expose raw SQL execution directly.
    # Use the management API or psycopg2 for direct Postgres access.
    # Best practice: run via psql or Supabase Dashboard SQL editor.

    print("📋 INSTRUCTIONS:")
    print("=" * 60)
    print("Option 1 — Supabase Dashboard (easiest):")
    print("  1. Go to https://app.supabase.com/project/<your-project>/sql")
    print("  2. Click 'New Query'")
    print("  3. Paste the contents of db/schema.sql")
    print("  4. Click 'Run'\n")

    print("Option 2 — psql command line:")
    print("  psql <your-database-url> < db/schema.sql\n")

    print("Option 3 — Direct Postgres (get URL from Supabase Dashboard):")
    print("  Dashboard → Settings → Database → Connection string (URI)\n")

    print("Option 4 — Using psycopg2 (if DATABASE_URL is set):")
    db_url = os.getenv("DATABASE_URL", "")
    if db_url:
        run_with_psycopg2(schema_sql, db_url)
    else:
        print("  Set DATABASE_URL in .env to use this option")
        print("  DATABASE_URL=postgresql://postgres:<password>@<host>:5432/postgres\n")

    print("=" * 60)
    print("\nAfter running the schema, verify with:")
    print("  python db/setup_schema.py --verify")

    if "--verify" in sys.argv:
        verify_tables(client)


def run_with_psycopg2(schema_sql: str, db_url: str):
    """Run schema via psycopg2 if DATABASE_URL is available."""
    try:
        import psycopg2
    except ImportError:
        print("  psycopg2 not installed. Run: pip install psycopg2-binary")
        return

    print(f"\nUsing DATABASE_URL to run schema directly...")
    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(schema_sql)
        conn.close()
        print("✅ Schema applied successfully!\n")
    except Exception as e:
        print(f"❌ Error running schema: {e}")
        print("   Try running manually via Supabase Dashboard.\n")


def verify_tables(client):
    """Verify all expected tables exist."""
    print("\n🔍 Verifying table setup...")
    expected_tables = [
        "companies",
        "company_knowledge",
        "jobs",
        "applications",
        "rizwan_profile",
        "story_bank",
        "agent_conversations",
        "boss_audit_log",
    ]

    errors = []
    for table in expected_tables:
        try:
            result = client.table(table).select("*").limit(1).execute()
            print(f"  ✅ {table}")
        except Exception as e:
            print(f"  ❌ {table}: {e}")
            errors.append(table)

    if errors:
        print(f"\n⚠️  {len(errors)} tables missing. Run the schema SQL first.")
    else:
        print("\n✅ All tables verified! System ready.")
        print("\nNext step: python main.py --onboard")


if __name__ == "__main__":
    main()
