"""
Comprehensive fix for the "Please use /settings -> Bot Operations -> Assign Server Alliance to assign one" bug.

ROOT CAUSES IDENTIFIED:
1. PendingConfigAdapter.approve_async() writes alliance_name as a string to server_alliances
   but NEVER writes alliances_id (the numeric foreign key). So get_alliance() always returns None
   for servers approved through this code path if the downstream SQLite lookup fails.

2. The downstream SQLite lookup in _do_approve (registration_admin.py, registration.py)
   uses `if alliance_id:` which fails if alliance_id=0 (the initial default).
   If SQLite fails, alliances_id is never set.

3. The old get_alliance() methods did int(None) -> TypeError -> caught -> returned None.
   (Already fixed in previous step.)

FIXES:
A. Add alliance_id parameter to approve_async() so callers can pass it directly.
   If provided, write alliances_id to server_alliances as part of the atomic approval.
   
B. Add a `get_or_create_alliance_id_async()` helper in AlliancesAdapter that tries
   SQLite first, falls back to MongoDB-only (with a sequence counter).
   
C. Update approve_async to inline the alliance_id resolution from SQLite as a fallback.
"""
with open('db/mongo_adapters.py', 'r', encoding='utf-8') as f:
    content = f.read()

# ---- Fix A: Update approve_async to accept optional alliance_id ----
# The current signature is:
#   async def approve_async(guild_id: int, admin_user_id: int) -> bool:
# We need to add alliance_id parameter that defaults to None.
# When provided, write alliances_id to server_alliances atomically.

old_approve = '''    @staticmethod
    async def approve_async(guild_id: int, admin_user_id: int) -> bool:
        """Approve request: apply access code + alliance name to server_alliances collection."""
        try:
            db = await _get_db_main_async()
            doc = await db[PendingConfigAdapter.COLL].find_one(
                {'guild_id': str(guild_id), 'status': 'pending'}
            )
            if not doc:
                return False
            now = datetime.utcnow().isoformat()
            server_payload = {
                'id': int(guild_id),
                'alliance_name': doc['alliance_name'],
                'member_list_password': doc['access_code'],
                'password_set_by': int(admin_user_id),
                'password_set_at': now,
                'updated_at': now
            }
            if doc.get('state') is not None:
                server_payload['state'] = int(doc['state'])

            await db[ServerAllianceAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': server_payload,
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            await db[PendingConfigAdapter.COLL].update_one(
                {'guild_id': str(guild_id)},
                {'$set': {
                    'status': 'approved',
                    'approved_by': int(admin_user_id),
                    'approved_at': now,
                    'updated_at': now
                }}
            )
            logger.info(f'Approved pending config for guild {guild_id} by admin {admin_user_id}')
            return True
        except Exception as e:
            logger.error(f'Failed to approve pending config for guild {guild_id}: {e}')
            return False'''

new_approve = '''    @staticmethod
    async def approve_async(guild_id: int, admin_user_id: int, alliance_id: int = None) -> bool:
        """Approve request: apply access code + alliance name to server_alliances collection.
        
        If alliance_id is provided, writes alliances_id atomically to server_alliances.
        If not provided, attempts to resolve it from SQLite alliance_list, then MongoDB alliances.
        """
        try:
            db = await _get_db_main_async()
            doc = await db[PendingConfigAdapter.COLL].find_one(
                {'guild_id': str(guild_id), 'status': 'pending'}
            )
            if not doc:
                return False
            now = datetime.utcnow().isoformat()
            alliance_name = doc.get('alliance_name', '')
            
            # --- Resolve numeric alliance_id if not supplied ---
            resolved_alliance_id = alliance_id
            if not resolved_alliance_id:
                # Try SQLite first
                try:
                    import sqlite3
                    db_path = 'db/alliance.sqlite'
                    with sqlite3.connect(db_path, timeout=10) as adb:
                        cur = adb.cursor()
                        cur.execute("SELECT alliance_id FROM alliance_list WHERE name = ?", (alliance_name,))
                        row = cur.fetchone()
                        if row:
                            resolved_alliance_id = int(row[0])
                        elif alliance_name:
                            cur.execute(
                                "INSERT INTO alliance_list (name, discord_server_id) VALUES (?, ?)",
                                (alliance_name, int(guild_id))
                            )
                            adb.commit()
                            resolved_alliance_id = cur.lastrowid
                except Exception as sqlite_err:
                    logger.warning(f'SQLite alliance lookup failed for guild {guild_id}: {sqlite_err}')

            if not resolved_alliance_id:
                # Try MongoDB alliances collection
                try:
                    alliance_doc = await db['alliances'].find_one({'name': str(alliance_name)})
                    if alliance_doc:
                        resolved_alliance_id = int(
                            alliance_doc.get('alliance_id') or alliance_doc.get('alliances_id') or 0
                        ) or None
                except Exception as mongo_err:
                    logger.warning(f'MongoDB alliances lookup failed for guild {guild_id}: {mongo_err}')

            server_payload = {
                'id': int(guild_id),
                'alliance_name': alliance_name,
                'member_list_password': doc['access_code'],
                'password_set_by': int(admin_user_id),
                'password_set_at': now,
                'updated_at': now
            }
            if doc.get('state') is not None:
                server_payload['state'] = int(doc['state'])
            # Critically: write alliances_id if we resolved one
            if resolved_alliance_id:
                server_payload['alliances_id'] = int(resolved_alliance_id)
                logger.info(f'Writing alliances_id={resolved_alliance_id} to server_alliances for guild {guild_id}')

            await db[ServerAllianceAdapter.COLL].update_one(
                {'_id': str(guild_id)},
                {
                    '$set': server_payload,
                    '$setOnInsert': {'created_at': now}
                },
                upsert=True
            )
            await db[PendingConfigAdapter.COLL].update_one(
                {'guild_id': str(guild_id)},
                {'$set': {
                    'status': 'approved',
                    'approved_by': int(admin_user_id),
                    'approved_at': now,
                    'updated_at': now
                }}
            )
            logger.info(f'Approved pending config for guild {guild_id} by admin {admin_user_id}')
            return True
        except Exception as e:
            logger.error(f'Failed to approve pending config for guild {guild_id}: {e}')
            return False'''

if old_approve in content:
    content = content.replace(old_approve, new_approve, 1)
    print("Fixed approve_async in PendingConfigAdapter")
else:
    # Try CRLF
    old_crlf = old_approve.replace('\n', '\r\n')
    new_crlf = new_approve.replace('\n', '\r\n')
    if old_crlf in content:
        content = content.replace(old_crlf, new_crlf, 1)
        print("Fixed approve_async in PendingConfigAdapter (CRLF)")
    else:
        print("ERROR: Could not find approve_async to patch - check manually")

with open('db/mongo_adapters.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Verifying syntax...")
import py_compile
try:
    py_compile.compile('db/mongo_adapters.py', doraise=True)
    print("Syntax OK!")
except py_compile.PyCompileError as e:
    print(f"Syntax ERROR: {e}")
