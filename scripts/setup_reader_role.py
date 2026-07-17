"""
One-time setup: create the read-only Postgres role that generated SQL
actually executes under 
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import psycopg
from app.config.settings import settings

def main() -> None:
    if not settings.reader_db_password:
        print("APP_READER_DB_PASSWORD is not set in .env — choose a password for the")
        print("reader role and add it there before running this script.")
        sys.exit(1)
    schemas = settings.schema_include
    print(f"Connecting as admin user '{settings.db_user}' to create/verify role "
          f"'{settings.reader_db_user}' ...")
    try:
        with psycopg.connect(settings.dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname = %s;", (settings.reader_db_user,)
                )
                if cur.fetchone():
                    print(f"Role '{settings.reader_db_user}' already exists, updating its "
                          f"password and grants only.")
                    cur.execute(
                        psycopg.sql.SQL("ALTER ROLE {} LOGIN PASSWORD {};").format(
                            psycopg.sql.Identifier(settings.reader_db_user),
                            psycopg.sql.Literal(settings.reader_db_password),
                        )
                    )
                else:
                    cur.execute(
                        psycopg.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {};").format(
                            psycopg.sql.Identifier(settings.reader_db_user),
                            psycopg.sql.Literal(settings.reader_db_password),
                        )
                    )
                    print(f"Created role '{settings.reader_db_user}'.")
                cur.execute(
                    psycopg.sql.SQL("GRANT CONNECT ON DATABASE {} TO {};").format(
                        psycopg.sql.Identifier(settings.db_name),
                        psycopg.sql.Identifier(settings.reader_db_user),
                    )
                )
                for schema in schemas:
                    cur.execute(
                        psycopg.sql.SQL("GRANT USAGE ON SCHEMA {} TO {};").format(
                            psycopg.sql.Identifier(schema),
                            psycopg.sql.Identifier(settings.reader_db_user),
                        )
                    )
                    cur.execute(
                        psycopg.sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {};").format(
                            psycopg.sql.Identifier(schema),
                            psycopg.sql.Identifier(settings.reader_db_user),
                        )
                    )
                    cur.execute(
                        psycopg.sql.SQL(
                            "ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT SELECT ON TABLES TO {};"
                        ).format(
                            psycopg.sql.Identifier(schema),
                            psycopg.sql.Identifier(settings.reader_db_user),
                        )
                    )
    except Exception as exc:  
        print("\n--- SETUP FAILED ---")
        print(f"{type(exc).__name__}: {exc}")
        print("\nCommon causes:")
        print("  - Admin credentials in .env are wrong")
        print("  - The admin user doesn't have permission to create roles")
        sys.exit(1)
    print(f"\nGranted SELECT on schemas {schemas} to '{settings.reader_db_user}'.")
    print("Verifying the role genuinely cannot write anything...")
    try:
        with psycopg.connect(settings.reader_dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                first_schema = schemas[0]
                cur.execute(
                    f"SELECT table_name FROM information_schema.tables "
                    f"WHERE table_schema = '{first_schema}' LIMIT 1;"
                )
                row = cur.fetchone()
                if row:
                    test_table = f"{first_schema}.{row[0]}"
                    try:
                        cur.execute(f'DELETE FROM {test_table};')
                        print(f"  WARNING: the reader role was able to run DELETE against "
                              f"{test_table}. Grants are not correctly restricted.")
                    except psycopg.errors.InsufficientPrivilege:
                        print(f"  Confirmed: DELETE against {test_table} was refused by Postgres itself.")
    except Exception as exc:  
        print(f"  Could not run the verification step: {exc}")
    print("\nDone. This role's DSN is used by app/services/query_executor.py for every "
          "generated query — it is never given the admin credentials.")
    
if __name__ == "__main__":
    main()