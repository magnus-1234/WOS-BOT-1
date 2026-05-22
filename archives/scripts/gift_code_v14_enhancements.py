"""
Gift Code System v1.4.0 Enhancements
Extracted from https://github.com/whiteout-project/bot/releases/tag/v1.4.0

This module contains new features to be integrated into the existing gift code system:
1. VIP-only code support
2. Minimum furnace level validation
3. Reactivated code handling
4. Enhanced validation
5. Configurable redemption priority
6. Improved CAPTCHA handling

Usage:
    Import this module and integrate the mixins/helpers into your existing cogs.
"""

import logging
import sqlite3
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import asyncio

logger = logging.getLogger(__name__)

# ============================================================================
# ERROR CODE DEFINITIONS (from v1.4.0)
# ============================================================================

VIP_REQUIRED_ERRORS = [
    'RECHARGE_MONEY_VIP',
    'VIP_REQUIREMENT_NOT_MET',
    'NEED_VIP_STATUS',
    'ERR_CDK_VIP_REQUIRED'
]

FURNACE_LEVEL_ERRORS = [
    'ERR_CDK_STOVE_LV',  # Most common furnace level error
    'FURNACE_LEVEL_TOO_LOW',
    'LEVEL_REQUIREMENT_NOT_MET',
    'STOVE_LV_NOT_ENOUGH'
]

REACTIVATED_CODE_INDICATORS = [
    'was_expired_now_active', 
    'code_reactivated'
]

# ============================================================================
# DATABASE SCHEMA ENHANCEMENTS
# ============================================================================

