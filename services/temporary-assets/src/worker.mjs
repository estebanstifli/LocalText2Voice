// A private R2 binding is the only storage credential. Clients receive revocable
// individual beta tokens; Runpod receives only an expiring URL for one image.
const encoder = new TextEncoder();
const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, noarchive" };
const DAY = 86400;
const now = () => Math.floor(Date.now() / 1000);
const reply = (value, status = 200) => Response.json(value, { status, headers });
class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
const fail = (status, message) => { throw new ApiError(status, message); };
const hex = bytes => [...new Uint8Array(bytes)].map(n => n.toString(16).padStart(2, "0")).join("");
const randomToken = () => hex(crypto.getRandomValues(new Uint8Array(32)));
const hash = async value => hex(await crypto.subtle.digest("SHA-256", encoder.encode(value)));
const bearer = request => /^Bearer ([a-f0-9]{64})$/.exec(request.headers.get("Authorization") || "")?.[1] || "";

async function hmacKey(secret) {
  if (!secret) fail(503, "Temporary storage is not configured.");
  return crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}
async function signedUrl(origin, asset, secret) {
  const message = `${asset.id}.${asset.expires}`;
  const sig = hex(await crypto.subtle.sign("HMAC", await hmacKey(secret), encoder.encode(message)));
  return `${origin}/media/${asset.id}?expires=${asset.expires}&signature=${sig}`;
}
async function verifyUrl(url, id, secret) {
  const expires = Number(url.searchParams.get("expires"));
  const signature = url.searchParams.get("signature") || "";
  if (!Number.isSafeInteger(expires) || expires <= now() || !/^[a-f0-9]{64}$/.test(signature)) return false;
  const bytes = Uint8Array.from(signature.match(/../g), byte => parseInt(byte, 16));
  return crypto.subtle.verify("HMAC", await hmacKey(secret), bytes, encoder.encode(`${id}.${expires}`));
}

// The desktop normalizes supported images to metadata-free RGB/RGBA PNG.
// Limit dimensions and validate the container before giving it a download URL.
export function inspectPng(bytes) {
  const signature = [137, 80, 78, 71, 13, 10, 26, 10];
  if (bytes.length < 57 || !signature.every((n, i) => bytes[i] === n)) fail(415, "A PNG image is required.");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let offset = 8, dimensions, data = false;
  while (offset + 12 <= bytes.length) {
    const size = view.getUint32(offset);
    const type = String.fromCharCode(...bytes.subarray(offset + 4, offset + 8));
    if (offset + size + 12 > bytes.length) fail(415, "Truncated PNG image.");
    if (offset === 8) {
      if (type !== "IHDR" || size !== 13) fail(415, "Invalid PNG header.");
      const width = view.getUint32(offset + 8), height = view.getUint32(offset + 12);
      if (!width || !height || width > 4096 || height > 4096 || width * height > 16777216) fail(413, "Image dimensions exceed 4096 x 4096.");
      if (bytes[offset + 16] !== 8 || ![2, 6].includes(bytes[offset + 17]) || bytes[offset + 18] || bytes[offset + 19] || bytes[offset + 20]) fail(415, "Use an 8-bit RGB or RGBA PNG.");
      dimensions = { width, height };
    } else if (type === "IDAT") {
      if (size) data = true;
    } else if (type === "IEND") {
      if (!data || size || offset + 12 !== bytes.length) fail(415, "Invalid PNG end.");
      return dimensions;
    } else {
      fail(415, "PNG metadata is not accepted. Export the image through the application.");
    }
    offset += size + 12;
  }
  fail(415, "Incomplete PNG image.");
}

