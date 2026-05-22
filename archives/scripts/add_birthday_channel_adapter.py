"""
Script to add BirthdayChannelAdapter to db/mongo_adapters.py
Run this script to add the birthday channel adapter implementation.
"""

import os
from pathlib import Path

# Define the adapter code to add
ADAPTER_CODE = '''
class BirthdayChannelAdapter:
    """Adapter for per-guild birthday channel configuration"""
    COLL = 'birthday_channels'
    
    @staticmethod
    def get(guild_id: int) -> Optional[int]:
        """Get birthday channel ID for a guild"""
        try:
            db = _get_db()
            doc = db[BirthdayChannelAdapter.COLL].find_one({'_id': int(guild_id)})
            if doc:
                return int(doc.get('channel_id'))
            return None
        except Exception as e:
            logger.error(f'Failed to get birthday channel for guild {guild_id}: {e}')
            return None
    
    @staticmethod
    def set(guild_id: int, channel_id: int) -> bool:
        """Set birthday channel ID for a guild"""
        try:
            db = _get_db()
            db[BirthdayChannelAdapter.COLL].update_one(
                {'_id': int(guild_id)},
                {'$set': {
                    'channel_id': int(channel_id),
                    'updated_at': datetime.utcnow().isoformat()
                }},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f'Failed to set birthday channel for guild {guild_id}: {e}')
            return False
    
    @staticmethod
    def remove(guild_id: int) -> bool:
        """Remove birthday channel configuration for a guild"""
        try:
            db = _get_db()
            res = db[BirthdayChannelAdapter.COLL].delete_one({'_id': int(guild_id)})
            return res.deleted_count > 0
        except Exception as e:
            logger.error(f'Failed to remove birthday channel for guild {guild_id}: {e}')
            return False
'''

def add_adapter_to_file():
    """Add BirthdayChannelAdapter to db/mongo_adapters.py"""
    
    file_path = Path('db/mongo_adapters.py')
    
    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        print("Make sure you're running this from the DISCORD BOT directory")
        return False
    
    # Read the file
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check if adapter already exists
    if 'class BirthdayChannelAdapter' in content:
        print("✅ BirthdayChannelAdapter already exists in the file!")
        return True
    
    # Find where to insert (after BirthdaysAdapter class)
    insert_marker = 'class BirthdaysAdapter:'
    
    if insert_marker not in content:
        print(f"❌ Could not find '{insert_marker}' in the file")
        return False
    
    # Find the end of BirthdaysAdapter class
    # Look for the next class definition or end of file
    lines = content.split('\n')
    insert_line = -1
    in_birthdays_adapter = False
    
    for i, line in enumerate(lines):
        if 'class BirthdaysAdapter:' in line:
            in_birthdays_adapter = True
        elif in_birthdays_adapter and line.startswith('class ') and 'BirthdaysAdapter' not in line:
            # Found the next class, insert before it
            insert_line = i
            break
    
    if insert_line == -1:
        print("❌ Could not find insertion point")
        return False
    
    # Insert the adapter code
    lines.insert(insert_line, ADAPTER_CODE)
    
    # Write back to file
    new_content = '\n'.join(lines)
    
    # Create backup
    backup_path = file_path.with_suffix('.py.backup')
    with open(backup_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"📦 Backup created: {backup_path}")
    
    # Write new content
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f"✅ Successfully added BirthdayChannelAdapter to {file_path}")
    print("🔄 Please restart the bot for changes to take effect")
    return True

if __name__ == '__main__':
    print("Adding BirthdayChannelAdapter to db/mongo_adapters.py...")
    print()
    
    success = add_adapter_to_file()
    
    if success:
        print()
        print("Next steps:")
        print("1. Restart the bot")
        print("2. Go to /start → Birthday → ⚙️ Set Channel")
        print("3. Select your birthday wish channel")
    else:
        print()
        print("❌ Failed to add adapter. Please add it manually.")
        print("See the walkthrough.md for the code to add.")
