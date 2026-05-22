
import sys
import os
from dotenv import load_dotenv
load_dotenv()

bot_dir = os.path.join(os.getcwd(), 'DISCORD BOT')
sys.path.insert(0, bot_dir)

try:
    from db.mongo_adapters import AutoRedeemMembersAdapter
    
    gids = [1147956569271697518, 1394263768501846068]
    
    for gid in gids:
        members = AutoRedeemMembersAdapter.get_members(gid)
        print(f"\n--- GID: {gid} | Count: {len(members)} ---")
        if members:
            for idx, m in enumerate(members[:2]):
                f = m.get('fid', 'EMPTY')
                n = m.get('nickname', 'EMPTY')
                print(f" Member {idx+1}: FID={f}, Nickname={n}")
        else:
            print(" No members found.")

except Exception as e:
    print(f"Error: {e}")