async function boundedBody(request, size) {
  const bytes = new Uint8Array(size);
  const reader = request.body?.getReader();
  if (!reader) fail(400, "An image body is required.");
  let offset = 0;
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; reader.cancel().catch(() => {}); }, 60000);
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      if (offset + value.length > size) { await reader.cancel(); fail(413, "Image exceeds its declared size."); }
      bytes.set(value, offset); offset += value.length;
    }
  } finally { clearTimeout(timer); reader.releaseLock(); }
  if (timedOut) fail(408, "Upload took too long.");
  if (offset !== size) fail(400, "Incomplete image upload.");
  return bytes;
}
async function smallJson(request) {
  const size = Number(request.headers.get("Content-Length"));
  if (!Number.isInteger(size) || size < 2 || size > 4096) fail(413, "Invalid request size.");
  try { return JSON.parse(new TextDecoder().decode(await boundedBody(request, size))); }
  catch (error) { if (error instanceof ApiError) throw error; fail(400, "Invalid JSON."); }
}

export class AssetRegistry {
  constructor(ctx, env) {
    this.ctx = ctx; this.env = env; this.sql = ctx.storage.sql;
    this.sql.exec(`CREATE TABLE IF NOT EXISTS clients (id TEXT PRIMARY KEY, token_hash TEXT UNIQUE, label TEXT, expires INTEGER, revoked INTEGER DEFAULT 0);
      CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, owner TEXT, object_key TEXT, size INTEGER, expires INTEGER, state TEXT, upload_deadline INTEGER, reads INTEGER DEFAULT 0);
      CREATE INDEX IF NOT EXISTS asset_expiry ON assets(expires);
      CREATE INDEX IF NOT EXISTS asset_owner ON assets(owner);
      CREATE TABLE IF NOT EXISTS counters (day INTEGER, scope TEXT, uploads INTEGER DEFAULT 0, bytes INTEGER DEFAULT 0, reads INTEGER DEFAULT 0, PRIMARY KEY(day, scope));
      CREATE TABLE IF NOT EXISTS service_config (id INTEGER PRIMARY KEY, paused INTEGER);
      INSERT OR IGNORE INTO service_config VALUES (1, 0);`);
  }
  one(query, ...args) { return this.sql.exec(query, ...args).toArray()[0]; }
  limit(key) { return Number(this.env[key]); }
  clientByHash(tokenHash) {
    const client = this.one("SELECT * FROM clients WHERE token_hash = ? AND revoked = 0 AND expires > ?", tokenHash, now());
    if (!client) fail(401, "Invalid or expired beta access code.");
    return client;
  }
  count(scope) {
    return this.one("SELECT * FROM counters WHERE day = ? AND scope = ?", Math.floor(now() / DAY), scope) || { uploads: 0, bytes: 0, reads: 0 };
  }
  bump(scope, uploads, bytes, reads = 0) {
    this.sql.exec(`INSERT INTO counters(day, scope, uploads, bytes, reads) VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(day, scope) DO UPDATE SET uploads = uploads + excluded.uploads, bytes = bytes + excluded.bytes, reads = reads + excluded.reads`,
    Math.floor(now() / DAY), scope, uploads, bytes, reads);
  }
  reserve(client, size, ipHash) {
    // Synchronous transaction: simultaneous requests cannot overbook quotas.
    return this.ctx.storage.transactionSync(() => {
      if (this.one("SELECT paused FROM service_config WHERE id = 1").paused) fail(503, "New uploads are temporarily paused. Existing jobs can finish.");
      const limits = [[client.id, this.limit("CLIENT_DAILY_UPLOADS"), this.limit("CLIENT_DAILY_BYTES")],
        ["global", this.limit("GLOBAL_DAILY_UPLOADS"), this.limit("GLOBAL_DAILY_BYTES")],
        [`ip:${ipHash}`, 500, 1073741824]];
      for (const [scope, uploads, bytes] of limits) {
        const used = this.count(scope);
        if (used.uploads >= uploads || used.bytes + size > bytes) fail(429, "Temporary storage daily quota reached. Use your own storage or try tomorrow (UTC).");
      }
      if (this.one("SELECT COUNT(*) AS n FROM assets WHERE state = 'uploading' AND upload_deadline > ?", now()).n >= 2) fail(429, "Two images are already uploading. Try again shortly.");
      const id = crypto.randomUUID();
      const asset = { id, owner: client.id, object_key: `references/${client.id}/${id}.png`, size,
        expires: now() + this.limit("ASSET_TTL_SECONDS") };
      for (const [scope] of limits) this.bump(scope, 1, size);
      this.sql.exec("INSERT INTO assets(id, owner, object_key, size, expires, state, upload_deadline) VALUES (?, ?, ?, ?, ?, 'uploading', ?)",
        id, client.id, asset.object_key, size, asset.expires, now() + 120);
      return asset;
    });
  }
  async remove(asset) {
    this.sql.exec("UPDATE assets SET state = 'deleting' WHERE id = ?", asset.id);
    await this.env.REFERENCES.delete(asset.object_key);
    this.sql.exec("DELETE FROM assets WHERE id = ?", asset.id);
  }
  async cleanup() {
    const stale = this.sql.exec(`SELECT a.* FROM assets a LEFT JOIN clients c ON c.id = a.owner
      WHERE a.expires <= ? OR a.state = 'deleting' OR (a.state = 'uploading' AND a.upload_deadline < ?)
      OR c.revoked = 1 OR c.expires <= ? LIMIT 200`, now(), now(), now()).toArray();
    let removed = 0;
    for (const asset of stale) { await this.remove(asset); removed++; }
    this.sql.exec("DELETE FROM counters WHERE day < ?", Math.floor(now() / DAY) - 2);
    return removed;
  }
  async fetch(request) {
    try { return await this.route(request); }
    catch (error) {
      // Never log request URLs, image bodies, or credentials.
      if (request.body && !request.bodyUsed) await request.body.cancel().catch(() => {});
      return reply({ error: error instanceof ApiError ? error.message : "Temporary storage is unavailable. Try again later." }, error.status || 503);
    }
  }
  async route(request) {
    const url = new URL(request.url), path = url.pathname;
    if (path === "/internal/cleanup") return reply({ removed: await this.cleanup() });
    if (path === "/internal/commit" || path === "/internal/fail") {
      const data = await smallJson(request);
      const asset = this.one("SELECT * FROM assets WHERE id = ?", data.id);
      if (path === "/internal/fail") {
        if (asset) await this.remove(asset);
        return reply({ deleted: true });
      }
      if (!asset || asset.state !== "uploading" || asset.upload_deadline <= now()) fail(408, "Upload reservation expired.");
      if (!this.one("SELECT id FROM clients WHERE id = ? AND revoked = 0 AND expires > ?", asset.owner, now())) fail(401, "Access was revoked during upload.");
      this.sql.exec("UPDATE assets SET state = 'ready' WHERE id = ?", asset.id);
      return reply({ id: asset.id, url: await signedUrl(data.origin, asset, this.env.URL_SIGNING_KEY), expires: asset.expires }, 201);
    }
    if (path.startsWith("/admin/")) {
      if (!this.env.ADMIN_TOKEN || !bearer(request) || await hash(bearer(request)) !== await hash(this.env.ADMIN_TOKEN)) fail(401, "Admin access denied.");
      if (path === "/admin/clients" && request.method === "POST") {
        const data = await smallJson(request), token = randomToken(), id = crypto.randomUUID();
        const expires = now() + Math.min(365, Math.max(1, Number(data.days) || 90)) * DAY;
        this.sql.exec("INSERT INTO clients(id, token_hash, label, expires) VALUES (?, ?, ?, ?)", id, await hash(token), String(data.label || "Beta user").slice(0, 80), expires);
        return reply({ id, token, expires }, 201);
      }
      if (path === "/admin/clients" && request.method === "GET") return reply({ clients: this.sql.exec("SELECT id, label, expires, revoked FROM clients LIMIT 1000").toArray() });
      if (/^\/admin\/clients\/[a-f0-9-]{36}$/.test(path) && request.method === "DELETE") {
        this.sql.exec("UPDATE clients SET revoked = 1 WHERE id = ?", path.split("/").at(-1));
        await this.cleanup(); return reply({ revoked: true });
      }
      if (path === "/admin/config" && request.method === "PATCH") {
        const data = await smallJson(request);
        if (typeof data.paused !== "boolean") fail(400, "paused must be true or false.");
        this.sql.exec("UPDATE service_config SET paused = ? WHERE id = 1", data.paused ? 1 : 0);
        return reply({ paused: data.paused });
      }
      if (path === "/admin/status" && request.method === "GET") return reply({ ...this.count("global"),
        paused: !!this.one("SELECT paused FROM service_config WHERE id = 1").paused,
        assets: this.one("SELECT COUNT(*) AS count, COALESCE(SUM(size), 0) AS bytes FROM assets") });
      if (path === "/admin/cleanup" && request.method === "POST") return reply({ removed: await this.cleanup() });
      fail(404, "Not found.");
    }
    const media = /^\/media\/([a-f0-9-]{36})$/.exec(path);
    if (media && ["GET", "HEAD"].includes(request.method)) {
      if (!await verifyUrl(url, media[1], this.env.URL_SIGNING_KEY)) fail(403, "Image link has expired or is invalid.");
      const asset = this.one(`SELECT a.* FROM assets a JOIN clients c ON a.owner = c.id
        WHERE a.id = ? AND a.state = 'ready' AND a.expires > ? AND c.revoked = 0 AND c.expires > ?`, media[1], now(), now());
      if (!asset) fail(404, "Temporary image is no longer available.");
      if (asset.reads >= 100 || this.count("global").reads >= this.limit("GLOBAL_DAILY_DOWNLOADS")) fail(429, "Download quota reached.");
      this.bump("global", 0, 0, 1);
      this.sql.exec("UPDATE assets SET reads = reads + 1 WHERE id = ?", asset.id);
      const object = request.method === "HEAD" ? await this.env.REFERENCES.head(asset.object_key) : await this.env.REFERENCES.get(asset.object_key);
      if (!object) fail(404, "Temporary image is no longer available.");
      return new Response(request.method === "HEAD" ? null : object.body, { headers: { ...headers,
        "Content-Type": "image/png", "Content-Length": String(object.size), "Content-Disposition": 'inline; filename="reference.png"' } });
    }
    if (!path.startsWith("/v1/") && path !== "/internal/reserve") fail(404, "Not found.");
    if (!bearer(request)) fail(401, "A beta access code is required.");
    if (path === "/v1/register" && request.method === "POST") {
      const data = await smallJson(request);
      if (data.consent_version !== 1) fail(400, "Accept the current temporary storage notice first.");
      const tokenHash = await hash(bearer(request));
      const ipHash = await hash(request.headers.get("CF-Connecting-IP") || "local");
      return this.ctx.storage.transactionSync(() => {
        const existing = this.one("SELECT * FROM clients WHERE token_hash = ?", tokenHash);
        if (existing) {
          if (existing.revoked || existing.expires <= now()) fail(401, "Temporary storage access has expired or was revoked.");
          if (existing.label === "Automatic installation") this.sql.exec("UPDATE clients SET expires = ? WHERE id = ?", now() + 365 * DAY, existing.id);
          return reply({ registered: true });
        }
        if (this.one("SELECT paused FROM service_config WHERE id = 1").paused) fail(503, "Temporary storage registration is paused.");
        // An installation ID is not proof of a unique person. Bound signups
        // independently of per-client quotas, and retain revoked records.
        for (const [scope, limit] of [["registrations", 1000], [`registration-ip:${ipHash}`, 10]]) {
          if (this.count(scope).uploads >= limit) fail(429, "Registration limit reached. Try tomorrow.");
        }
        this.sql.exec("INSERT INTO clients(id, token_hash, label, expires) VALUES (?, ?, ?, ?)", crypto.randomUUID(), tokenHash, "Automatic installation", now() + 365 * DAY);
        this.bump("registrations", 1, 0);
        this.bump(`registration-ip:${ipHash}`, 1, 0);
        return reply({ registered: true }, 201);
      });
    }
    const client = this.clientByHash(await hash(bearer(request)));
    if (path === "/v1/status" && request.method === "GET") return reply({ available: !this.one("SELECT paused FROM service_config WHERE id = 1").paused,
      expires: client.expires, usage: this.count(client.id), limits: { image_bytes: this.limit("MAX_IMAGE_BYTES"), daily_uploads: this.limit("CLIENT_DAILY_UPLOADS"), daily_bytes: this.limit("CLIENT_DAILY_BYTES") } });
    if (path === "/internal/reserve" && request.method === "POST") {
      const { size } = await smallJson(request);
      if (!Number.isSafeInteger(size) || size <= 0 || size > this.limit("MAX_IMAGE_BYTES")) fail(413, "Image must be at most 15 MB.");
      const asset = this.reserve(client, size, await hash(request.headers.get("CF-Connecting-IP") || "local"));
      return reply(asset, 201);
    }
    const owned = /^\/v1\/assets\/([a-f0-9-]{36})$/.exec(path);
    if (owned && request.method === "DELETE") {
      const asset = this.one("SELECT * FROM assets WHERE id = ? AND owner = ?", owned[1], client.id);
      if (asset) await this.remove(asset);
      return reply({ deleted: true });
    }
    fail(404, "Not found.");
  }
}

