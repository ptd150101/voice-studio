// Cloudflare Worker — OmniVoice License Server
// Deploy via: wrangler deploy
// Requires KV namespace LICENSE_KV

const ADMIN_SECRET = "4ae4b51ea627cd1307dd83d1dedfc93a37719784e111659013a9db6d5595ffc2";
const SIGN_KEY     = "7f8eee8d7b1e7c7ecceb68033f1921406150e8e7965418d144754c8428f06d85";

async function hmacSign(data, secret) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" },
    false, ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(data));
  return btoa(String.fromCharCode(...new Uint8Array(sig)));
}

function now() { return Math.floor(Date.now() / 1000); }

async function handleActivate(req, env) {
  if (req.method !== "POST") return json(405, { ok: false, error: "method" });
  const { license_key, hwid } = await req.json();
  if (!license_key || !hwid) return json(400, { ok: false, error: "missing_fields" });

  const record = await env.LICENSE_KV.get(`key:${license_key}`, "json");
  if (!record) return json(403, { ok: false, error: "invalid_key" });
  if (record.revoked) return json(403, { ok: false, error: "revoked" });
  if (now() > record.expires_at) return json(403, { ok: false, error: "expired" });
  if (record.hwid && record.hwid !== hwid)
    return json(403, { ok: false, error: "hwid_mismatch" });

  record.hwid = hwid;
  record.activated_at ??= now();
  record.activation_count = (record.activation_count || 0) + 1;
  await env.LICENSE_KV.put(`key:${license_key}`, JSON.stringify(record));

  const payload = btoa(JSON.stringify({ hwid, key: license_key, exp: record.expires_at, iat: now() }));
  const sig = await hmacSign(payload, SIGN_KEY);
  return json(200, { ok: true, token: payload + "." + sig, expires_at: record.expires_at });
}

async function handleVerify(req, env) {
  if (req.method !== "POST") return json(405, { ok: false, error: "method" });
  const { token, hwid } = await req.json();
  if (!token || !hwid) return json(400, { ok: false, error: "missing_fields" });

  const parts = token.split(".");
  if (parts.length !== 2) return json(400, { ok: false, error: "bad_token" });
  const [payload, sig] = parts;
  const expected = await hmacSign(payload, SIGN_KEY);
  if (sig !== expected) return json(403, { ok: false, error: "bad_signature" });

  const data = JSON.parse(atob(payload));
  if (data.hwid !== hwid) return json(403, { ok: false, error: "hwid_mismatch" });
  if (now() > data.exp) return json(403, { ok: false, error: "expired" });

  return json(200, { ok: true, expires_at: data.exp });
}

async function handleAdminGen(req, env) {
  if (req.method !== "POST") return json(405, { ok: false, error: "method" });
  const { days = 30, count = 1, ttl_seconds } = await req.json();
  const n = Math.min(count, 100);
  // ttl_seconds overrides days — useful for testing with short expiry
  const expiresAt = ttl_seconds ? now() + ttl_seconds : now() + days * 86400;
  const keys = [];
  for (let i = 0; i < n; i++) {
    const raw = crypto.getRandomValues(new Uint8Array(16));
    const k = Array.from(raw).map(b => b.toString(16).padStart(2, "0")).join("");
    const existing = await env.LICENSE_KV.get(`key:${k}`);
    if (existing) { i--; continue; }
    await env.LICENSE_KV.put(`key:${k}`, JSON.stringify({
      created_at: now(), expires_at: expiresAt, days, revoked: false,
      hwid: null, activated_at: null, activation_count: 0,
    }));
    keys.push(k);
  }
  return json(200, { ok: true, keys, expires_at: expiresAt });
}

async function handleAdminList(req, env) {
  if (req.method !== "GET") return json(405, { ok: false, error: "method" });
  const list = await env.LICENSE_KV.list({ prefix: "key:" });
  const keys = [];
  for (const { name } of list.keys) {
    const r = await env.LICENSE_KV.get(name, "json");
    keys.push({ key: name.replace("key:", ""), ...r });
  }
  return json(200, { ok: true, keys });
}

function adminAuth(req) {
  return req.headers.get("X-Admin-Key") === ADMIN_SECRET;
}

function json(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-cache, private, max-age=0" },
  });
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    try {
      if (url.pathname.startsWith("/admin/")) {
        if (!adminAuth(req)) return json(401, { ok: false, error: "unauthorized" });
        if (url.pathname === "/admin/gen")  return handleAdminGen(req, env);
        if (url.pathname === "/admin/list") return handleAdminList(req, env);
        return json(404, { ok: false, error: "not_found" });
      }
      switch (url.pathname) {
        case "/activate": return handleActivate(req, env);
        case "/verify":   return handleVerify(req, env);
        default:          return json(404, { ok: false, error: "not_found" });
      }
    } catch (e) {
      return json(500, { ok: false, error: "internal", detail: e.message });
    }
  },
};
