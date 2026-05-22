import os
from pymongo import MongoClient
from dotenv import load_dotenv

def inspect_cluster(uri, label):
    if not uri:
        print(f"\n--- {label} URI not found ---")
        return
        
    print(f"\n--- Inspecting {label} Cluster ---")
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        db_name = os.getenv('MONGO_DB_WOS', 'reminderbot')
        db = client[db_name]
        
        count = db.alliance_members.count_documents({})
        print(f"Total members in alliance_members: {count}")
        
        distinct_alliances = db.alliance_members.distinct("alliance")
        print(f"Distinct values in 'alliance' field: {distinct_alliances}")
        
        distinct_alliance_ids = db.alliance_members.distinct("alliance_id")
        print(f"Distinct values in 'alliance_id' field: {distinct_alliance_ids}")
        
        client.close()
    except Exception as e:
        print(f"Error inspecting {label} cluster: {e}")

def main():
    load_dotenv()
    inspect_cluster(os.getenv('MONGO_URI'), "Primary")
    inspect_cluster(os.getenv('MONGO_URI_FALLBACK'), "Fallback")

if __name__ == "__main__":
    main()
