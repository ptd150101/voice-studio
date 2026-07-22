# OmniVoice License Client
# Embedded in compiled exe - verifies subscription online + offline

import json, os, platform, socket, subprocess, sys, time, uuid
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    requests = None


# Config
SERVER_URL = "https://voice-studio.dnh30701.workers.dev"
CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".omnivoice"))
CACHE_FILE = CACHE_DIR / "license.json"
MAX_CLOCK_DRIFT = 3600
NTP_SERVERS = ["pool.ntp.org", "time.google.com", "time.cloudflare.com"]


def _get_hwid() -> str:
    parts = []
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                "wmic cpu get processorid", shell=True, timeout=5, stderr=subprocess.DEVNULL
            ).decode()
            parts.append(out.strip().split("\n")[-1].strip())
    except Exception:
        pass
    try:
        if sys.platform == "win32":
            out = subprocess.check_output("getmac", shell=True, timeout=5, stderr=subprocess.DEVNULL).decode()
            for line in out.splitlines():
                if ":" in line and "-" in line:
                    parts.append(line.split()[0])
                    break
    except Exception:
        pass
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                "wmic volume where driveletter='C' get serialnumber",
                shell=True, timeout=5, stderr=subprocess.DEVNULL,
            ).decode()
            parts.append(out.strip().split("\n")[-1].strip())
    except Exception:
        pass
    parts.append(platform.node())
    parts.append(platform.machine())
    raw = "-".join(filter(None, parts))
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, raw))


def _ntp_time(host: str = "pool.ntp.org", timeout: int = 3) -> Optional[float]:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        req = b"\x1b" + 47 * b"\x00"
        before = time.time()
        sock.sendto(req, (socket.gethostbyname(host), 123))
        resp, _ = sock.recvfrom(1024)
        after = time.time()
        sock.close()
        if len(resp) < 48:
            return None
        int_part = resp[40] << 24 | resp[41] << 16 | resp[42] << 8 | resp[43]
        frac_part = resp[44] << 24 | resp[45] << 16 | resp[46] << 8 | resp[47]
        server_time = int_part - 2208988800 + (frac_part / 2**32)
        rtt = after - before
        return server_time + rtt / 2
    except Exception:
        return None


def _check_clock_safety():
    for host in NTP_SERVERS:
        t = _ntp_time(host)
        if t is not None:
            drift = abs(t - time.time())
            return drift < MAX_CLOCK_DRIFT, t
    return True, None


def _post_json(url: str, data: dict, timeout: int = 15) -> Optional[dict]:
    if requests:
        try:
            r = requests.post(url, json=data, timeout=timeout)
            return r.json()
        except Exception:
            return None
    try:
        import urllib.request
        body = json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode())
    except Exception:
        return None


def get_hwid() -> str:
    hwid = getattr(get_hwid, "_cache", None)
    if hwid is None:
        hwid = _get_hwid()
        get_hwid._cache = hwid
    return hwid


class LicenseState:
    UNKNOWN = "unknown"
    VALID = "valid"
    EXPIRED = "expired"
    ACTIVATION_REQUIRED = "activation_required"
    CLOCK_TAMPERED = "clock_tampered"
    SERVER_ERROR = "server_error"
    NETWORK_ERROR = "network_error"


def check():
    hwid = get_hwid()
    details = {"hwid": hwid}

    clock_safe, ntp_ts = _check_clock_safety()
    if not clock_safe:
        return LicenseState.CLOCK_TAMPERED, {**details, "error": "system clock tampered"}

    cache = None
    if CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text())
        except Exception:
            pass

    if not cache or not cache.get("token"):
        return LicenseState.ACTIVATION_REQUIRED, details

    try:
        payload_b64 = cache["token"].split(".")[0]
        import base64
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "==").decode())
        exp = payload.get("exp", 0)
        details["expires_at"] = exp
        details["days_left"] = max(0, int((exp - time.time()) / 86400))
    except Exception:
        pass

    server_resp = _post_json(f"{SERVER_URL}/verify", {"token": cache["token"], "hwid": hwid})
    if server_resp is None:
        if details.get("days_left", 0) > 0:
            return LicenseState.VALID, {**details, "online": False}
        return LicenseState.NETWORK_ERROR, {**details, "error": "cannot reach license server"}
    if not server_resp.get("ok"):
        error = server_resp.get("error", "")
        if error in ("expired", "bad_signature", "hwid_mismatch"):
            try:
                CACHE_FILE.unlink()
            except Exception:
                pass
        if error == "expired":
            return LicenseState.EXPIRED, details
        return LicenseState.SERVER_ERROR, {**details, "error": error}

    details["days_left"] = max(0, int((server_resp["expires_at"] - time.time()) / 86400))
    return LicenseState.VALID, {**details, "online": True}


def activate(license_key: str):
    hwid = get_hwid()
    resp = _post_json(f"{SERVER_URL}/activate", {"license_key": license_key, "hwid": hwid})
    if resp is None:
        return LicenseState.NETWORK_ERROR, "Cannot reach license server. Check internet."
    if not resp.get("ok"):
        err = resp.get("error", "unknown")
        msgs = {
            "invalid_key": "License key khong hop le.",
            "expired": "License key da het han.",
            "revoked": "License key da bi thu hoi.",
            "hwid_mismatch": "Key nay da duoc kich hoat tren may khac.",
        }
        return LicenseState.SERVER_ERROR, msgs.get(err, f"Loi: {err}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps({
        "token": resp["token"],
        "expires_at": resp["expires_at"],
        "activated_at": time.time(),
        "hwid": hwid,
    }))
    return LicenseState.VALID, None


def clear_cache():
    try:
        CACHE_FILE.unlink()
    except Exception:
        pass


def cache_info() -> Optional[dict]:
    if not CACHE_FILE.exists():
        return None
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return None
