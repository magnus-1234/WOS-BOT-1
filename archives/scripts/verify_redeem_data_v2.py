
import sys
import os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

# Add DISCORD BOT to sys.path
bot_dir = os.path.join(os.getcwd(), 'DISCORD BOT')
sys.path.insert(0, bot_dir)

try:
    # This should now work without errors because of the shim and wrapper fixes
    from mongo_adapters import AutoRedeemMembersAdapter
    print(f"Successfully imported AutoRedeemMembersAdapter from DISCORD BOT shim")
    
    gid_with_grouped = 1147956569271697518
    gid_with_flat = 1394263768501846068
    
    for gid in [gid_with_grouped, gid_with_flat]:
        print(f"\n--- Testing DISCORD BOT Adapter for Guild: {gid} ---")
        members = AutoRedeemMembersAdapter.get_members(gid)
        print(f"Retrieved {len(members)} members")
        if members:
            m = members[0]
            print(f"Sample member FID: {m.get('fid')}")
            print(f"Sample member Nickname: {m.get('nickname')}")
            print(f"Sample member object: {m}")

except Exception as e:
    print(f"Verification failed: {e}")
    import traceback
    traceback.print_exc()
