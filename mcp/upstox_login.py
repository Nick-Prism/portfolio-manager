"""
mcp/upstox_login.py — One-time daily script to get Upstox access token.

Upstox access tokens expire daily. Run this each morning before starting the bot.

Usage:
    python mcp/upstox_login.py
"""

import os
import re
import webbrowser
from pathlib import Path

try:
    from dotenv import load_dotenv, set_key
    load_dotenv()
except ImportError:
    print("ERROR: python-dotenv not installed.")
    raise SystemExit(1)

import httpx

ENV_PATH   = Path(__file__).resolve().parent.parent / ".env"
API_KEY    = os.getenv("UPSTOX_API_KEY", "").strip()
API_SECRET = os.getenv("UPSTOX_API_SECRET", "").strip()
REDIRECT   = os.getenv("UPSTOX_REDIRECT_URL", "https://zeta-portfolio.duckdns.org").strip()

if not API_KEY or not API_SECRET:
    print("\nERROR: UPSTOX_API_KEY and UPSTOX_API_SECRET must be set in .env")
    print("Get them from: https://developer.upstox.com\n")
    raise SystemExit(1)

login_url = (
    f"https://api.upstox.com/v2/login/authorization/dialog"
    f"?response_type=code&client_id={API_KEY}&redirect_uri={REDIRECT}"
)

print("\n" + "=" * 60)
print("  Upstox Login — Daily Access Token Setup")
print("=" * 60)
print(f"\n1. Opening login URL in your browser...")
print(f"   {login_url}\n")
webbrowser.open(login_url)

print("2. Log in with your Upstox credentials + TOTP")
print("3. After login, Upstox redirects to a URL like:")
print("   https://zeta-portfolio.duckdns.org/?code=XXXXXXXX")
print("   Copy the full URL from the browser address bar.\n")

raw  = input("   > ").strip()
match = re.search(r"[?&]code=([A-Za-z0-9_\-]+)", raw)
code  = match.group(1) if match else raw

if not code:
    print("\nERROR: Could not parse authorization code.")
    raise SystemExit(1)

try:
    resp = httpx.post(
        "https://api.upstox.com/v2/login/authorization/token",
        data={
            "code":          code,
            "client_id":     API_KEY,
            "client_secret": API_SECRET,
            "redirect_uri":  REDIRECT,
            "grant_type":    "authorization_code",
        },
        headers={"Accept": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    access_token = resp.json()["access_token"]
except Exception as e:
    print(f"\nERROR: Failed to get access token: {e}")
    raise SystemExit(1)

set_key(str(ENV_PATH), "UPSTOX_ACCESS_TOKEN", access_token)

print(f"\n✅  Access token saved to .env")
print(f"   UPSTOX_ACCESS_TOKEN={access_token[:8]}...{access_token[-4:]}")
print("\n   You can now run: python main.py --run-once")
print("   Token expires tomorrow — run this script again each morning.\n")
