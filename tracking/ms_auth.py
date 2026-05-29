"""
ms_auth.py — MSAL token management for Microsoft Graph API.

First-time setup: run  python tracking/get_token.py
Subsequent runs:  access token is auto-refreshed from MS_REFRESH_TOKEN in .env
"""

import os
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


def _app() -> msal.PublicClientApplication:
    return msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)


def get_access_token() -> str:
    """Return a valid access token, refreshing via stored refresh token if needed."""
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

    # Persist the new refresh token if Microsoft rotated it
    new_rt = result.get("refresh_token")
    if new_rt and new_rt != refresh_token:
        set_key(str(_ENV_FILE), "MS_REFRESH_TOKEN", new_rt)
        os.environ["MS_REFRESH_TOKEN"] = new_rt
        log.debug("Refresh token rotated and saved.")

    return result["access_token"]


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
