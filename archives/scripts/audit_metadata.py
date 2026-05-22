from pymongo import MongoClient
import certifi

def audit_full_metadata(uri):
    client = MongoClient(uri, tlsCAFile=certifi.where())
    db = client['reminderbot']
    
    print("\n--- Collection: alliance__alliance_list ---")
    list_data = list(db['alliance__alliance_list'].find({}))
    for d in list_data:
        print(f" ID: {d.get('id') or d.get('alliance_id')} | Name: {d.get('name')}")

    print("\n--- Collection: server_alliances ---")
    server_data = list(db['server_alliances'].find({}))
    for d in server_data:
        print(f" Alliance ID: {d.get('alliance_id')} | Alliance Name: {d.get('alliance_name')} | Guild: {d.get('guild_id')}")

    print("\n--- Collection: alliance_settings ---")
    settings_data = list(db['alliance_settings'].find({}))
    for d in settings_data:
        print(f" Alliance ID: {d.get('alliance_id')} | Name: {d.get('name')}")

    print("\n--- Collection: alliances ---")
    alliances_data = list(db['alliances'].find({}))
    for d in alliances_data:
        print(f" ID: {d.get('_id')} | Name: {d.get('name')}")

URI_MAIN = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
audit_full_metadata(URI_MAIN)
