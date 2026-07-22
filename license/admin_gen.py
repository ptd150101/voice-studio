# OmniVoice License Admin Tool
# Gen license key + push to Cloudflare Worker

import json, os, sys
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    print("Install requests: pip install requests")
    sys.exit(1)

WORKER_URL = os.environ.get("LICENSE_WORKER_URL", "https://omnivoice-license.YOUR-SUBDOMAIN.workers.dev")
ADMIN_KEY = os.environ.get("LICENSE_ADMIN_KEY", "CHANGE-ME-TO-A-RANDOM-STRING")


def generate(days: int = 30, count: int = 1, ttl_seconds: int = None):
    """Generate license keys via Worker admin API."""
    body = {"days": days, "count": count}
    if ttl_seconds is not None:
        body["ttl_seconds"] = ttl_seconds
        body.pop("days")
    resp = requests.post(
        f"{WORKER_URL}/admin/gen",
        json=body,
        headers={"X-Admin-Key": ADMIN_KEY},
        timeout=15,
    )
    if not resp.ok:
        print(f"Error: {resp.status_code} {resp.text}")
        return None
    data = resp.json()
    if not data.get("ok"):
        print(f"API error: {data}")
        return None
    return data


def list_keys():
    """List all license keys."""
    resp = requests.get(
        f"{WORKER_URL}/admin/list",
        headers={"X-Admin-Key": ADMIN_KEY},
        timeout=15,
    )
    if not resp.ok:
        print(f"Error: {resp.status_code} {resp.text}")
        return
    data = resp.json()
    if not data.get("ok"):
        print(f"API error: {data}")
        return
    for k in data["keys"]:
        expired = "EXPIRED" if k["expires_at"] < __import__("time").time() else "ACTIVE"
        hwid = k.get("hwid") or "unused"
        print(f"  {k['key']}  {expired}  hwid={hwid}  activated={k.get('activation_count',0)}x")


def revoke(key: str):
    """Revoke a license key (set revoked=true in KV directly)."""
    # Worker doesn't have a revoke endpoint - use direct KV manipulation
    print("Not implemented via Worker. Edit KV manually in Cloudflare Dashboard.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="OmniVoice License Admin")
    parser.add_argument("action", choices=["gen", "list"], help="Action")
    parser.add_argument("--days", type=int, default=30, help="License duration in days")
    parser.add_argument("--count", type=int, default=1, help="Number of keys to generate")
    parser.add_argument("--ttl", type=int, dest="ttl_seconds", default=None, help="TTL in seconds (override days, for testing)")
    args = parser.parse_args()

    if args.action == "gen":
        seconds = args.ttl_seconds
        result = generate(args.days, args.count, ttl_seconds=seconds)
        if result:
            print(f"\nGenerated {len(result['keys'])} key(s) - expires at {datetime.fromtimestamp(result['expires_at'], tz=timezone.utc).isoformat()}")
            for k in result["keys"]:
                print(f"  LICENSE KEY: {k}")
                print(f"  (Send this to your customer)")
    elif args.action == "list":
        list_keys()
