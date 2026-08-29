# tests/test_email_admin.py
import threading
import time
from unittest.mock import patch

import pytest
from flask import Flask


@pytest.fixture
def app_client(temp_db, monkeypatch):
    from dashboard.email_admin import email_admin_bp, db as email_admin_db
    monkeypatch.setattr("dashboard.email_admin.db", temp_db)
    app = Flask(__name__)
    app.register_blueprint(email_admin_bp)
    return app.test_client()


def test_approve_batch_rejects_concurrent_run(app_client, temp_db):
    from dashboard import email_admin

    release = threading.Event()
    entered = threading.Event()

    def slow_approve_batch(ids):
        entered.set()
        release.wait(timeout=5)
        return {"succeeded": len(ids), "failed": 0}

    with patch("tracking.email_executor.approve_batch", side_effect=slow_approve_batch):
        results = {}

        def first_call():
            results["first"] = app_client.post("/email-admin/api/approve-batch", json={"ids": [1]})

        t = threading.Thread(target=first_call)
        t.start()
        assert entered.wait(timeout=5)  # first request is now inside the batch

        second = app_client.post("/email-admin/api/approve-batch", json={"ids": [2]})
        assert second.status_code == 409
        assert second.get_json()["ok"] is False

        release.set()
        t.join(timeout=5)
        assert results["first"].status_code == 200

    # lock must be released for the next real request
    assert email_admin._batch_lock.acquire(blocking=False)
    email_admin._batch_lock.release()
