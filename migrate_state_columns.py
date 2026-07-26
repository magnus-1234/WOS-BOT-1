#!/usr/bin/env python3
"""
One-time migration script to add state_id and state_transfer_suspected columns
to auto_redeem_members, and default_state to auto_redeem_settings.

Run this ONCE on the Oracle VM after pulling the latest code:
    python migrate_state_columns.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "db", "giftcode.sqlite")

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database not found at {DB_PATH}")
        return

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()

        # --- auto_redeem_members ---
        existing = {row[1] for row in c.execute("PRAGMA table_info(auto_redeem_members)").fetchall()}
        print(f"auto_redeem_members existing columns: {existing}")

        for col, typedef in [
            ("state_id", "TEXT DEFAULT '0'"),
            ("state_transfer_suspected", "INTEGER DEFAULT 0"),
        ]:
            if col not in existing:
                c.execute(f"ALTER TABLE auto_redeem_members ADD COLUMN {col} {typedef}")
                print(f"  ✅ Added column: {col}")
            else:
                print(f"  ℹ️  Column already exists: {col}")

        # --- auto_redeem_settings ---
        existing_settings = {row[1] for row in c.execute("PRAGMA table_info(auto_redeem_settings)").fetchall()}
        print(f"\nauto_redeem_settings existing columns: {existing_settings}")

        if "default_state" not in existing_settings:
            c.execute("ALTER TABLE auto_redeem_settings ADD COLUMN default_state TEXT DEFAULT '0'")
            print("  ✅ Added column: default_state")
        else:
            print("  ℹ️  Column already exists: default_state")

        conn.commit()
        print("\nMigration complete.")

if __name__ == "__main__":
    migrate()
