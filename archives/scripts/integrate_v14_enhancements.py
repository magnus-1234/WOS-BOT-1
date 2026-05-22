"""
Automated Integration Script for Gift Code v1.4.0 Features

This script automates the integration of v1.4.0 enhancements into your existing
ManageGiftCode cog.

Usage:
    python integrate_v14_enhancements.py --setup-database     # Create new tables only
    python integrate_v14_enhancements.py --test               # Run tests
    python integrate_v14_enhancements.py --auto-integrate     # Full integration (backup + integrate)
    python integrate_v14_enhancements.py --status             # Check integration status
"""

import sys
import os
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from db_utils import get_db_connection
except ImportError:
    def get_db_connection(db_name, **kwargs):
        """Fallback DB connection"""
        db_path = os.path.join(os.path.dirname(__file__), db_name)
        return sqlite3.connect(db_path, **kwargs)

from gift_code_v14_enhancements import (
    GiftCodeV14Schema,
    VIPValidator,
    FurnaceLevelValidator,
    CodeReactivationDetector,
    RedemptionPriorityQueue,
    EnhancedErrorHandler
)


class V14Integrator:
    """Automated integration handler"""
    
    def __init__(self):
        self.db = None
        self.cursor = None
        self.backup_dir = Path("backups/v14_integration")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
    def connect_db(self):
        """Connect to database"""
        try:
            self.db = get_db_connection('giftcode.sqlite', check_same_thread=False)
            self.cursor = self.db.cursor()
            print("✅ Connected to giftcode.sqlite")
            return True
        except Exception as e:
            print(f"❌ Failed to connect to database: {e}")
            return False
    
    def backup_database(self):
        """Backup database before making changes"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = self.backup_dir / f"giftcode_backup_{timestamp}.sqlite"
            
            # Copy database file
            db_path = "giftcode.sqlite"
            if os.path.exists(db_path):
                shutil.copy2(db_path, backup_file)
                print(f"✅ Database backed up to: {backup_file}")
                return True
            else:
                print(f"⚠️  Database file not found at: {db_path}")
                return False
        except Exception as e:
            print(f"❌ Backup failed: {e}")
            return False
    
    def setup_database(self):
        """Create v1.4.0 database tables"""
        print("\n" + "="*60)
        print("Setting up v1.4.0 Database Tables")
        print("="*60)
        
        if not self.connect_db():
            return False
        
        try:
            success = GiftCodeV14Schema.setup_all_tables(self.db)
            if success:
                print("✅ All v1.4.0 tables created successfully")
                return True
            else:
                print("❌ Failed to create some tables")
                return False
        except Exception as e:
            print(f"❌ Error during table creation: {e}")
            return False
    
    def check_integration_status(self):
        """Check if v1.4.0 features are already integrated"""
        print("\n" + "="*60)
        print("Checking Integration Status")
        print("="*60)
        
        if not self.connect_db():
            return
        
        # Check tables
        tables_to_check = [
            'member_vip_status',
            'gift_code_requirements',
            'alliance_redemption_priority',
            'code_reactivation_history'
        ]
        
        print("\n📋 Database Tables:")
        for table in tables_to_check:
            try:
                self.cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = self.cursor.fetchone()[0]
                print(f"  ✅ {table}: {count} records")
            except sqlite3.OperationalError:
                print(f"  ❌ {table}: Not found")
        
        # Check cog file
        print("\n📄 Cog Integration:")
        cog_path = Path("cogs/manage_giftcode.py")
        if cog_path.exists():
            content = cog_path.read_text()
            if 'gift_code_v14_enhancements' in content:
                print("  ✅ v1.4.0 import found in manage_giftcode.py")
            else:
                print("  ❌ v1.4.0 not integrated in manage_giftcode.py")
            
            if 'GiftCodeV14Integrator' in content:
                print("  ✅ GiftCodeV14Integrator initialized")
            else:
                print("  ❌ GiftCodeV14Integrator not initialized")
        else:
            print("  ❌ manage_giftcode.py not found")
        
        # Check enhancement module
        print("\n📦 Enhancement Module:")
        if Path("gift_code_v14_enhancements.py").exists():
            print("  ✅ gift_code_v14_enhancements.py exists")
        else:
            print("  ❌ gift_code_v14_enhancements.py not found")
    
    def run_tests(self):
        """Run integration tests"""
        print("\n" + "="*60)
        print("Running Integration Tests")
        print("="*60)
        
        if not self.connect_db():
            return False
        
        all_passed = True
        
        # Test 1: VIP Validator
        print("\n🧪 Test 1: VIP Validator")
        try:
            vip_val = VIPValidator(self.db)
            
            # Test error detection
            assert vip_val.is_vip_required_error("RECHARGE_MONEY_VIP")
            print("  ✅ VIP error detection works")
            
            # Test VIP status update
            vip_val.update_member_vip_status("TEST123", True, 5)
            is_vip, level = vip_val.is_member_vip("TEST123")
            assert is_vip and level == 5
            print("  ✅ VIP status update works")
            
            # Cleanup
            self.cursor.execute("DELETE FROM member_vip_status WHERE fid = 'TEST123'")
            self.db.commit()
        except Exception as e:
            print(f"  ❌ VIP Validator test failed: {e}")
            all_passed = False
        
        # Test 2: Furnace Level Validator
        print("\n🧪 Test 2: Furnace Level Validator")
        try:
            furnace_val = FurnaceLevelValidator(self.db)
            
            # Test error detection
            assert furnace_val.is_furnace_level_error("ERR_CDK_STOVE_LV")
            print("  ✅ Furnace error detection works")
            
            # Test requirement recording
            furnace_val.record_furnace_requirement("TESTCODE", 25)
            min_lv, max_lv = furnace_val.get_furnace_requirement("TESTCODE")
            assert min_lv == 25
            print("  ✅ Furnace requirement recording works")
            
            # Test validation
            meets, msg = furnace_val.meets_furnace_requirement(30, "TESTCODE")
            assert meets == True
            meets, msg = furnace_val.meets_furnace_requirement(20, "TESTCODE")
            assert meets == False
            print("  ✅ Furnace level validation works")
            
            # Cleanup
            self.cursor.execute("DELETE FROM gift_code_requirements WHERE code = 'TESTCODE'")
            self.db.commit()
        except Exception as e:
            print(f"  ❌ Furnace Validator test failed: {e}")
            all_passed = False
        
        # Test 3: Priority Queue
        print("\n🧪 Test 3: Redemption Priority Queue")
        try:
            priority_queue = RedemptionPriorityQueue(self.db)
            
            # Test priority setting
            priority_queue.set_alliance_priority(999, 123, 1)
            priority = priority_queue.get_alliance_priority(999, 123)
            assert priority == 1
            print("  ✅ Priority setting works")
            
            # Test sorting
            sorted_alliances = priority_queue.get_sorted_alliances(999, [123, 456])
            assert sorted_alliances[0][0] == 123  # 123 should be first (higher priority)
            print("  ✅ Alliance sorting works")
            
            # Cleanup
            self.cursor.execute("DELETE FROM alliance_redemption_priority WHERE guild_id = 999")
            self.db.commit()
        except Exception as e:
            print(f"  ❌ Priority Queue test failed: {e}")
            all_passed = False
        
        # Test 4: Error Handler
        print("\n🧪 Test 4: Enhanced Error Handler")
        try:
            error_handler = EnhancedErrorHandler(self.db)
            
            # Test VIP error categorization
            result = error_handler.categorize_error("RECHARGE_MONEY_VIP")
            assert result['category'] == 'VIP_REQUIRED'
            assert result['is_permanent'] == True
            print("  ✅ VIP error categorization works")
            
            # Test furnace error categorization
            result = error_handler.categorize_error("ERR_CDK_STOVE_LV")
            assert result['category'] == 'FURNACE_LEVEL_REQUIRED'
            print("  ✅ Furnace error categorization works")
            
            # Test success categorization
            result = error_handler.categorize_error("SUCCESS")
            assert result['category'] == 'SUCCESS'
            assert result['is_permanent'] == False
            print("  ✅ Success status categorization works")
        except Exception as e:
            print(f"  ❌ Error Handler test failed: {e}")
            all_passed = False
        
        # Results
        print("\n" + "="*60)
        if all_passed:
            print("✅ ALL TESTS PASSED")
            print("="*60)
            return True
        else:
            print("❌ SOME TESTS FAILED")
            print("="*60)
            return False
    
    def backup_and_integrate(self):
        """Full integration process with backup"""
        print("\n" + "="*60)
        print("Starting Full Integration")
        print("="*60)
        
        # Step 1: Backup
        print("\n📦 Step 1: Backup Database")
        if not self.backup_database():
            print("⚠️  Continuing without backup...")
        
        # Step 2: Setup Database
        print("\n📊 Step 2: Setup Database Tables")
        if not self.setup_database():
            print("❌ Database setup failed. Aborting.")
            return False
        
        # Step 3: Test Integration
        print("\n🧪 Step 3: Run Tests")
        if not self.run_tests():
            print("⚠️  Some tests failed, but tables are created.")
        
        # Step 4: Integration Instructions
        print("\n📝 Step 4: Manual Integration Required")
        print("="*60)
        print("""
