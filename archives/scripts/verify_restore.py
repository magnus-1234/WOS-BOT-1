"""
Verification script to check if all critical files are present after restore
"""
import os
from pathlib import Path

def check_file_exists(filepath, description):
    """Check if a file exists and print status"""
    exists = os.path.exists(filepath)
    status = "✅" if exists else "❌"
    print(f"{status} {description}: {filepath}")
    return exists

def check_directory_exists(dirpath, description):
    """Check if a directory exists and print status"""
    exists = os.path.isdir(dirpath)
    status = "✅" if exists else "❌"
    print(f"{status} {description}: {dirpath}")
    return exists

def main():
    print("=" * 80)
    print("DISCORD BOT RESTORE VERIFICATION")
    print("=" * 80)
    print()
    
    base_dir = Path(__file__).parent
    all_checks_passed = True
    
    # Check critical Python files
    print("📄 CRITICAL PYTHON FILES")
    print("-" * 80)
    all_checks_passed &= check_file_exists(base_dir / "app.py", "Main application")
    all_checks_passed &= check_file_exists(base_dir / "requirements.txt", "Requirements")
    all_checks_passed &= check_file_exists(base_dir / "bot_config.py", "Bot configuration")
    print()
    
    # Check configuration files
    print("⚙️  CONFIGURATION FILES")
    print("-" * 80)
    all_checks_passed &= check_file_exists(base_dir / ".env", "Environment variables")
    all_checks_passed &= check_file_exists(base_dir / "bot_token.txt", "Bot token")
    all_checks_passed &= check_file_exists(base_dir / "creds.json", "Google credentials")
    all_checks_passed &= check_file_exists(base_dir / "mongo_uri.txt", "MongoDB URI")
    print()
    
    # Check directories
    print("📁 CRITICAL DIRECTORIES")
    print("-" * 80)
    all_checks_passed &= check_directory_exists(base_dir / "cogs", "Cogs directory")
    all_checks_passed &= check_directory_exists(base_dir / "db", "Database directory")
    all_checks_passed &= check_directory_exists(base_dir / "scripts", "Scripts directory")
    all_checks_passed &= check_directory_exists(base_dir / "models", "Models directory")
    print()
    
    # Check database files
    print("💾 DATABASE FILES")
    print("-" * 80)
    db_dir = base_dir / "db"
    all_checks_passed &= check_file_exists(db_dir / "alliance.sqlite", "Alliance database")
    all_checks_passed &= check_file_exists(db_dir / "attendance.sqlite", "Attendance database")
    all_checks_passed &= check_file_exists(db_dir / "giftcode.sqlite", "Gift code database")
    all_checks_passed &= check_file_exists(db_dir / "settings.sqlite", "Settings database")
    all_checks_passed &= check_file_exists(db_dir / "mongo_adapters.py", "MongoDB adapters")
    all_checks_passed &= check_file_exists(base_dir / "reminders.db", "Reminders database")
    print()
    
    # Check deployment files
    print("🚀 DEPLOYMENT FILES")
    print("-" * 80)
    all_checks_passed &= check_file_exists(base_dir / "Dockerfile", "Dockerfile")
    all_checks_passed &= check_file_exists(base_dir / "render.yaml", "Render config")
    all_checks_passed &= check_file_exists(base_dir / "Procfile", "Procfile")
    all_checks_passed &= check_file_exists(base_dir / "runtime.txt", "Runtime specification")
    print()
    
    # Summary
    print("=" * 80)
    if all_checks_passed:
        print("✅ ALL CHECKS PASSED - Restore completed successfully!")
    else:
        print("❌ SOME CHECKS FAILED - Please review missing files above")
    print("=" * 80)
    
    return all_checks_passed

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
