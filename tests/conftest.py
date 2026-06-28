"""
Shared pytest fixtures.
"""
import sys
import os
from pathlib import Path
import pytest 

# Ajouter la racine du projet au PYTHONPATH
root = Path(__file__).parent.parent
sys.path.insert(0, str(root))

# Définir des variables d'environnement fictives pour les tests
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-used-in-tests")
os.environ.setdefault("GOOGLE_CREDENTIALS_JSON", '{"type":"service_account","project_id":"test"}')
os.environ.setdefault("GOOGLE_CALENDAR_ID", "test@calendar.com")

import db

@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    """
    Point db.DB_FILENAME at a throwaway file for each test.
    This ensures each test gets a fresh, isolated database.
    """
    test_db_path = tmp_path / "test_leads.db"
    monkeypatch.setattr(db, "DB_FILENAME", str(test_db_path))
    if test_db_path.exists():
        test_db_path.unlink()
    db.init_db()
    yield
    if test_db_path.exists():
        test_db_path.unlink()
