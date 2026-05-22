
import sys
import os
from dotenv import load_dotenv
load_dotenv()

# Add root to sys.path
sys.path.insert(0, os.path.dirname(__file__))

try:
    from db.mongo_adapters import AutoRedeemMembersAdapter
    print(f"Testing AutoRedeemMembersAdapter with database: {os.getenv('MONGO_DB_NAME', 'reminderbot')}")
    
    # Test for a common guild_id or just try getting all (if adapter supports it)
    # The adapter uses find({'guild_id': int(guild_id)})
    
    # Let's find a valid guild_id from the database directly
    from pymongo import MongoClient
    uri = os.getenv('MONGO_URI')
    client = MongoClient(uri)
    db = client['reminderbot']
    docs = list(db['auto_redeem_members'].find({}).limit(10))
    print(f"Inspecting {len(docs)} documents...")
    for i, d in enumerate(docs):
        print(f"--- Document {i} ---")
        gid = d.get('guild_id')
        fid = d.get('fid')
        has_members = 'members' in d
        print(f"Guild ID: {gid}, FID: {fid}, Has 'members' array: {has_members}")
        if has_members:
            m_count = len(d['members'])
            print(f"  Members array size: {m_count}")
            if m_count > 0:
                print(f"  First member sample: {d['members'][0].get('fid')}")

    gid_with_grouped = 1147956569271697518
    gid_with_flat = 1394263768501846068
    
    for gid in [gid_with_grouped, gid_with_flat]:
        print(f"\n--- Testing Adapter for Guild: {gid} ---")
        members = AutoRedeemMembersAdapter.get_members(gid)
        print(f"Retrieved {len(members)} members")
        if members:
            print(f"Sample member FID: {members[0].get('fid')}")
            print(f"Sample member Nickname: {members[0].get('nickname')}")

except Exception as e:
    print(f"Verification failed: {e}")
    import traceback
    traceback.print_exc()
