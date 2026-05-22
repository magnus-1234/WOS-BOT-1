import os
from dotenv import load_dotenv

# Load env from the bot directory
load_dotenv('/home/opc/app/bot/.env')

from db.mongo_adapters import AlliancesAdapter, AllianceMembersAdapter

def verify_bot_data():
    print("--- Bot Adapter Verification ---")
    print(f"MONGO_DB_WOS: {os.getenv('MONGO_DB_WOS')}")
    print(f"MONGO_DB_NAME: {os.getenv('MONGO_DB_NAME')}")
    
    alliances = AlliancesAdapter.get_all()
    print(f"\nTotal alliances in list: {len(alliances)}")
    for a in alliances:
        name = a.get('name')
        aid = a.get('alliance_id')
        member_count = len(AllianceMembersAdapter.get_members_by_alliance_id(aid) if hasattr(AllianceMembersAdapter, 'get_members_by_alliance_id') else [])
        # Fallback if method name is different
        if member_count == 0:
             # Just count directly if needed
             from db.mongo_adapters import _get_db_wos
             db = _get_db_wos()
             member_count = db['alliance_members'].count_documents({'alliance': aid})
        
        print(f" - {name} (ID: {aid}): {member_count} members")

if __name__ == "__main__":
    try:
        verify_bot_data()
    except Exception as e:
        print(f"Error during verification: {e}")
