"""CLI script to seed the PaperGen database from a structured JSON file.

Usage:
    cd backend
    .\\.venv\\Scripts\\python.exe seed_from_json.py --file ..\\complete_seed_example.json
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

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

# Dependency-safe order for insertion
TABLE_ORDER = [
    "boards",
    "classes",
    "curriculum_versions",
    "subjects",
    "chapters",
    "learning_outcomes",
    "users",
    "organizations",
    "branches",
    "memberships",
    "programs",
    "program_subjects",
    "program_teachers",
    "subscriptions",
    "prompt_versions",
    "textbooks",
    "textbook_assets",
    "templates",
    "question_bank",
    "generated_papers",
    "paper_versions",
    "approvals",
    "usage",
]

UUID_ARRAY_FIELDS = {"learning_outcome_ids", "competency_ids"}
DATE_FIELDS = {"effective_from", "period_start", "period_end", "due_date"}


def parse_field_value(col_name: str, value: Any) -> Any:
    if value is None:
        return None
    if col_name in UUID_ARRAY_FIELDS and isinstance(value, list):
        return [uuid.UUID(str(x)) for x in value]
    if col_name in DATE_FIELDS and isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            pass
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


async def seed_from_json(json_path: str, superadmin_email: str | None = None) -> None:
    path = Path(json_path)
    if not path.is_absolute():
        path = Path.cwd() / path

    if not path.exists():
        print(f"[!] Error: Seed file not found at {path}")
        sys.exit(1)

    print(f"[*] Reading seed data from {path.name}...")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    settings = get_settings()
    engine = create_engine(settings)

    # Valid fallback hash for placeholder password hashes
    valid_dev_hash = await hash_password_async("Papergen123!", settings)

    print("[*] Connecting to PostgreSQL...")
    id_map: dict[str, str] = {}

    # Tables that need per-row dedup checks before inserting
    DEDUP_TABLES = {"boards", "organizations"}
    # Batch size for executemany inserts
    BATCH_SIZE = 500

    async with engine.connect() as conn:
        for table_name in TABLE_ORDER:
            records = data.get(table_name)
            if not records or not isinstance(records, list):
                continue

            t0 = datetime.datetime.now()
            print(f"--> Seeding table: '{table_name}' ({len(records)} records)")
            success_count = 0
            skip_count = 0

            # --- Phase 1: Pre-process all rows (dedup checks, FK remaps, type coercion) ---
            # Rows that need dedup lookups are handled inside a single read transaction.
            rows_to_insert: list[dict[str, Any]] = []
            sql_template: str | None = None

            if table_name in DEDUP_TABLES:
                # Dedup tables: run SELECT checks in a single read pass, then batch-insert survivors
                trans = await conn.begin()
                try:
                    for record in records:
                        clean_row: dict[str, Any] = {
                            k: v for k, v in record.items() if not k.startswith("_")
                        }
                        for k, v in clean_row.items():
                            if isinstance(v, str) and v in id_map:
                                clean_row[k] = id_map[v]

                        if table_name == "boards" and "code" in clean_row:
                            exist_res = await conn.execute(
                                text("SELECT id FROM boards WHERE lower(code) = lower(:code) AND deleted_at IS NULL"),
                                {"code": clean_row["code"]},
                            )
                            existing_board_id = exist_res.scalar()
                            if existing_board_id:
                                if "id" in clean_row:
                                    id_map[str(clean_row["id"])] = str(existing_board_id)
                                success_count += 1
                                continue

                        if table_name == "organizations" and "name" in clean_row:
                            exist_res = await conn.execute(
                                text("SELECT id FROM organizations WHERE lower(name) = lower(:name) AND deleted_at IS NULL"),
                                {"name": clean_row["name"]},
                            )
                            existing_org_id = exist_res.scalar()
                            if existing_org_id:
                                if "id" in clean_row:
                                    id_map[str(clean_row["id"])] = str(existing_org_id)
                                success_count += 1
                                continue

                        processed_row = {k: parse_field_value(k, v) for k, v in clean_row.items()}
                        rows_to_insert.append(processed_row)
                    await trans.commit()
                except Exception:
                    await trans.rollback()
                    raise
            else:
                # Non-dedup tables: prepare rows without any DB round trips
                for record in records:
                    clean_row = {
                        k: v for k, v in record.items() if not k.startswith("_")
                    }
                    for k, v in clean_row.items():
                        if isinstance(v, str) and v in id_map:
                            clean_row[k] = id_map[v]

                    if table_name == "users" and "password_hash" in clean_row:
                        if "hashhash" in str(clean_row["password_hash"]):
                            clean_row["password_hash"] = valid_dev_hash

                    processed_row = {k: parse_field_value(k, v) for k, v in clean_row.items()}
                    rows_to_insert.append(processed_row)

            # --- Phase 2: Batch INSERT all prepared rows ---
            if rows_to_insert:
                # All rows in a table should have the same columns (from the JSON schema)
                # but we union all keys to be safe
                all_columns = list(dict.fromkeys(
                    col for row in rows_to_insert for col in row.keys()
                ))

                # Normalize: ensure every row has every column (None for missing)
                for row in rows_to_insert:
                    for col in all_columns:
                        row.setdefault(col, None)

                placeholders = [f":{col}" for col in all_columns]
                col_list = ", ".join(all_columns)
                val_list = ", ".join(placeholders)

                if "id" in all_columns:
                    sql_template = f"INSERT INTO {table_name} ({col_list}) VALUES ({val_list}) ON CONFLICT (id) DO NOTHING"
                elif table_name == "program_subjects":
                    sql_template = f"INSERT INTO program_subjects ({col_list}) VALUES ({val_list}) ON CONFLICT (program_id, subject_id) DO NOTHING"
                elif table_name == "program_teachers":
                    sql_template = f"INSERT INTO program_teachers ({col_list}) VALUES ({val_list}) ON CONFLICT (program_id, user_id) DO NOTHING"
                else:
                    sql_template = f"INSERT INTO {table_name} ({col_list}) VALUES ({val_list})"

                # Insert in batches within a single transaction per table
                for batch_start in range(0, len(rows_to_insert), BATCH_SIZE):
                    batch = rows_to_insert[batch_start : batch_start + BATCH_SIZE]
                    trans = await conn.begin()
                    try:
                        await conn.execute(text(sql_template), batch)
                        await trans.commit()
                        success_count += len(batch)
                    except Exception as exc:
                        await trans.rollback()
                        # Fallback: insert one-by-one to identify bad rows
                        for row in batch:
                            try:
                                t2 = await conn.begin()
                                await conn.execute(text(sql_template), row)
                                await t2.commit()
                                success_count += 1
                            except Exception as row_exc:
                                await t2.rollback()
                                skip_count += 1
                                err_msg = str(row_exc).split("\n")[0]
                                print(f"    [WARN] Skip row {row.get('id', 'N/A')}: {err_msg}")

            elapsed = (datetime.datetime.now() - t0).total_seconds()
            print(f"    Inserted/Verified: {success_count}/{len(records)} ({elapsed:.1f}s)")
            if skip_count:
                print(f"    Skipped: {skip_count}")

        # Post-insert updates (resolving circular foreign keys)
        post_updates = data.get("_post_insert_update__organizations")
        if post_updates and isinstance(post_updates, list):
            print("\n--> Applying post-insert foreign key updates (organizations.subscription_id)...")
            trans = await conn.begin()
            try:
                for upd in post_updates:
                    org_id = upd.get("id")
                    sub_id = upd.get("subscription_id")
                    if org_id and sub_id:
                        await conn.execute(
                            text("UPDATE organizations SET subscription_id = :sub_id WHERE id = :org_id"),
                            {"sub_id": sub_id, "org_id": org_id},
                        )
                await trans.commit()
                print("    Successfully updated organization subscriptions!")
            except Exception as e:
                await trans.rollback()
                print(f"    [WARN] Post-update failed: {e}")

        # Grant superadmin membership in newly seeded organizations
        if superadmin_email:
            trans = await conn.begin()
            try:
                user_res = await conn.execute(
                    text("SELECT id FROM users WHERE lower(email) = :email"),
                    {"email": superadmin_email.strip().lower()},
                )
                super_user_id = user_res.scalar()
                if super_user_id:
                    for org in data.get("organizations", []):
                        org_id = org.get("id")
                        if org_id:
                            await conn.execute(
                                text("""
                                    INSERT INTO memberships (id, user_id, org_id, role, status)
                                    VALUES (:id, :user_id, :org_id, 'super_admin', 'active')
                                    ON CONFLICT (id) DO NOTHING
                                """),
                                {"id": uuid.uuid4(), "user_id": super_user_id, "org_id": org_id},
                            )
                    print(f"\n[+] Linked SuperAdmin ({superadmin_email}) to all seeded organizations!")
                await trans.commit()
            except Exception as e:
                await trans.rollback()
                print(f"    [WARN] SuperAdmin linking: {e}")

    print("\n" + "=" * 60)
    print(" ALL SEED DATA SUCCESSFULLY INSERTED INTO DATABASE!")
    print("=" * 60 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed PaperGen database from JSON.")
    parser.add_argument(
        "--file",
        default="../complete_seed_example.json",
        help="Path to the seed JSON file",
    )
    parser.add_argument(
        "--superadmin",
        default="rounakpanigrahi902@gmail.com",
        help="SuperAdmin email to grant membership in seeded orgs",
    )

    args = parser.parse_args()
    asyncio.run(seed_from_json(args.file, args.superadmin))


if __name__ == "__main__":
    main()
