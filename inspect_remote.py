import os
import sys
import sqlite3
import json
from dotenv import load_dotenv

# Load env from bot directory if present
if os.path.exists("bot/.env"):
    load_dotenv("bot/.env")
else:
    load_dotenv(".env")

print("================ DIAGNOSTIC REPORT ================")
print("Working Directory:", os.getcwd())
print("MONGO_URI present:", bool(os.getenv("MONGO_URI")))
print("MONGO_DB_NAME:", os.getenv("MONGO_DB_NAME"))
print("MONGO_DB_REMINDERS:", os.getenv("MONGO_DB_REMINDERS"))

# 1. Inspect Local SQLite
db_path = "bot/reminders.db" if os.path.exists("bot/reminders.db") else "reminders.db"
print("Checking SQLite at:", db_path)
if os.path.exists(db_path):
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = c.fetchall()
        print("SQLite Tables:", tables)
        if any(t[0] == 'reminders' for t in tables):
            c.execute("SELECT count(*) FROM reminders")
            print("Total reminders in SQLite:", c.fetchone()[0])
            c.execute("SELECT id, message, thumbnail_url, image_url, is_active, is_sent FROM reminders WHERE is_active=1 AND is_sent=0")
            active = c.fetchall()
            print("Active reminders in SQLite (count):", len(active))
            for row in active:
                print(f"  ID: {row[0]} | Msg: {repr(row[1])} | Thumb: {row[2]} | Img: {row[3]} | Active: {row[4]} | Sent: {row[5]}")
        conn.close()
    except Exception as e:
        print("SQLite error:", e)
else:
    print("SQLite file does not exist")

# 2. Inspect MongoDB
if os.getenv("MONGO_URI"):
    try:
        from pymongo import MongoClient
        client = MongoClient(os.getenv("MONGO_URI"))
        db_name = os.getenv("MONGO_DB_REMINDERS") or os.getenv("MONGO_DB_NAME") or "reminderbot"
        print(f"Connecting to MongoDB database: {db_name}")
        db = client[db_name]
        col = db["reminders"]
        print("Total reminders in MongoDB:", col.count_documents({}))
        
        query = {
            "$and": [
                {"$or": [{"is_active": 1}, {"is_active": True}, {"is_active": "1"}, {"is_active": "true"}, {"is_active": "True"}, {"is_active": {"$exists": False}}]},
                {"$or": [{"is_sent": 0}, {"is_sent": False}, {"is_sent": "0"}, {"is_sent": "false"}, {"is_sent": "False"}, {"is_sent": {"$exists": False}}]},
            ]
        }
        active_docs = list(col.find(query))
        print("Active reminders in MongoDB (count):", len(active_docs))
        for d in active_docs:
            print(f"  ID: {d.get('_id')} | Msg: {repr(d.get('message'))} | Thumb: {d.get('thumbnail_url')} | Img: {d.get('image_url')} | Active: {d.get('is_active')} | Sent: {d.get('is_sent')}")
    except Exception as e:
        print("MongoDB error:", e)
else:
    print("MongoDB not configured in env")
print("===================================================")
