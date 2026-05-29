"""
get_token.py — One-time OAuth2 authentication for Microsoft Graph API.

Run ONCE from the project root:
    python tracking/get_token.py

It will print a URL and a code. Open the URL in your browser, enter the code,
and sign in with f_behboud@hotmail.com. When done, the refresh token is saved
to .env automatically.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tracking.ms_auth import device_code_flow

if __name__ == "__main__":
    print("Starting Microsoft OAuth2 device code flow...")
    print("Sign in with: f_behboud@hotmail.com\n")
    try:
        token = device_code_flow()
        print("\n[OK] Authentication successful!")
        print("  MS_REFRESH_TOKEN has been saved to .env")
        print("  You can now run the email processor normally.")
    except Exception as exc:
        print(f"\n[FAIL] Authentication failed: {exc}")
        sys.exit(1)
