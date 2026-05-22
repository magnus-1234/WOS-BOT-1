"""
Script to grant global administrator access to a Discord user.
This adds the user to the admin table with is_initial=1 (global admin).
"""
import sqlite3
from pathlib import Path

def grant_global_admin(user_id):
    """
    Grant global administrator access to a user.
    
    Args:
        user_id: Discord user ID (integer)
    """
    # Get the database path
    repo_root = Path(__file__).resolve().parent
    db_path = repo_root / "db" / "settings.sqlite"
    
    # Ensure db directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Connect to database
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    # Create admin table if it doesn't exist
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY,
            is_initial INTEGER DEFAULT 0
        )
    """)
    
    # Check if user already exists
    cursor.execute("SELECT id, is_initial FROM admin WHERE id = ?", (user_id,))
    existing = cursor.fetchone()
    
    if existing:
        if existing[1] == 1:
            print(f"✅ User {user_id} is already a global administrator!")
        else:
            # Update to global admin
            cursor.execute("UPDATE admin SET is_initial = 1 WHERE id = ?", (user_id,))
            conn.commit()
            print(f"✅ User {user_id} has been upgraded to global administrator!")
    else:
        # Insert new global admin
        cursor.execute("INSERT INTO admin (id, is_initial) VALUES (?, 1)", (user_id,))
        conn.commit()
        print(f"✅ User {user_id} has been added as a global administrator!")
    
    # Verify the change
    cursor.execute("SELECT id, is_initial FROM admin WHERE id = ?", (user_id,))
    result = cursor.fetchone()
    print(f"\n📊 Current status:")
    print(f"   User ID: {result[0]}")
    print(f"   Global Admin: {'Yes' if result[1] == 1 else 'No'}")
    
    conn.close()

def list_current_admins():
    """List all current administrators in the database."""
    repo_root = Path(__file__).resolve().parent
    db_path = repo_root / "db" / "settings.sqlite"
    
    if not db_path.exists():
        print("⚠️  Database not found. No administrators exist yet.")
        return
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT id, is_initial FROM admin ORDER BY is_initial DESC, id ASC")
        admins = cursor.fetchall()
        
        if not admins:
            print("⚠️  No administrators found in database.")
        else:
            print("📋 Current Administrators:")
            print("-" * 60)
            for admin_id, is_initial in admins:
                admin_type = "Global Admin" if is_initial == 1 else "Regular Admin"
                print(f"   User ID: {admin_id} - {admin_type}")
            print("-" * 60)
    except Exception as e:
        print(f"⚠️  Could not read admin table: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    print("=" * 60)
    print("Global Administrator Access Grant Script")
    print("=" * 60)
    print()
    
    # Show current admins
    list_current_admins()
    print()
    
    # Instructions
    print("📝 To find your Discord User ID:")
    print("   1. Enable Developer Mode in Discord (Settings > Advanced)")
    print("   2. Right-click your username and select 'Copy User ID'")
    print()
    
    # Get user ID from input
    user_id_input = input("Enter your Discord User ID: ").strip()
    
    try:
        user_id = int(user_id_input)
        print()
        grant_global_admin(user_id)
        print()
        print("=" * 60)
        print("✨ You can now use all features in /setting!")
        print("=" * 60)
    except ValueError:
        print("❌ Error: Please enter a valid numeric Discord User ID")
