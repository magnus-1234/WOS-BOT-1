import os

filepath = r"f:\Whiteout Survival Bot\DISCORD BOT\db\mongo_adapters.py"

append_text = """

class AlliancesAdapter:
    COLL = 'alliances'

    @staticmethod
    def get_all() -> list:
        try:
            db = _get_db_main()
            return list(db[AlliancesAdapter.COLL].find({}))
        except Exception as e:
            logger.error(f"Failed to get all alliances: {e}")
            return []

    @staticmethod
    async def get_all_async() -> list:
        try:
            db = await _get_db_main_async()
            return await db[AlliancesAdapter.COLL].find({}).to_list(length=None)
        except Exception as e:
            logger.error(f"Failed to get async alliances: {e}")
            return []

    @staticmethod
    def get(alliance_id: int) -> Optional[dict]:
        try:
            db = _get_db_main()
            return db[AlliancesAdapter.COLL].find_one({'alliance_id': int(alliance_id)})
        except Exception as e:
            logger.error(f"Failed to get alliance {alliance_id}: {e}")
            return None


class AllianceSettingsAdapter:
    COLL = 'alliance_settings'

    @staticmethod
    def get(alliance_id: int) -> Optional[dict]:
        try:
            db = _get_db_main()
            return db[AllianceSettingsAdapter.COLL].find_one({'alliance_id': int(alliance_id)})
        except Exception as e:
            logger.error(f"Failed to get alliance settings {alliance_id}: {e}")
            return None


class AllianceMembersAdapter:
    COLL = 'alliance_members'

    @staticmethod
    def get_all_members() -> list:
        try:
            db = _get_db_main()
            return list(db[AllianceMembersAdapter.COLL].find({}))
        except Exception as e:
            logger.error(f"Failed to get all members: {e}")
            return []

    @staticmethod
    async def get_all_members_async() -> list:
        try:
            db = await _get_db_main_async()
            return await db[AllianceMembersAdapter.COLL].find({}).to_list(length=None)
        except Exception as e:
            logger.error(f"Failed to get async members: {e}")
            return []

    @staticmethod
    def get_member(fid: str) -> Optional[dict]:
        try:
            db = _get_db_main()
            return db[AllianceMembersAdapter.COLL].find_one({'fid': str(fid)})
        except Exception as e:
            logger.error(f"Failed to get member {fid}: {e}")
            return None

    @staticmethod
    async def get_member_async(fid: str) -> Optional[dict]:
        try:
            db = await _get_db_main_async()
            return await db[AllianceMembersAdapter.COLL].find_one({'fid': str(fid)})
        except Exception as e:
            logger.error(f"Failed to get async member {fid}: {e}")
            return None

    @staticmethod
    def upsert_member(fid: str, data: dict) -> bool:
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            update_data = data.copy()
            update_data['updated_at'] = now
            db[AllianceMembersAdapter.COLL].update_one(
                {'fid': str(fid)},
                {'$set': update_data, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"Failed to upsert member {fid}: {e}")
            return False

    @staticmethod
    async def upsert_member_async(fid: str, data: dict) -> bool:
        try:
            db = await _get_db_main_async()
            now = datetime.utcnow().isoformat()
            update_data = data.copy()
            update_data['updated_at'] = now
            await db[AllianceMembersAdapter.COLL].update_one(
                {'fid': str(fid)},
                {'$set': update_data, '$setOnInsert': {'created_at': now}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"Failed to async upsert member {fid}: {e}")
            return False


class AllianceMonitoringAdapter:
    COLL = 'alliance_monitoring'

    @staticmethod
    def get_all_monitors() -> list:
        try:
            db = _get_db_main()
            return list(db[AllianceMonitoringAdapter.COLL].find({}))
        except Exception as e:
            logger.error(f"Failed to get all monitors: {e}")
            return []

    @staticmethod
    async def get_all_monitors_async() -> list:
        try:
            db = await _get_db_main_async()
            return await db[AllianceMonitoringAdapter.COLL].find({}).to_list(length=None)
        except Exception as e:
            logger.error(f"Failed to get async monitors: {e}")
            return []


class FurnaceHistoryAdapter:
    COLL = 'furnace_history'

    @staticmethod
    def insert(data: dict) -> bool:
        try:
            db = _get_db_main()
            data['created_at'] = datetime.utcnow().isoformat()
            db[FurnaceHistoryAdapter.COLL].insert_one(data)
            return True
        except Exception as e:
            logger.error(f"Failed to insert furnace history: {e}")
            return False

    @staticmethod
    async def insert_async(data: dict) -> bool:
        try:
            db = await _get_db_main_async()
            data['created_at'] = datetime.utcnow().isoformat()
            await db[FurnaceHistoryAdapter.COLL].insert_one(data)
            return True
        except Exception as e:
            logger.error(f"Failed to async insert furnace history: {e}")
            return False


class AutoRedeemMembersAdapter:
    COLL = 'auto_redeem_members'

    @staticmethod
    def get_all_for_guild(guild_id: int) -> list:
        try:
            db = _get_db_main()
            return list(db[AutoRedeemMembersAdapter.COLL].find({'guild_id': int(guild_id)}))
        except Exception as e:
            logger.error(f"Failed to get auto redeem members for guild {guild_id}: {e}")
            return []

    @staticmethod
    def add_member(guild_id: int, fid: str, nickname: str = "", furnace_lv: int = 0, avatar: str = "") -> bool:
        try:
            db = _get_db_main()
            now = datetime.utcnow().isoformat()
            db[AutoRedeemMembersAdapter.COLL].update_one(
                {'guild_id': int(guild_id), 'fid': str(fid)},
                {
                    '$set': {
                        'nickname': str(nickname),
                        'furnace_lv': int(furnace_lv),
                        'avatar_image': str(avatar),
                        'updated_at': now
                    },
                    '$setOnInsert': {'created_at': now}

                                    },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"Failed to add auto redeem member {fid}: {e}")
            return False

    @staticmethod
    def remove_member(guild_id: int, fid: str) -> bool:
        try:
            db = _get_db_main()
            res = db[AutoRedeemMembersAdapter.COLL].delete_one({'guild_id': int(guild_id), 'fid': str(fid)})
            return res.deleted_count > 0
        except Exception as e:
            logger.error(f"Failed to remove auto redeem member {fid}: {e}")
            return False

    @staticmethod
    def clear_all(guild_id: int) -> bool:
        try:
            db = _get_db_main()
            res = db[AutoRedeemMembersAdapter.COLL].delete_many({'guild_id': int(guild_id)})
            return True
        except Exception as e:
            logger.error(f"Failed to clear auto redeem members for {guild_id}: {e}")
            return False
"""

with open(filepath, 'a', encoding='utf-8') as f:
    f.write(append_text)

print("Appended adapters successfully.")
