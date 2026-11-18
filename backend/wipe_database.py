"""Script to wipe all dummy and evidence data from the database.

Preserves the schema and Alembic migrations, and ensures the SuperAdmin account
(rounakpanigrahi902@gmail.com) is recreated clean and ready.

Usage:
    cd backend
    .\\.venv\\Scripts\\python.exe wipe_database.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from sqlalchemy import text

from app.core.config import get_settings
from app.db.engine import create_engine
from app.modules.auth.security import hash_password_async

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


async def wipe_data(superadmin_email: str = "rounakpanigrahi902@gmail.com", superadmin_pwd: str = "Papergen@2026") -> None:
    settings = get_settings()
    engine = create_engine(settings)

    print("[*] Connecting to database...")
    async with engine.begin() as conn:
        # 1. Get all table names in public schema, excluding alembic_version
        tables_res = await conn.execute(
            text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                  AND table_type = 'BASE TABLE'
                  AND table_name != 'alembic_version'
                ORDER BY table_name;
            """)
        )
        tables = [row[0] for row in tables_res.fetchall()]

        if not tables:
            print("[!] No tables found to wipe.")
            return

        print(f"[*] Found {len(tables)} tables to wipe: {', '.join(tables)}")

        # 2. Truncate all tables in one CASCADE statement
        tables_str = ", ".join(f'"{t}"' for t in tables)
        truncate_sql = f"TRUNCATE TABLE {tables_str} CASCADE;"
        
        print("[*] Executing TRUNCATE ... CASCADE...")
        await conn.execute(text(truncate_sql))
        print("[+] All dummy and evidence data successfully wiped!")

        # 3. Re-create the SuperAdmin account cleanly
        clean_email = superadmin_email.strip().lower()
        pwd_hash = await hash_password_async(superadmin_pwd, settings)
        user_id = uuid.uuid4()
        org_id = uuid.uuid4()
        mem_id = uuid.uuid4()

        await conn.execute(
            text("""
                INSERT INTO organizations (id, name, org_type, region, status)
                VALUES (:id, 'PaperGen Global', 'coaching_center', 'ap-south-1', 'active');
            """),
            {"id": org_id},
        )

        await conn.execute(
            text("""
                INSERT INTO users (id, email, password_hash, full_name, status)
                VALUES (:id, :email, :hash, 'Rounak Panigrahi', 'active');
            """),
            {"id": user_id, "email": clean_email, "hash": pwd_hash},
        )

        await conn.execute(
            text("""
                INSERT INTO memberships (id, user_id, org_id, role, status)
                VALUES (:id, :user_id, :org_id, 'super_admin', 'active');
            """),
            {"id": mem_id, "user_id": user_id, "org_id": org_id},
        )
        print(f"[+] Re-created clean SuperAdmin account: {clean_email} in 'PaperGen Global'")

    print("\n" + "=" * 60)
    print(" DATABASE IS NOW COMPLETELY CLEAN & FRESH!")
    print(" Ready for fresh data seeding.")
    print(f" SuperAdmin:   {superadmin_email} (Password: {superadmin_pwd})")
    print(f" Organization: PaperGen Global (ID: {org_id})")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(wipe_data())
