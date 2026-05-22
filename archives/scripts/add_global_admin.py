"""
Add Global Admin to MongoDB
This script adds a user as a global administrator in MongoDB.
Run this on Render or any environment using MongoDB.
"""

import os
import asyncio
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

async def add_global_admin():
    """Add a user as a global administrator in MongoDB."""
    
    print("=" * 60)
    print("Add Global Administrator to MongoDB")
    print("=" * 60)
    
    # Get user ID
    user_id_input = input("\nEnter your Discord User ID: ").strip()
    
    try:
        user_id = int(user_id_input)
    except ValueError:
        print("❌ Invalid user ID. Must be a number.")
        return
    
    print(f"\n📝 User ID: {user_id}")
    confirm = input("Is this correct? (yes/no): ").strip().lower()
    
    if confirm not in ['yes', 'y']:
        print("❌ Cancelled.")
        return
    
    # Import MongoDB adapters
    try:
        from db.mongo_adapters import mongo_enabled, AdminsAdapter
        
        if not mongo_enabled():
            print("\n❌ MongoDB is not enabled!")
            print("Make sure MONGODB_URI is set in your environment variables.")
            return
        
        print("\n✅ MongoDB connection verified")
        
        # Check if user already exists
        existing_admin = AdminsAdapter.get(user_id)
        
        if existing_admin:
            print(f"\n⚠️  User {user_id} is already an admin")
            print(f"Current status: is_initial = {existing_admin.get('is_initial', 0)}")
            
            if existing_admin.get('is_initial') == 1:
                print("✅ User is already a global administrator!")
                return
            
            update = input("\nUpgrade to global administrator? (yes/no): ").strip().lower()
            if update not in ['yes', 'y']:
                print("❌ Cancelled.")
                return
        
        # Add/update user as global admin
        print(f"\n🔄 Adding user {user_id} as global administrator...")
        
        success = AdminsAdapter.upsert(user_id, is_initial=1)
        
        if success:
            print("\n✅ SUCCESS!")
            print(f"User {user_id} has been added as a global administrator.")
            print("\nYou can now use the following commands:")
            print("  • /syncdata")
            print("  • /checkauth")
            print("  • /verifyscope")
        else:
            print("\n❌ Failed to add user as global administrator.")
            print("Check the logs for more details.")
        
    except ImportError as e:
        print(f"\n❌ Failed to import MongoDB adapters: {e}")
        print("Make sure you're running this in the correct environment.")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

def add_to_sqlite():
    """Add a user as a global administrator in SQLite (for local testing)."""
    import sqlite3
    from pathlib import Path
    
    print("\n" + "=" * 60)
    print("Add Global Administrator to SQLite (Local)")
    print("=" * 60)
    
    # Get user ID
    user_id_input = input("\nEnter your Discord User ID: ").strip()
    
    try:
        user_id = int(user_id_input)
    except ValueError:
        print("❌ Invalid user ID. Must be a number.")
        return
    
    print(f"\n📝 User ID: {user_id}")
    confirm = input("Is this correct? (yes/no): ").strip().lower()
    
    if confirm not in ['yes', 'y']:
        print("❌ Cancelled.")
        return
    
    try:
        # Get database path
        repo_root = Path(__file__).resolve().parent
        db_dir = repo_root / "db"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "settings.sqlite"
        
        print(f"\n📂 Database: {db_path}")
        
        # Connect to database
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        # Create table if it doesn't exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin (
                id INTEGER PRIMARY KEY,
                is_initial INTEGER DEFAULT 1
            )
        """)
        
        # Check if user already exists
        cursor.execute("SELECT id, is_initial FROM admin WHERE id = ?", (user_id,))
        existing = cursor.fetchone()
        
        if existing:
            print(f"\n⚠️  User {user_id} is already an admin")
            print(f"Current status: is_initial = {existing[1]}")
            
            if existing[1] == 1:
                print("✅ User is already a global administrator!")
                conn.close()
                return
        
        # Add/update user
        print(f"\n🔄 Adding user {user_id} as global administrator...")
        
        cursor.execute(
            "INSERT OR REPLACE INTO admin (id, is_initial) VALUES (?, ?)",
            (user_id, 1)
        )
        conn.commit()
        conn.close()
        
        print("\n✅ SUCCESS!")
        print(f"User {user_id} has been added as a global administrator in SQLite.")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

def main():
    """Main function to choose between MongoDB and SQLite."""
    print("\n" + "=" * 60)
    print("Global Administrator Setup")
    print("=" * 60)
    print("\nChoose database:")
    print("1. MongoDB (for Render/Production)")
    print("2. SQLite (for Local/Development)")
    print("3. Both")
    
    choice = input("\nEnter choice (1/2/3): ").strip()
    
    if choice == "1":
        asyncio.run(add_global_admin())
    elif choice == "2":
        add_to_sqlite()
    elif choice == "3":
        add_to_sqlite()
        print("\n" + "=" * 60)
        asyncio.run(add_global_admin())
    else:
        print("❌ Invalid choice.")

if __name__ == "__main__":
    main()
