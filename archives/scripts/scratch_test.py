import asyncio
import os
import sys

# Add the current directory to sys.path
sys.path.insert(0, r"f:\Whiteout Survival Bot")

from gift_codes import get_active_gift_codes

async def main():
    codes = await get_active_gift_codes()
    print("Codes returned:")
    for c in codes:
        print(f"Code: {c.get('code')} | Rewards: {c.get('rewards')} | Expiry: {c.get('expiry')}")

if __name__ == '__main__':
    asyncio.run(main())