The database is now ready for v1.4.0 features!

To complete integration, you need to update your cog:

1. Open: cogs/manage_giftcode.py

2. Add import at the top:
   from gift_code_v14_enhancements import GiftCodeV14Integrator

3. In __init__ method, add after database setup:
   try:
       from gift_code_v14_enhancements import Gift CodeV14Schema, GiftCodeV14Integrator
       GiftCodeV14Schema.setup_all_tables(self.giftcode_db)
       self.v14 = GiftCodeV14Integrator(self, self.giftcode_db)
       self.logger.info("✨ Gift Code v1.4.0 features enabled")
   except Exception as e:
       self.logger.error(f"Failed to initialize v1.4.0 features: {e}")
       self.v14 = None

4. Update _redeem_for_member method (see GIFTCODE_V1.4.0_INTEGRATION.md)

5. Restart the bot and check logs for "✨ Gift Code v1.4.0 features enabled"

For detailed instructions, see: GIFTCODE_V1.4.0_INTEGRATION.md
        """)
        
        print("="*60)
        print("✅ Integration preparation complete!")
        print("="*60)
        return True
    
    def cleanup(self):
        """Clean up resources"""
        if self.db:
            try:
                self.db.close()
                print("\n✅ Database connection closed")
            except Exception:
                pass


def main():
    """Main entry point"""
    integrator = V14Integrator()
    
    try:
        if len(sys.argv) < 2:
            print(__doc__)
            return
        
        action = sys.argv[1].lower()
        
        if action == '--setup-database':
            integrator.setup_database()
        
        elif action == '--test':
            integrator.run_tests()
        
        elif action == '--status':
            integrator.check_integration_status()
        
        elif action == '--auto-integrate':
            integrator.backup_and_integrate()
        
        else:
            print(f"Unknown action: {action}")
            print(__doc__)
    
    finally:
        integrator.cleanup()


if __name__ == "__main__":
    main()
