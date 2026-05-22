import os
from pymongo import MongoClient
import certifi

URI_MAIN = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"

client = MongoClient(URI_MAIN, tlsCAFile=certifi.where())

print("--- Members in wos_bot with alliance '6' or 6 ---")
c1 = client["wos_bot"]["alliance_members"].count_documents({"$or": [{"alliance": "6"}, {"alliance": 6}, {"alliance_id": "6"}, {"alliance_id": 6}]})
print(f"Count: {c1}")

print("\n--- Members in discord_bot with alliance '6' or 6 ---")
c2 = client["discord_bot"]["alliance_members"].count_documents({"$or": [{"alliance": "6"}, {"alliance": 6}, {"alliance_id": "6"}, {"alliance_id": 6}]})
print(f"Count: {c2}")

print("\n--- Total Members in wos_bot ---")
print(client["wos_bot"]["alliance_members"].count_documents({}))

print("\n--- Total Members in discord_bot ---")
print(client["discord_bot"]["alliance_members"].count_documents({}))
