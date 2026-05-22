from pymongo import MongoClient
import certifi

def sample_alliances(uri):
    client = MongoClient(uri, tlsCAFile=certifi.where())
    db = client['reminderbot']
    
    print("\n--- Alliance Member ID Sampling ---")
    pipeline = [
        {"$group": {"_id": "$alliance", "sample_name": {"$first": "$nickname"}, "count": {"$sum": 1}}}
    ]
    results = list(db['alliance_members'].aggregate(pipeline))
    for r in results:
        aid = r['_id']
        sample = db['alliance_members'].find_one({"alliance": aid})
        print(f" Alliance ID: {aid} | Count: {r['count']} | Sample Member: {sample.get('nickname')} | Alliance Tag in Doc: {sample.get('alliance')}")

    print("\n--- Alliance List Details ---")
    alliances = list(db['alliance__alliance_list'].find({}))
    for a in alliances:
        print(f" Name: {a.get('name')} | ID: {a.get('alliance_id') or a.get('id')} | Extra: {a.get('alliance_name')}")

URI_MAIN = "mongodb+srv://iammagnusx1_db_user:zYFHUOjjXhfGLpMs@reminder.hlx5aem.mongodb.net/?appName=REMINDER"
sample_alliances(URI_MAIN)
