import sqlite3
import os

def search_sqlite(db_path, search_term):
    print(f"\n--- Searching {os.path.basename(db_path)} for '{search_term}' ---")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [t[0] for t in cursor.fetchall()]
        
        for table in tables:
            try:
                # Get all columns
                cursor.execute(f"PRAGMA table_info({table})")
                cols = [c[1] for c in cursor.fetchall()]
                
                # Search each column
                for col in cols:
                    query = f"SELECT * FROM {table} WHERE \"{col}\" LIKE ?"
                    cursor.execute(query, (f"%{search_term}%",))
                    rows = cursor.fetchall()
                    if rows:
                        print(f" FOUND in {table}.{col}: {len(rows)} rows")
                        print(f" Sample: {rows[0]}")
            except Exception as e:
                pass
        conn.close()
    except Exception as e:
        print(f" Error: {e}")

db_dir = "/home/opc/app/bot/db"
dbs = [f for f in os.listdir(db_dir) if f.endswith(".sqlite")]

for db in dbs:
    search_sqlite(os.path.join(db_dir, db), "GTA")
    search_sqlite(os.path.join(db_dir, db), "3063")
