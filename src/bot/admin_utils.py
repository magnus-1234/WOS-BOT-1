"""
Shared admin permission utilities for all cogs.
Provides consistent admin checking with MongoDB/SQLite fallback.
"""
import sqlite3
from pathlib import Path

# Import MongoDB adapters with fallback
try:
    from db.mongo_adapters import mongo_enabled, AdminsAdapter
except Exception:
    mongo_enabled = lambda: False
    class AdminsAdapter:
        @staticmethod
        def get(user_id): return None
        @staticmethod
        def upsert(user_id, is_initial): return False
        @staticmethod
        def count(): return 0

# Import database path utilities
try:
    from db_utils import get_db_connection
except ImportError:
    def get_db_connection(db_name: str, **kwargs):
        repo_root = Path(__file__).resolve().parent.parent.parent
        db_dir = repo_root / "db"
        db_dir.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(db_dir / db_name), **kwargs)


def get_admin(user_id):
    """
    Get admin info with MongoDB fallback to SQLite.
    Returns admin record or None.
    """
    try:
        if mongo_enabled():
            admin = AdminsAdapter.get(user_id)
            if admin is not None:
                return admin
    except Exception as e:
        print(f"[WARNING] MongoDB AdminsAdapter.get failed: {e}. Falling back to SQLite.")
    
    # SQLite fallback
    try:
        with get_db_connection('settings.sqlite') as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, is_initial FROM admin WHERE id = ?", (user_id,))
            return cursor.fetchone()
    except Exception as e:
        print(f"[ERROR] SQLite admin query failed: {e}")
        return None


def upsert_admin(user_id, is_initial=1):
    """
    Insert/update admin with MongoDB fallback to SQLite.
    Returns True on success, False on failure.
    """
    try:
        if mongo_enabled():
            success = AdminsAdapter.upsert(user_id, is_initial)
            if success:
                return True
            print(f"[WARNING] MongoDB AdminsAdapter.upsert returned False. Falling back to SQLite.")
    except Exception as e:
        print(f"[WARNING] MongoDB AdminsAdapter.upsert failed: {e}. Falling back to SQLite.")
    
    # SQLite fallback
    try:
        with get_db_connection('settings.sqlite') as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO admin (id, is_initial) VALUES (?, ?)",
                (user_id, is_initial)
            )
            conn.commit()
            return True
    except Exception as e:
        print(f"[ERROR] SQLite admin upsert failed: {e}")
        return False


def count_admins():
    """
    Count admins with MongoDB fallback to SQLite.
    Returns count or 0 on error.
    """
    try:
        if mongo_enabled():
            count = AdminsAdapter.count()
            if count is not None and count >= 0:
                return count
    except Exception as e:
        print(f"[WARNING] MongoDB AdminsAdapter.count failed: {e}. Falling back to SQLite.")
    
    # SQLite fallback
    try:
        with get_db_connection('settings.sqlite') as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM admin")
            return cursor.fetchone()[0]
    except Exception as e:
        print(f"[ERROR] SQLite admin count failed: {e}")
        return 0


def is_admin(user_id):
    """
    Check if user is an admin.
    Returns True if admin, False otherwise.
    """
    admin = get_admin(user_id)
    return admin is not None


def is_global_admin(user_id):
    """
    Check if user is a global admin (is_initial = 1).
    Returns True if global admin, False otherwise.
    """
    admin = get_admin(user_id)
    if admin is None:
        return False
    
    # Handle both tuple (SQLite) and dict (MongoDB) formats
    if isinstance(admin, tuple):
        return int(admin[1]) == 1 if len(admin) > 1 else False
    elif isinstance(admin, dict):
        return int(admin.get('is_initial', 0)) == 1
    
    return False


def grant_admin_if_discord_admin(user_id, interaction):
    """
    Grant admin rights if user has Discord administrator permissions.
    Returns True if admin rights were granted or user already has them, False otherwise.
    """
    # Check if already admin
    if is_admin(user_id):
        return True
    
    # Check Discord permissions
    if interaction.guild and (interaction.user.guild_permissions.administrator or interaction.guild.owner_id == interaction.user.id):
        # Grant admin rights
        return upsert_admin(user_id, 0)
    
    return False


async def is_bot_owner(bot, user_id):
    """
    Check if user is the bot owner.
    Uses BOT_OWNER_ID environment variable as primary source for reliability on hosted environments.
    Falls back to Discord.py's bot.is_owner() if env var is not set.
    
    Args:
        bot: Discord bot instance
        user_id: User ID to check (int)
    
    Returns:
        True if user is bot owner, False otherwise.
    """
    import os
    
    # Primary: Check BOT_OWNER_ID environment variable
    owner_id_str = os.getenv('BOT_OWNER_ID')
    print(f"[DEBUG] is_bot_owner called - user_id: {user_id}, BOT_OWNER_ID env: {owner_id_str}")
    
    if owner_id_str:
        try:
            owner_id = int(owner_id_str)
            result = user_id == owner_id
            print(f"[DEBUG] BOT_OWNER_ID check - owner_id: {owner_id}, user_id: {user_id}, match: {result}")
            return result
        except (ValueError, TypeError):
            print(f"[WARNING] BOT_OWNER_ID is set but invalid: {owner_id_str}")
    
    # Fallback: Use Discord.py's built-in owner check
    print(f"[DEBUG] BOT_OWNER_ID not set, using Discord.py fallback for user_id: {user_id}")
    try:
        # Get user object if we have an ID
        if isinstance(user_id, int):
            user = bot.get_user(user_id)
            if user is None:
                try:
                    user = await bot.fetch_user(user_id)
                except Exception:
                    print(f"[DEBUG] Failed to fetch user {user_id}")
                    return False
        else:
            user = user_id
        
        result = await bot.is_owner(user)
        print(f"[DEBUG] Discord.py is_owner result: {result}")
        return result
    except Exception as e:
        print(f"[ERROR] Failed to check bot owner: {e}")
        return False


def format_furnace_level(furnace_lv):
    """
    Format furnace level display matching game logic.
    Fire Crystal levels start at level 31:
    - 31-34: 30-1 to 30-4
    - 35: 1
    - 36-39: 1-1 to 1-4
    - 40: 2
    - 41-43: 2-1
    - 44: 2-2
    - 45: 3
    """
    try:
        if furnace_lv is None:
            return "0"
            
        lv = int(furnace_lv)
        
        if lv <= 30:
            return str(lv)
        
        # Specific requested mapping
        if lv == 31: return "30-1"
        if lv == 32: return "30-2"
        if lv == 33: return "30-3"
        if lv == 34: return "30-4"
        if lv == 35: return "1"
        if lv == 36: return "1-1"
        if lv == 37: return "1-2"
        if lv == 38: return "1-3"
        if lv == 39: return "1-4"
        if lv == 40: return "2"
        if lv == 41: return "2-1"
        if lv == 42: return "2-1"
        if lv == 43: return "2-1"
        if lv == 44: return "2-2"
        if lv == 45: return "3"
        
        # Default pattern for 46+ (FC 3-1, 3-2, etc.)
        relative = lv - 45
        tier = (relative // 5) + 3
        stage = relative % 5
        
        if stage == 0:
            return str(tier)
        return f"{tier}-{stage}"
            
    except (ValueError, TypeError):
        return str(furnace_lv) if furnace_lv else "0"


def get_level_mapping():
    """
    Returns a consistent mapping of raw furnace levels to display strings.
    Covers levels 1-84.
    """
    mapping = {i: str(i) for i in range(1, 31)}
    for i in range(31, 85):
        mapping[i] = format_furnace_level(i)
    return mapping
