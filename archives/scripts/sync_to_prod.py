import os
import shutil

source_root = r'f:\Whiteout Survival Bot'
dest_root = r'f:\Whiteout Survival Bot\DISCORD BOT'

files_to_sync = [
    'app.py',
    'requirements.txt',
    'db_utils.py',
    'admin_utils.py',
    'api_manager.py',
    'bot_config.py',
    'ecosystem.config.js',
    'mongo_adapters.py',
    'db_migration_tool.py'
]

# Sync files
for file_name in files_to_sync:
    src_file = os.path.join(source_root, file_name)
    dest_file = os.path.join(dest_root, file_name)
    if os.path.exists(src_file):
        shutil.copy2(src_file, dest_file)
        print(f"Synced {file_name}")

# Sync cogs directory
src_cogs = os.path.join(source_root, 'cogs')
dest_cogs = os.path.join(dest_root, 'cogs')

if os.path.exists(src_cogs):
    for cog_file in os.listdir(src_cogs):
        if cog_file.endswith('.py'):
            shutil.copy2(os.path.join(src_cogs, cog_file), os.path.join(dest_cogs, cog_file))
    print("Synced cogs directory")