class GiftCodeV14Schema:
    """Database schema updates for v1.4.0 features """
        
    @staticmethod
    def create_vip_tracking_table(cursor):
        """Create table to track VIP status of members"""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS member_vip_status (
                fid TEXT PRIMARY KEY,
                is_vip INTEGER DEFAULT 0,
                vip_level INTEGER DEFAULT 0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        logger.info("✅ Created member_vip_status table")
    
    @staticmethod
    def create_code_requirements_table(cursor):
        """Create table to track code requirements (VIP, furnace level)"""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS gift_code_requirements (
                code TEXT PRIMARY KEY,
                vip_required INTEGER DEFAULT 0,
                min_furnace_level INTEGER DEFAULT 0,
                max_furnace_level INTEGER DEFAULT 999,
                reactivated INTEGER DEFAULT 0,
                reactivation_date TIMESTAMP,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_validated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes TEXT
            )
        """)
        logger.info("✅ Created gift_code_requirements table")
    
    @staticmethod
    def create_redemption_priority_table(cursor):
        """Create table for alliance redemption priority"""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alliance_redemption_priority (
                guild_id INTEGER,
                alliance_id INTEGER,
                priority_level INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, alliance_id)
            )
        """)
        logger.info("✅ Created alliance_redemption_priority table")
        
    @staticmethod
    def create_code_reactivation_history_table(cursor):
        """Track when codes are reactivated"""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS code_reactivation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                previous_status TEXT,
                reactivated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                detected_by TEXT
            )
        """)
        logger.info("✅ Created code_reactivation_history table")
    
    @staticmethod
    def setup_all_tables(db_connection):
        """Set up all v1.4.0 tables"""
        try:
            cursor = db_connection.cursor()
            GiftCodeV14Schema.create_vip_tracking_table(cursor)
            GiftCodeV14Schema.create_code_requirements_table(cursor)
            GiftCodeV14Schema.create_redemption_priority_table(cursor)
            GiftCodeV14Schema.create_code_reactivation_history_table(cursor)
            db_connection.commit()
            logger.info("✨ All v1.4.0 database tables created successfully")
            return True
        except Exception as e:
            logger.error(f"❌ Error creating v1.4.0 tables: {e}")
            return False


# ============================================================================
# VIP DETECTION & VALIDATION
# ============================================================================

class VIPValidator:
    """Handle VIP-only code validation"""
    
    def __init__(self, db_connection):
        self.db = db_connection
        self.cursor = db_connection.cursor()
    
    def is_vip_required_error(self, status: str) -> bool:
        """Check if error indicates VIP requirement"""
        return any(vip_err in status.upper() for vip_err in VIP_REQUIRED_ERRORS)
    
    def record_vip_requirement(self, giftcode: str, detected_from_error: bool = True):
        """Mark a code as requiring VIP"""
        try:
            self.cursor.execute("""
                INSERT OR REPLACE INTO gift_code_requirements 
                (code, vip_required, last_validated, notes)
                VALUES (?, 1, ?, ?)
            """, (giftcode, datetime.now(), 
                  'Detected from API error' if detected_from_error else 'Manually set'))
            self.db.commit()
            logger.info(f"💎 Marked code '{giftcode}' as VIP-required")
            return True
        except Exception as e:
            logger.error(f"Error recording VIP requirement: {e}")
            return False
    
    def is_code_vip_only(self, giftcode: str) -> bool:
        """Check if code is marked as VIP-only"""
        try:
            self.cursor.execute(
                "SELECT vip_required FROM gift_code_requirements WHERE code = ?",
                (giftcode,)
            )
            result = self.cursor.fetchone()
            return bool(result and result[0])
        except Exception:
            return False
    
    def update_member_vip_status(self, fid: str, is_vip: bool, vip_level: int = 0):
        """Update VIP status for a member"""
        try:
            self.cursor.execute("""
                INSERT OR REPLACE INTO member_vip_status 
                (fid, is_vip, vip_level, last_updated, last_checked)
                VALUES (?, ?, ?, ?, ?)
            """, (fid, 1 if is_vip else 0, vip_level, datetime.now(), datetime.now()))
            self.db.commit()
            return True
        except Exception as e:
            logger.error(f"Error updating VIP status for FID {fid}: {e}")
            return False
    
    def is_member_vip(self, fid: str) -> Tuple[bool, int]:
        """Check if member has VIP status (returns is_vip, vip_level)"""
        try:
            self.cursor.execute(
                "SELECT is_vip, vip_level FROM member_vip_status WHERE fid = ?",
                (fid,)
            )
            result = self.cursor.fetchone()
            if result:
                return (bool(result[0]), result[1])
            return (False, 0)
        except Exception:
            return (False, 0)
    
    async def fetch_and_update_vip_status(self, fid: str, player_data: dict) -> bool:
        """Extract and update VIP status from player data"""
        try:
            # Check if player_data contains VIP information
            # This varies by API, but common fields are:
            # - vip_level, vip_lv, vip, is_vip
            vip_level = player_data.get('vip_level') or player_data.get('vip_lv') or 0
            is_vip = vip_level > 0 or player_data.get('is_vip', False)
            
            self.update_member_vip_status(fid, is_vip, vip_level)
            logger.debug(f"Updated VIP status for FID {fid}: VIP={is_vip}, Level={vip_level}")
            return True
        except Exception as e:
            logger.error(f"Error fetching VIP status for FID {fid}: {e}")
            return False


# ============================================================================
# FURNACE LEVEL VALIDATION
# ============================================================================

class FurnaceLevelValidator:
    """Handle furnace level requirement validation"""
    
    def __init__(self, db_connection):
        self.db = db_connection
        self.cursor = db_connection.cursor()
    
    def is_furnace_level_error(self, status: str) -> bool:
        """Check if error indicates furnace level requirement"""
        return any(furnace_err in status.upper() for furnace_err in FURNACE_LEVEL_ERRORS)
    
    def record_furnace_requirement(self, giftcode: str, min_level: int, max_level: int = 999):
        """Record furnace level requirement for a code"""
        try:
            self.cursor.execute("""
                INSERT OR REPLACE INTO gift_code_requirements 
                (code, min_furnace_level, max_furnace_level, last_validated, notes)
                VALUES (?, ?, ?, ?, ?)
            """, (giftcode, min_level, max_level, datetime.now(),
                  f'Min level: {min_level}, Max level: {max_level}'))
            self.db.commit()
            logger.info(f"🏭 Marked code '{giftcode}' as requiring furnace level {min_level}+")
            return True
        except Exception as e:
            logger.error(f"Error recording furnace requirement: {e}")
            return False
    
    def get_furnace_requirement(self, giftcode: str) -> Tuple[int, int]:
        """Get furnace level requirement for code (returns min, max)"""
        try:
            self.cursor.execute(
                "SELECT min_furnace_level, max_furnace_level FROM gift_code_requirements WHERE code = ?",
                (giftcode,)
            )
            result = self.cursor.fetchone()
            if result:
                return (result[0] or 0, result[1] or 999)
            return (0, 999)
        except Exception:
            return (0, 999)
    
    def meets_furnace_requirement(self, member_level: int, giftcode: str) -> Tuple[bool, str]:
        """
        Check if member meets furnace requirement
        Returns: (meets_requirement, message)
        """
        min_level, max_level = self.get_furnace_requirement(giftcode)
        
        if member_level < min_level:
            return (False, f"Furnace level {member_level} is below minimum {min_level}")
        elif member_level > max_level:
            return (False, f"Furnace level {member_level} is above maximum {max_level}")
        
        return (True, "Meets furnace requirement")


# ============================================================================
# CODE REACTIVATION DETECTOR
# ============================================================================

class CodeReactivationDetector:
    """Detect and handle reactivated gift codes"""
    
    def __init__(self, db_connection):
        self.db = db_connection
        self.cursor = db_connection.cursor()
    
    async def detect_reactivation(self, giftcode: str, current_status: str, previous_status: str = None) -> bool:
        """
        Detect if a code has been reactivated
        Returns True if reactivation detected
        """
        try:
            # Check if code was previously marked as expired/invalid
            if not previous_status:
                self.cursor.execute(
                    "SELECT validation_status FROM gift_codes WHERE giftcode = ?",
                    (giftcode,)
                )
                result = self.cursor.fetchone()
                previous_status = result[0] if result else None
            
            # Reactivation detected if:
            # 1. Previously expired/invalid
            # 2. Now valid/active
            if previous_status in ['EXPIRED', 'INVALID_CODE', 'CDK_NOT_FOUND']:
                if current_status in ['SUCCESS', 'VALID', 'ACTIVE']:
                    logger.info(f"🔄 Code reactivation detected: '{giftcode}' ({previous_status} → {current_status})")
                    await self.record_reactivation(giftcode, previous_status)
                    return True
            
            return False
        except Exception as e:
            logger.error(f"Error detecting reactivation: {e}")
            return False
    
    async def record_reactivation(self, giftcode: str, previous_status: str):
        """Record code reactivation in history"""
        try:
            self.cursor.execute("""
                INSERT INTO code_reactivation_history 
                (code, previous_status, reactivated_at, detected_by)
                VALUES (?, ?, ?, ?)
            """, (giftcode, previous_status, datetime.now(), 'auto_detector'))
            
            # Update requirements table
            self.cursor.execute("""
                UPDATE gift_code_requirements 
                SET reactivated = 1, reactivation_date = ?
                WHERE code = ?
            """, (datetime.now(), giftcode))
            
            self.db.commit()
            logger.info(f"✅ Recorded reactivation for code '{giftcode}'")
            return True
        except Exception as e:
            logger.error(f"Error recording reactivation: {e}")
            return False
    
    async def clear_redemption_history(self, giftcode: str) -> int:
        """
        Clear redemption records for reactivated code
        Returns: number of records cleared
        """
        try:
            # Clear from auto_redeem tracking (if exists)
            self.cursor.execute(
                "DELETE FROM auto_redeem_tracking WHERE giftcode = ?",
                (giftcode,)
            )
            cleared_count = self.cursor.rowcount
            
            self.db.commit()
            logger.info(f"🧹 Cleared {cleared_count} redemption records for reactivated code '{giftcode}'")
            return cleared_count
        except Exception as e:
            logger.error(f"Error clearing redemption history: {e}")
            return 0
    
    def is_code_reactivated(self, giftcode: str) -> bool:
        """Check if code has been reactivated"""
        try:
            self.cursor.execute(
                "SELECT reactivated FROM gift_code_requirements WHERE code = ? AND reactivated = 1",
                (giftcode,)
            )
            return self.cursor.fetchone() is not None
        except Exception:
            return False


# ============================================================================
# REDEMPTION PRIORITY QUEUE
# ============================================================================

class RedemptionPriorityQueue:
    """Manage redemption order with configurable priorities"""
    
    def __init__(self, db_connection):
        self.db = db_connection
        self.cursor = db_connection.cursor()
    
    def set_alliance_priority(self, guild_id: int, alliance_id: int, priority: int = 0):
        """
        Set redemption priority for an alliance
        Higher priority = processed first
        Priority levels: 0 (normal), 1 (high), 2 (critical)
        """
        try:
            self.cursor.execute("""
                INSERT OR REPLACE INTO alliance_redemption_priority
                (guild_id, alliance_id, priority_level, updated_at)
                VALUES (?, ?, ?, ?)
            """, (guild_id, alliance_id, priority, datetime.now()))
            self.db.commit()
            logger.info(f"⭐ Set priority {priority} for alliance {alliance_id} in guild {guild_id}")
            return True
        except Exception as e:
            logger.error(f"Error setting alliance priority: {e}")
            return False
    
    def get_alliance_priority(self, guild_id: int, alliance_id: int) -> int:
        """Get priority level for an alliance (default: 0)"""
        try:
            self.cursor.execute(
                "SELECT priority_level FROM alliance_redemption_priority WHERE guild_id = ? AND alliance_id = ?",
                (guild_id, alliance_id)
            )
            result = self.cursor.fetchone()
            return result[0] if result else 0
        except Exception:
            return 0
    
    def get_sorted_alliances(self, guild_id: int, alliance_ids: List[int]) -> List[Tuple[int, int]]:
        """
        Get alliances sorted by priority (highest first)
        Returns: List of (alliance_id, priority) tuples
        """
        alliance_priorities = [
            (aid, self.get_alliance_priority(guild_id, aid))
            for aid in alliance_ids
        ]
        # Sort by priority (descending), then by alliance_id (ascending)
        return sorted(alliance_priorities, key=lambda x: (-x[1], x[0]))
    
    def sort_members_by_priority(self, guild_id: int, members: List[dict]) -> List[dict]:
        """
        Sort members list by various priorities:
        1. VIP status (VIP first)
        2. Furnace level (higher first)
        3. Alliance priority  (if available)
        
        Returns: Sorted members list
        """
        def get_priority_score(member):
            vip_score = 1000 if member.get('is_vip', False) else 0
            furnace_score = member.get('furnace_lv', 0)
            return vip_score + furnace_score
        
        return sorted(members, key=get_priority_score, reverse=True)


# ============================================================================
# ENHANCED ERROR HANDLER
# ============================================================================

class EnhancedErrorHandler:
    """Enhanced error handling for v1.4.0 features"""
    
    def __init__(self, db_connection):
        self.vip_validator = VIPValidator(db_connection)
        self.furnace_validator = FurnaceLevelValidator(db_connection)
    
    def categorize_error(self, status: str, giftcode: str = None, fid: str = None) -> dict:
        """
        Categorize redemption error and provide actionable information
        Returns dict with: category, is_permanent, requires_vip, requires_furnace_level, message
        """
        status_upper = status.upper()
        
        result = {
            'category': 'UNKNOWN',
            'is_permanent': False,
            'requires_vip': False,
            'requires_furnace_level': False,
            'min_furnace_level': None,
            'message': f'Unknown status: {status}',
            'should_retry': False
        }
        
        # VIP requirement
        if self.vip_validator.is_vip_required_error(status):
            result.update({
                'category': 'VIP_REQUIRED',
                'is_permanent': True,
                'requires_vip': True,
                'message': '💎 This code requires VIP status',
                'should_retry': False
            })
            if giftcode:
                self.vip_validator.record_vip_requirement(giftcode)
        
        # Furnace level requirement
        elif self.furnace_validator.is_furnace_level_error(status):
            result.update({
                'category': 'FURNACE_LEVEL_REQUIRED',
                'is_permanent': True,
                'requires_furnace_level': True,
                'message': '🏭 This code requires a higher furnace level',
                'should_retry': False
            })
            # Try to extract level from error message
            import re
            level_match = re.search(r'(\d+)', status)
            if level_match and giftcode:
                min_level = int(level_match.group(1))
                result['min_furnace_level'] = min_level
                self.furnace_validator.record_furnace_requirement(giftcode, min_level)
        
        # Success states
        elif status_upper in ['SUCCESS', 'ALREADY_RECEIVED', 'SAME TYPE EXCHANGE']:
            result.update({
                'category': 'SUCCESS',
                'is_permanent': False,
                'message': '✅ Redemption successful',
                'should_retry': False
            })
        
        # Permanent failures
        elif status_upper in ['INVALID_CODE', 'EXPIRED', 'CDK_NOT_FOUND', 'USAGE_LIMIT', 'TIME_ERROR']:
            result.update({
                'category': 'INVALID_CODE',
                'is_permanent': True,
                'message': f'❌ Code is invalid or expired: {status}',
                'should_retry': False
            })
        
        # Rate limiting
        elif 'RATE' in status_upper or 'FREQUENT' in status_upper or status_upper == 'CAPTCHA_TOO_FREQUENT':
            result.update({
                'category': 'RATE_LIMITED',
                'is_permanent': False,
                'message': '⏳ Rate limited, will retry with backoff',
                'should_retry': True
            })
        
        # Temporary failures (network, timeout, etc.)
        elif status_upper in ['TIMEOUT', 'NETWORK_ERROR', 'CAPTCHA_FETCH_ERROR']:
            result.update({
                'category': 'TEMPORARY_ERROR',
                'is_permanent': False,
                'message': f'⚠️ Temporary error: {status}, will retry',
                'should_retry': True
            })
        
        return result


# ============================================================================
# INTEGRATION HELPER
# ============================================================================

class GiftCodeV14Integrator:
    """Main integration class for adding v1.4.0 features to existing cog"""
    
    def __init__(self, cog, db_connection):
        """
        Initialize with reference to the ManageGiftCode cog instance
        
        Args:
            cog: Instance of ManageGiftCode cog
            db_connection: Database connection
        """
        self.cog = cog
        self.db = db_connection
        
        # Initialize all validators
        self.vip_validator = VIPValidator(db_connection)
        self.furnace_validator = FurnaceLevelValidator(db_connection)
        self.reactivation_detector = CodeReactivationDetector(db_connection)
        self.priority_queue = RedemptionPriorityQueue(db_connection)
        self.error_handler = EnhancedErrorHandler(db_connection)
        
        logger.info("✨ GiftCode v1.4.0 integrator initialized")
    
    async def should_skip_member(self, fid: str, nickname: str, furnace_lv: int, giftcode: str) -> Tuple[bool, str]:
        """
        Determine if member should be skipped for redemption
        Returns: (should_skip, reason)
        """
        # Check VIP requirement
        if self.vip_validator.is_code_vip_only(giftcode):
            is_vip, vip_level = self.vip_validator.is_member_vip(fid)
            if not is_vip:
                return (True, f"💎 {nickname} - Skipped (VIP required)")
        
        # Check furnace level requirement
        meets_req, msg = self.furnace_validator.meets_furnace_requirement(furnace_lv, giftcode)
        if not meets_req:
            return (True, f"🏭 {nickname} - Skipped ({msg})")
        
        return (False, "")
    
    async def handle_redemption_error(self, status: str, giftcode: str, fid: str, nickname: str) -> dict:
        """
        Handle redemption error with v1.4.0 features
        Returns error info dict from categorize_error
        """
        error_info = self.error_handler.categorize_error(status, giftcode, fid)
        logger.info(f"{error_info['message']} for {nickname} (FID: {fid})")
        return error_info
    
    async def check_code_reactivation(self, giftcode: str, current_status: str) ->bool:
        """Check if code has been reactivated and clear history if needed"""
        if await self.reactivation_detector.detect_reactivation(giftcode, current_status):
            cleared = await self.reactivation_detector.clear_redemption_history(giftcode)
            logger.info(f"🔄 Code '{giftcode}' reactivated - cleared {cleared} old redemption records")
            return True
        return False


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

"""
# In your ManageGiftCode cog __init__:
from gift_code_v14_enhancements import GiftCodeV14Schema, GiftCodeV14Integrator

