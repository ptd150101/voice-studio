#!/usr/bin/env python3
"""Self-check for the revoke flow: gen → activate → verify → revoke → verify revoked.

Run:
    set LICENSE_WORKER_URL=https://voice-studio.dnh30701.workers.dev
    set LICENSE_ADMIN_KEY=<admin secret>
    uv run python packaging/test_revoke.py

Exit 0 = pass, non-zero = fail.
"""
import json, os, sys, time, uuid

try:
    import requests
except ImportError:
    print("Install requests: pip install requests")
    sys.exit(1)

WORKER_URL = os.environ.get("LICENSE_WORKER_URL", "https://voice-studio.dnh30701.workers.dev")
ADMIN_KEY  = os.environ.get("LICENSE_ADMIN_KEY", "")
if not ADMIN_KEY:
    print("Set LICENSE_ADMIN_KEY env var")
    sys.exit(2)

ADMIN_H = {"X-Admin-Key": ADMIN_KEY}


def _hwid():
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, "test-revoke-hwid"))


def _post(path, body, timeout=15):
    r = requests.post(f"{WORKER_URL}{path}", json=body, timeout=timeout)
    return r.status_code, r.json()


def main():
    hwid = _hwid()
    failures = []

    # 1. Gen a short-lived key (120s so expiry doesn't race the test).
    s, data = _post("/admin/gen", {"ttl_seconds": 120, "count": 1})
    assert s == 200 and data.get("ok"), f"gen failed: {s} {data}"
    key = data["keys"][0]
    print(f"[1] gen key: {key}")

    # 2. Activate + verify ok.
    s, data = _post("/activate", {"license_key": key, "hwid": hwid})
    assert s == 200 and data.get("ok"), f"activate failed: {s} {data}"
    token = data["token"]
    print(f"[2] activated, token issued")

    s, data = _post("/verify", {"token": token, "hwid": hwid})
    assert s == 200 and data.get("ok"), f"verify pre-revoke failed: {s} {data}"
    print(f"[3] verify pre-revoke ok")

    # 3. Revoke via admin endpoint.
    r = requests.post(f"{WORKER_URL}/admin/revoke",
                      json={"license_key": key}, headers=ADMIN_H, timeout=15)
    assert r.status_code == 200 and r.json().get("ok"), f"revoke failed: {r.status_code} {r.text}"
    print(f"[4] revoked via /admin/revoke")

    # 4. Poll verify until revoked (KV eventual consistency, max 90s).
    revoked_seen = False
    t0 = time.time()
    while time.time() - t0 < 90:
        s, data = _post("/verify", {"token": token, "hwid": hwid})
        if s == 403 and data.get("error") == "revoked":
            revoked_seen = True
            break
        time.sleep(2)
    if not revoked_seen:
        failures.append(f"verify never returned revoked (last={s} {data})")
    else:
        print(f"[5] verify returned revoked after {int(time.time()-t0)}s")

    # 5. Unauthorized revoke → 401.
    r = requests.post(f"{WORKER_URL}/admin/revoke",
                      json={"license_key": key}, headers={"X-Admin-Key": "wrong"}, timeout=15)
    if r.status_code != 401:
        failures.append(f"unauth revoke: expected 401, got {r.status_code}")
    else:
        print(f"[6] unauthorized revoke → 401 ok")

    # 6. Revoke non-existent key → 404.
    r = requests.post(f"{WORKER_URL}/admin/revoke",
                      json={"license_key": "deadbeefdeadbeefdeadbeefdeadbeef"}, headers=ADMIN_H, timeout=15)
    if r.status_code != 404:
        failures.append(f"missing key revoke: expected 404, got {r.status_code}")
    else:
        print(f"[7] missing key revoke → 404 ok")

    # 7. Client check() returns REVOKED and cache is cleared.
    #    Import the client bundled in packaging/ — it points at the same Worker.
    sys.path.insert(0, os.path.dirname(__file__))
    import client as lic
    lic.SERVER_URL = WORKER_URL
    lic.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    lic.CACHE_FILE.write_text(json.dumps({"token": token, "hwid": hwid}))
    state, _ = lic.check()
    if state != lic.LicenseState.REVOKED:
        failures.append(f"client check() expected REVOKED, got {state}")
    else:
        print(f"[8] client check() → REVOKED")
    if lic.CACHE_FILE.exists():
        failures.append("client cache not cleared after revoke")
    else:
        print(f"[9] client cache cleared")

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\nALL PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()