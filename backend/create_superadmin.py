"""CLI tool to create or promote a user to SuperAdmin.

Usage:
    cd backend
    .\\.venv\\Scripts\\python.exe create_superadmin.py --email user@example.com --password MyPassword123! --name "My Name"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from sqlalchemy import text

from app.core.config import get_settings
from app.db.engine import create_engine
from app.modules.auth.security import hash_password_async


async def make_superadmin(email: str, password: str, name: str, org_name: str = "Sunrise Coaching Centre") -> None:
    settings = get_settings()
    engine = create_engine(settings)
    
    clean_email = email.strip().lower()
    print(f"[*] Hashing password with Argon2id...")
    password_hash = await hash_password_async(password, settings)
    
    async with engine.begin() as conn:
        # 1. Check or insert organization
        org_row = await conn.execute(
            text("SELECT id, name FROM organizations ORDER BY created_at ASC LIMIT 1")
        )
        existing_org = org_row.fetchone()
        
        if existing_org:
            org_id = existing_org[0]
            print(f"[+] Using existing organization: {existing_org[1]} (id: {org_id})")
        else:
            org_id = uuid.uuid4()
            await conn.execute(
                text("""
                    INSERT INTO organizations (id, name, org_type, region, status)
                    VALUES (:id, :name, 'coaching_center', 'ap-south-1', 'active')
                """),
                {"id": org_id, "name": org_name},
            )
            print(f"[+] Created organization: {org_name} (id: {org_id})")

        # 2. Check if user already exists
        user_row = await conn.execute(
            text("SELECT id, email, full_name FROM users WHERE lower(email) = :email"),
            {"email": clean_email},
        )
        existing_user = user_row.fetchone()

        if existing_user:
            user_id = existing_user[0]
            await conn.execute(
                text("""
                    UPDATE users 
                    SET password_hash = :hash, full_name = :name, status = 'active'
                    WHERE id = :id
                """),
                {"id": user_id, "hash": password_hash, "name": name},
            )
            print(f"[+] Updated existing user: {clean_email} (id: {user_id})")
        else:
            user_id = uuid.uuid4()
            await conn.execute(
                text("""
                    INSERT INTO users (id, email, password_hash, full_name, status)
                    VALUES (:id, :email, :hash, :name, 'active')
                """),
                {"id": user_id, "email": clean_email, "hash": password_hash, "name": name},
            )
            print(f"[+] Created new user: {clean_email} (id: {user_id})")

        # 3. Create or promote membership to super_admin
        mem_row = await conn.execute(
            text("SELECT id, role FROM memberships WHERE user_id = :user_id AND org_id = :org_id"),
            {"user_id": user_id, "org_id": org_id},
        )
        existing_mem = mem_row.fetchone()

        if existing_mem:
            await conn.execute(
                text("""
                    UPDATE memberships
                    SET role = 'super_admin', status = 'active'
                    WHERE id = :id
                """),
                {"id": existing_mem[0]},
            )
            print(f"[+] Promoted existing membership {existing_mem[0]} to 'super_admin'!")
        else:
            mem_id = uuid.uuid4()
            await conn.execute(
                text("""
                    INSERT INTO memberships (id, user_id, org_id, role, status)
                    VALUES (:id, :user_id, :org_id, 'super_admin', 'active')
                """),
                {"id": mem_id, "user_id": user_id, "org_id": org_id},
            )
            print(f"[+] Granted 'super_admin' membership (id: {mem_id})!")

    print("\n" + "=" * 60)
    print(f" SUCCESS: {clean_email} is now a SUPERADMIN!")
    print(f" Organization: {org_name}")
    print(f" Login at:     http://localhost:3000/login")
    print(f" Email:        {clean_email}")
    print(f" Password:     {password}")
    print("=" * 60 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or promote a user to SuperAdmin.")
    parser.add_argument("--email", required=True, help="User email address")
    parser.add_argument("--password", required=True, help="User password (min 8 chars)")
    parser.add_argument("--name", default="Super Admin", help="Full name of user")
    parser.add_argument("--org", default="Sunrise Coaching Centre", help="Organization name")

    args = parser.parse_args()

    if len(args.password) < 8:
        print("[!] Error: Password must be at least 8 characters.")
        sys.exit(1)

    asyncio.run(make_superadmin(args.email, args.password, args.name, args.org))


if __name__ == "__main__":
    main()
