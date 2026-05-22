#!/usr/bin/env python3
"""List and clean stale music states from MongoDB."""
import os
import sys
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
uri = os.getenv("MONGO_URI") or os.getenv("MONGODB_URI")
if not uri:
    print("ERROR: MONGO_URI not found in .env")
    sys.exit(1)

client = MongoClient(uri)
db = client["reminderbot"]
states = list(db["music_states"].find({}, {"guild_id": 1, "guild_name": 1, "channel_name": 1, "channel_id": 1, "_id": 1}))

print(f"Found {len(states)} music state(s):")
for s in states:
    print(f"  Guild: {s.get('guild_name', '?')} ({s.get('guild_id', '?')}) | Channel: {s.get('channel_name', '?')} ({s.get('channel_id', '?')}) | _id: {s['_id']}")

if "--delete-all" in sys.argv:
    result = db["music_states"].delete_many({})
    print(f"\nDeleted {result.deleted_count} music state(s).")
elif "--delete-lounge" in sys.argv:
    result = db["music_states"].delete_many({"channel_name": "Lounge"})
    print(f"\nDeleted {result.deleted_count} 'Lounge' state(s).")
