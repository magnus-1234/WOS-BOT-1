try:
    from pymongo import MongoClient
    import certifi
    import sys
    
    URI_MAIN = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
    client = MongoClient(URI_MAIN, tlsCAFile=certifi.where())
    db = client['reminderbot']
    coll = db['alliance__alliance_list']
    
    print("--- SUCCESS: Connected and starting dump ---")
    docs = list(coll.find({}))
    for doc in docs:
        print(doc)
    print("--- SUCCESS: Dump complete ---")

except Exception as e:
    import traceback
    print(f"--- FAILURE: {e} ---")
    traceback.print_exc()
    sys.exit(1)
