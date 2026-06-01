# tests/conftest.py
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Isolated SQLite DB for each test. Patches _DB_PATH in dedup.db."""
    import dedup.db as db_module
    db_path = tmp_path / "test_jobhunt.db"
    monkeypatch.setattr(db_module, "_DB_PATH", db_path)
    db_module.init_db()
    return db_module


@pytest.fixture
def sample_application(temp_db):
    """Insert one application and return its id."""
    temp_db.log_application(
        {"company": "Acme Corp", "title": "Engineer", "url": "https://acme.com/jobs/1"},
        status="Applied",
    )
    with temp_db._conn() as conn:
        row = conn.execute(
            "SELECT id FROM applications WHERE job_url=?", ("https://acme.com/jobs/1",)
        ).fetchone()
    return row["id"]