export default {
  async fetch(request, env) {
    try { return await publicRequest(request, env); }
    catch (error) { return reply({ error: error instanceof ApiError ? error.message : "Temporary storage is unavailable. Try again later." }, error.status || 503); }
  },
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(env.REGISTRY.get(env.REGISTRY.idFromName("beta-v1")).fetch("https://internal/internal/cleanup").then(response => {
      if (!response.ok) throw new Error("Temporary asset cleanup failed.");
    }));
  }
};

async function publicRequest(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/" && request.method === "GET") return new Response("LocalText2Voice temporary image storage (beta). Private uploads with individual access codes. Images expire after 24 hours; generated media is handled by Runpod.", { headers: { ...headers, "Content-Type": "text/plain; charset=utf-8" } });
    if (url.pathname === "/health" && request.method === "GET") return reply({ service: "localtext2voice-temporary-assets", version: 1 });
    if (url.pathname.startsWith("/internal/")) return reply({ error: "Not found." }, 404);
    const registry = env.REGISTRY.get(env.REGISTRY.idFromName("beta-v1"));
    const internal = (path, data) => {
      const body = JSON.stringify(data);
      return registry.fetch(new Request(`https://internal${path}`, { method: "POST", body, headers: {
        "Content-Type": "application/json", "Content-Length": String(encoder.encode(body).length),
        "Authorization": request.headers.get("Authorization") || "", "CF-Connecting-IP": request.headers.get("CF-Connecting-IP") || "local"
      } }));
    };
    if (url.pathname === "/v1/assets" && request.method === "POST") {
      if (request.headers.get("Content-Type") !== "image/png") fail(415, "Upload an image/png body.");
      const size = Number(request.headers.get("Content-Length"));
      if (!Number.isSafeInteger(size) || size <= 0 || size > Number(env.MAX_IMAGE_BYTES)) fail(413, "Image must be at most 15 MB.");
      const reserved = await internal("/internal/reserve", { size });
      if (!reserved.ok) return reserved;
      const asset = await reserved.json();
      try {
        const bytes = await boundedBody(request, size);
        inspectPng(bytes);
        await env.REFERENCES.put(asset.object_key, bytes, { httpMetadata: { contentType: "image/png", cacheControl: "no-store" } });
        const committed = await internal("/internal/commit", { id: asset.id, origin: url.origin });
        if (!committed.ok) {
          await env.REFERENCES.delete(asset.object_key);
          await internal("/internal/fail", { id: asset.id });
        }
        return committed;
      } catch (error) {
        await env.REFERENCES.delete(asset.object_key);
        await internal("/internal/fail", { id: asset.id });
        throw error;
      }
    }
    // Only small administration bodies cross the Durable Object boundary.
    // Buffer them before forwarding so early rejection cannot leave a live
    // forwarded request stream behind the response.
    if (request.body) {
      const size = Number(request.headers.get("Content-Length"));
      if (!Number.isSafeInteger(size) || size < 0 || size > 4096) fail(413, "Invalid request size.");
      return registry.fetch(new Request(request, { body: await boundedBody(request, size) }));
    }
    return registry.fetch(request);
}
