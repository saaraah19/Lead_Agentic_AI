"""
Shared pytest fixtures.
"""
import sys
import os
import pytest
import db
from pathlib import Path

# Add project root to Python path so that `import db`, `import agent`, etc. work
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Set dummy API key before any real code tries to use Gemini
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-used-in-tests")


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    """
    Point db.DB_FILENAME at a throwaway file for each test.
    This ensures each test gets a fresh, isolated database.
    """
    # Create a temporary file path for this test
    test_db_path = tmp_path / "test_leads.db"

    # Override the module's DB_FILENAME
    monkeypatch.setattr(db, "DB_FILENAME", str(test_db_path))

    # Ensure the database is initialised with the new path
    # (delete any leftover file from a previous run)
    if test_db_path.exists():
        test_db_path.unlink()
    db.init_db()

    yield  # Test runs here

    # Cleanup: remove the test database file after the test
    if test_db_path.exists():
        test_db_path.unlink()