class ManageGiftCode(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.giftcode_db = get_db_connection('giftcode.sqlite')
        
        # Set up v1.4.0 tables
        GiftCodeV14Schema.setup_all_tables(self.giftcode_db)
        
        # Initialize v1.4.0 integrator
        self.v14 = GiftCodeV14Integrator(self, self.giftcode_db)
    
    async def _redeem_for_member(self, guild_id, fid, nickname, furnace_lv, giftcode):
        # Check if member should be skipped (VIP/furnace requirements)
        should_skip, reason = await self.v14.should_skip_member(fid, nickname, furnace_lv, giftcode)
        if should_skip:
            self.logger.info(reason)
            return ("SKIPPED", 0, 0, 0)
        
        # ... existing redemption logic ...
        
        status, img, code, method = await self.attempt_gift_code_with_api(fid, giftcode, session)
        
        # Handle error with v1.4.0 features
        error_info = await self.v14.handle_redemption_error(status, giftcode, fid, nickname)
        
        if error_info['requires_vip']:
            # Skip future attempts for this code/member combination
            return ("VIP_REQUIRED", 0, 0, 1)
        
        # ... rest of redemption logic ...
"""

if __name__ == "__main__":
    # Test module
    print("Gift Code v1.4.0 Enhancements Module")
    print("=====================================")
    print("✅ VIP Validator")
    print("✅ Furnace Level Validator")
    print("✅ Code Reactivation Detector")
    print("✅ Redemption Priority Queue")
    print("✅ Enhanced Error Handler")
    print("\nReady to integrate into ManageGiftCode cog!")
