"""
Shared pytest fixtures.

WHY THIS FILE EXISTS:
- config.py raises ValueError at import time if GEMINI_API_KEY is unset
  (by design — fail fast on misconfiguration). Tests never call the real
  Gemini API, but they DO import modules that import config, so we set a
  dummy key before anything else gets imported.
- db.py runs init_db() against leads.db the moment it's imported, and
  every db.py function reads/writes that same hardcoded file. Without
  isolation, running the test suite would create/pollute a real
  leads.db in whatever directory pytest is run from, and tests could
  see leftover data from previous runs (or from your own manual testing).
  The isolate_db fixture below points db.DB_FILENAME at a fresh
  temp file for every single test.
"""
import os

# Must happen before any project module is imported anywhere in the
# test session, since config.py validates this at import time.
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-used-in-tests")

import pytest
import db


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    """
    Point db.DB_FILENAME at a throwaway file for the duration of each
    test, so tests never touch the real leads.db and never leak state
    between tests.
    """
    test_db_path = tmp_path / "test_leads.db"
    monkeypatch.setattr(db, "DB_FILENAME", str(test_db_path))
    db.init_db()
    yield
