from pymongo import MongoClient
import certifi

def final_verify(uri):
    client = MongoClient(uri, tlsCAFile=certifi.where())
    db = client['reminderbot']
    
    print("\n--- Collection: alliance__alliance_list ---")
    for a in db['alliance__alliance_list'].find({}):
        print(f" Name: {a.get('name')} | ID: {a.get('id') or a.get('alliance_id')}")

    print("\n--- Collection: alliance__alliancesettings ---")
    for s in db['alliance__alliancesettings'].find({}):
        print(f" Alliance ID: {s.get('alliance_id')} | Channel: {s.get('channel_id')}")

    print("\n--- Collection: server_alliances ---")
    for d in db['server_alliances'].find({}):
        print(f" Guild: {d.get('_id')} | Alliance ID: {d.get('alliance_id') or d.get('alliances_id')}")

    print("\n--- GTA/3063 Member Check ---")
    # we know ID 13 is GTA, ID 8 is 3063
    gta_count = db['alliance_members'].count_documents({"alliance": 13})
    k3063_count = db['alliance_members'].count_documents({"alliance": 8})
    print(f" Alliance 13 (GTA) Members: {gta_count}")
    print(f" Alliance 8 (3063) Members: {k3063_count}")

URI_MAIN = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
final_verify(URI_MAIN)
