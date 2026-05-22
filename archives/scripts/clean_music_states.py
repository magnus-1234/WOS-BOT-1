#!/usr/bin/env python3
"""Clean up stale music states from MongoDB by guild_id."""
from pymongo import MongoClient
import sys

MONGO_URI = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"

client = MongoClient(MONGO_URI)
db = client["reminderbot"]

# List all states first
states = list(db["music_states"].find())
print(f"Found {len(states)} music state(s):")
for s in states:
    print(f"  _id={s['_id']} guild_id={s.get('guild_id')} channel_id={s.get('channel_id')}")

# Delete states for "test" guild (ID: 1147956569271697518) — keeps timing out
test_guild_id = 1147956569271697518
result = db["music_states"].delete_many({"guild_id": test_guild_id})
print(f"\nDeleted {result.deleted_count} stale state(s) for guild {test_guild_id}")

# Verify
remaining = db["music_states"].count_documents({})
print(f"Remaining music states: {remaining}")
