"""
mcp/zerodha_login.py — One-time script to get your Zerodha access token.

Zerodha access tokens expire every day at 6 AM IST.
Run this script once each morning before starting the bot.

Usage:
    python mcp/zerodha_login.py
"""

import os
import re
import webbrowser
from pathlib import Path

try:
    from dotenv import load_dotenv, set_key
    load_dotenv()
except ImportError:
    print("ERROR: python-dotenv not installed. Run: pip install python-dotenv")
    raise SystemExit(1)

from kiteconnect import KiteConnect

ENV_PATH   = Path(__file__).resolve().parent.parent / ".env"
API_KEY    = os.getenv("ZERODHA_API_KEY", "").strip()
API_SECRET = os.getenv("ZERODHA_API_SECRET", "").strip()

if not API_KEY or not API_SECRET:
    print("\nERROR: ZERODHA_API_KEY and ZERODHA_API_SECRET must be set in .env")
    print("Get them from: https://developers.kite.trade/apps\n")
    raise SystemExit(1)

kite      = KiteConnect(api_key=API_KEY)
login_url = kite.login_url()

print("\n" + "=" * 60)
print("  Zerodha Login — One-Time Access Token Setup")
print("  Plan: Personal (free)")
print("=" * 60)
print(f"\n1. Opening login URL in your browser...")
print(f"   {login_url}\n")
webbrowser.open(login_url)

print("2. Log in with your Zerodha credentials + TOTP/PIN")
print("3. After login, Zerodha redirects your browser to a URL like:")
print("   https://zeta-portfolio.duckdns.org/?request_token=XXXXXXXX&action=login&status=success")
print("   The browser will show a security warning or ERR_CONNECTION_REFUSED")
print("   — that is completely normal. Just copy the full URL from the address bar.")
print("\n   Paste the FULL URL from the browser address bar below:\n")

raw = input("   > ").strip()

match         = re.search(r"request_token=([A-Za-z0-9]+)", raw)
request_token = match.group(1) if match else raw

if not request_token:
    print("\nERROR: Could not parse request_token from input.")
    raise SystemExit(1)

try:
    session      = kite.generate_session(request_token, api_secret=API_SECRET)
    access_token = session["access_token"]
except Exception as e:
    print(f"\nERROR: Failed to generate session: {e}")
    print("Make sure ZERODHA_API_SECRET is correct and the request_token is fresh.")
    raise SystemExit(1)

set_key(str(ENV_PATH), "ZERODHA_ACCESS_TOKEN", access_token)

print(f"\n✅  Access token saved to .env")
print(f"   ZERODHA_ACCESS_TOKEN={access_token[:8]}...{access_token[-4:]}")
print("\n   You can now run: python main.py --run-once")
print("   Token expires at 6 AM IST tomorrow — run this script again then.\n")
