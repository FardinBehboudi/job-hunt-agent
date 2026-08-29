"""
ms_auth.py — MSAL token management for Microsoft Graph API.

First-time setup: run  python tracking/get_token.py
Subsequent runs:  access token is auto-refreshed from MS_REFRESH_TOKEN in .env
"""

import os
import threading
import time
import logging
from pathlib import Path

import msal
from dotenv import load_dotenv, set_key

load_dotenv()
log = logging.getLogger(__name__)

CLIENT_ID = "7924cb25-11f6-4052-a3c4-6b082d6efd58"
AUTHORITY  = "https://login.microsoftonline.com/common"
SCOPES     = ["Mail.ReadWrite"]

_ENV_FILE = Path(__file__).parent.parent / ".env"

# In-memory access-token cache. Without this, every single Graph API call
# (e.g. each email move in a batch) triggered its own network round-trip to
# Azure AD and, whenever Microsoft rotated the refresh token, its own rewrite
# of .env — under a tight sequential loop of 50+ calls this produced repeated
# needless token exchanges and, at least once, a WinError 5 on the .env
# rename when something else touched the file mid-write.
_token_lock = threading.Lock()
_cached_token: str | None = None
_cached_expires_at: float = 0.0
# Refresh a bit early so a token doesn't expire mid-request.
_EXPIRY_SKEW_SECONDS = 60


def _app() -> msal.PublicClientApplication:
    return msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)


def get_access_token() -> str:
    """Return a valid access token, reusing the cached one until it's near expiry."""
    global _cached_token, _cached_expires_at

    with _token_lock:
        if _cached_token and time.time() < _cached_expires_at:
            return _cached_token

        refresh_token = os.getenv("MS_REFRESH_TOKEN")
        if not refresh_token:
            raise EnvironmentError(
                "MS_REFRESH_TOKEN not set. Run  python tracking/get_token.py  first."
            )

        app = _app()
        result = app.acquire_token_by_refresh_token(refresh_token, scopes=SCOPES)

        if "access_token" not in result:
            raise RuntimeError(
                f"Token refresh failed: {result.get('error_description', result)}"
            )

        # Persist the new refresh token if Microsoft rotated it. A failure to
        # write .env (e.g. a transient file lock) shouldn't fail the token
        # acquisition itself — the access token is already valid in memory,
        # and the next successful refresh will retry the write.
        new_rt = result.get("refresh_token")
        if new_rt and new_rt != refresh_token:
            try:
                set_key(str(_ENV_FILE), "MS_REFRESH_TOKEN", new_rt)
                os.environ["MS_REFRESH_TOKEN"] = new_rt
                log.debug("Refresh token rotated and saved.")
            except OSError as exc:
                log.warning("Could not persist rotated refresh token to .env: %s", exc)

        _cached_token = result["access_token"]
        _cached_expires_at = time.time() + int(result.get("expires_in", 3600)) - _EXPIRY_SKEW_SECONDS
        return _cached_token


def device_code_flow() -> str:
    """
    Interactive one-time auth via device code.
    Prints a URL + code for the user to visit, then waits for completion.
    Saves MS_REFRESH_TOKEN to .env and returns the access token.
    """
    app = _app()
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Device flow initiation failed: {flow}")

    print("\n" + "=" * 60)
    print(flow["message"])          # "Go to https://microsoft.com/devicelogin and enter code XXXXX"
    print("=" * 60 + "\n")

    result = app.acquire_token_by_device_flow(flow)   # blocks until user completes auth

    if "access_token" not in result:
        raise RuntimeError(
            f"Device flow failed: {result.get('error_description', result)}"
        )

    refresh_token = result.get("refresh_token", "")
    if refresh_token:
        set_key(str(_ENV_FILE), "MS_REFRESH_TOKEN", refresh_token)
        os.environ["MS_REFRESH_TOKEN"] = refresh_token
        print("[OK] Refresh token saved to .env")
    else:
        print("[WARN] No refresh token received — offline_access scope may not have been granted.")

    return result["access_token"]
