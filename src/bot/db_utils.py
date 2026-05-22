"""
Database path utilities for consistent database access across all cogs.
Ensures database files are found correctly in both local and production environments.
"""
from pathlib import Path
import sqlite3
import os

def get_db_path(db_name: str) -> str:
    """
    Get the absolute path to a database file.
    
    Args:
        db_name: Name of the database file (e.g., 'settings.sqlite', 'users.sqlite')
    
    Returns:
        Absolute path to the database file as a string
    """
    # Get the repository root (parent of the directory containing this file)
    repo_root = Path(__file__).resolve().parent.parent.parent
    db_dir = repo_root / "db"
    
    # Ensure db directory exists
    try:
        db_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    
    return str(db_dir / db_name)


def get_db_dir() -> Path:
    """
    Get the absolute path to the db directory.
    
    Returns:
        Path object pointing to the db directory
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    db_dir = repo_root / "db"
    
    # Ensure db directory exists
    try:
        db_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    
    return db_dir


def get_db_connection(db_name: str, **kwargs) -> sqlite3.Connection:
    """
    Get a SQLite database connection using the correct absolute path.
    
    Args:
        db_name: Name of the database file (e.g., 'settings.sqlite', 'users.sqlite')
        **kwargs: Additional arguments to pass to sqlite3.connect()
    
    Returns:
        SQLite connection object
    """
    db_path = get_db_path(db_name)
    return sqlite3.connect(db_path, **kwargs)
