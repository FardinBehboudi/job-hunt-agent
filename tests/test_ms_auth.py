# tests/test_ms_auth.py
from unittest.mock import MagicMock, patch


def _reset_cache():
    from tracking import ms_auth
    ms_auth._cached_token = None
    ms_auth._cached_expires_at = 0.0


def test_get_access_token_caches_between_calls(monkeypatch):
    from tracking import ms_auth
    _reset_cache()
    monkeypatch.setenv("MS_REFRESH_TOKEN", "rt-1")

    mock_app = MagicMock()
    mock_app.acquire_token_by_refresh_token.return_value = {
        "access_token": "at-1", "expires_in": 3600,
    }
    monkeypatch.setattr(ms_auth, "_app", lambda: mock_app)

    tok1 = ms_auth.get_access_token()
    tok2 = ms_auth.get_access_token()

    assert tok1 == "at-1"
    assert tok2 == "at-1"
    mock_app.acquire_token_by_refresh_token.assert_called_once()  # only refreshed once


def test_get_access_token_refreshes_after_expiry(monkeypatch):
    from tracking import ms_auth
    _reset_cache()
    monkeypatch.setenv("MS_REFRESH_TOKEN", "rt-1")

    mock_app = MagicMock()
    mock_app.acquire_token_by_refresh_token.return_value = {
        "access_token": "at-1", "expires_in": 3600,
    }
    monkeypatch.setattr(ms_auth, "_app", lambda: mock_app)

    ms_auth.get_access_token()
    ms_auth._cached_expires_at = 0.0  # force expiry
    ms_auth.get_access_token()

    assert mock_app.acquire_token_by_refresh_token.call_count == 2


def test_get_access_token_survives_env_write_failure(monkeypatch):
    """A locked/undeletable .env shouldn't fail the token call — the token is
    already valid in memory even if persisting the rotated refresh token fails."""
    from tracking import ms_auth
    _reset_cache()
    monkeypatch.setenv("MS_REFRESH_TOKEN", "rt-1")

    mock_app = MagicMock()
    mock_app.acquire_token_by_refresh_token.return_value = {
        "access_token": "at-1", "expires_in": 3600, "refresh_token": "rt-2",
    }
    monkeypatch.setattr(ms_auth, "_app", lambda: mock_app)
    with patch("tracking.ms_auth.set_key", side_effect=OSError("[WinError 5] Access is denied")):
        tok = ms_auth.get_access_token()

    assert tok == "at-1"


def test_get_access_token_raises_without_refresh_token(monkeypatch):
    from tracking import ms_auth
    _reset_cache()
    monkeypatch.delenv("MS_REFRESH_TOKEN", raising=False)

    try:
        ms_auth.get_access_token()
        assert False, "expected EnvironmentError"
    except EnvironmentError:
        pass
