// flixcloud.cc stream resolver for the Re:Anime provider.
//
// The embed pages hide the playback URL behind a WebAssembly-based
// obfuscation (seed -> SHA-256 field names -> /api/m3u8/{token} -> AES-256-CBC),
// and every playlist it serves is XOR-masked with a WASM-derived key. This
// sidecar does the same in Node.js, decrypts the full playlist tree and
// writes it to a directory, printing {"manifest": "<path>"} on stdout.
//
// Usage: node flixcloud_resolve.js <embed_url> <output_dir>

const https = require("https");
const fs = require("fs");

const BASE = "https://flixcloud.cc";

function get(url, headers) {
  return new Promise((resolve, reject) => {
    https
      .get(url, { headers }, (res) => {
        const chunks = [];
        res.on("data", (chunk) => chunks.push(chunk));
        res.on("end", () => resolve({ status: res.statusCode, body: Buffer.concat(chunks) }));
      })
      .on("error", reject);
  });
}

function extractObject(html, marker) {
  const idx = html.indexOf(marker);
  if (idx === -1) return null;
  const start = html.indexOf("{", idx + marker.length - 1);
  if (start === -1) return null;
  let depth = 0, inStr = false, esc = false;
  for (let i = start; i < html.length; i++) {
    const c = html[i];
    if (inStr) {
      if (esc) esc = false;
      else if (c === "\\") esc = true;
      else if (c === '"') inStr = false;
      continue;
    }
    if (c === '"') inStr = true;
    else if (c === "{") depth++;
    else if (c === "}") { depth--; if (depth === 0) return html.slice(start, i + 1); }
  }
  return null;
}

function Ft(t) { const e = atob(t), a = new Uint8Array(e.length); for (let s = 0; s < e.length; s++) a[s] = e.charCodeAt(s); return a; }

let le = null;
async function er(t, e, a, s, w_payload) {
  const d = Ft(w_payload);
  le = (await WebAssembly.instantiate(d, {})).instance;
  const m = le.exports, E = m.memory;
  E.buffer.byteLength === 0 && E.grow(1);
  const b = new Uint8Array(E.buffer), L = t.length, x = 1e3, A = x + L, v = A + L, B = v + L;
  b.set(t, x); b.set(e, A); b.set(a, v); m._s(s); m._r(x, A, v, B, L);
  const R = new Uint8Array(L); return R.set(b.subarray(B, B + L)), R;
}

async function ce(t) {
  const e = new TextEncoder().encode(t), a = await crypto.subtle.digest("SHA-256", e);
  return Array.from(new Uint8Array(a)).map((c) => c.toString(16).padStart(2, "0")).join("");
}

async function nr(t) {
  let e = t; for (let s = 0; s < 3; s++) e = await ce(e + s.toString());
  let a = e; for (let s = 0; s < 3; s++) a = await ce(a + s.toString());
  return {
    videoField: `vf_${e.substring(0, 8)}`, keyField: `kf_${e.substring(8, 16)}`, ivField: `ivf_${e.substring(16, 24)}`,
    containerName: `cd_${e.substring(24, 32)}`, arrayName: `ad_${e.substring(32, 40)}`, objectName: `od_${e.substring(40, 48)}`,
    tokenField: `${e.substring(48, 64)}_${e.substring(56, 64)}`, keyFrag2Field: `${a.substring(0, 16)}_${a.substring(16, 24)}`,
  };
}

function rr(t, e) {
  const a = t[e.containerName]; if (!a) throw new Error("Container not found");
  const s = a[e.arrayName]; if (!s || !Array.isArray(s) || s.length === 0) throw new Error("Array not found");
  const c = s[0][e.objectName]; if (!c) throw new Error("Object not found");
  const d = c[e.keyField], l = c[e.ivField]; if (!d || !l) throw new Error("Missing required fields");
  return { frag1_b64: d, iv_b64: l };
}

async function resolveStream(embedUrl) {
  const html = (await get(embedUrl, { Referer: "https://reanime.to/", "User-Agent": "Mozilla/5.0" })).body.toString("utf8");
  const seed = html.match(/obfuscation_seed:"([^"]+)"/)?.[1];
  const wPayload = html.match(/w_payload:"([^"]+)"/)?.[1];
  if (!seed || !wPayload) throw new Error("Streaming data not found on embed page");
  const cryptoData = Function("return (" + extractObject(html, "obfuscated_crypto_data:") + ")")();
  const s = await nr(seed);
  const c = rr(cryptoData, s);
  const kf2 = html.match(new RegExp(`["']?${s.keyFrag2Field}["']?\\s*:\\s*"([^"]+)"`))?.[1];
  const token = html.match(new RegExp(`["']?${s.tokenField}["']?\\s*:\\s*"([^"]+)"`))?.[1];
  if (!kf2 || !token) throw new Error("Missing key material on embed page");
  const b = JSON.parse((await get(`${BASE}/api/m3u8/${token}`, { Referer: BASE + "/", "User-Agent": "Mozilla/5.0" })).body);
  const L = (await ce(token + "vid")).substring(0, 10), x = (await ce(token + "key")).substring(0, 10);
  const A = b[L], v = b[x];
  if (!A || !v) throw new Error("Incomplete token response");
  const R = Ft(c.frag1_b64), u = Ft(kf2), p = Ft(v), S = parseInt(seed.substring(0, 8), 16);
  const z = await er(R, u, p, S, wPayload);
  const V = await crypto.subtle.importKey("raw", z, { name: "PBKDF2" }, false, ["deriveBits"]);
  const N = await crypto.subtle.deriveBits({ name: "PBKDF2", salt: new TextEncoder().encode(seed), iterations: 1000, hash: "SHA-256" }, V, 256);
  const G = new Uint8Array(N);
  for (let dt = 0; dt < 32; dt++) G[dt] ^= seed.charCodeAt(dt % seed.length);
  const at = await crypto.subtle.digest("SHA-256", G), o = new Uint8Array(at);
  const y = Ft(c.iv_b64), T = Ft(A);
  const i = await crypto.subtle.importKey("raw", o, { name: "AES-CBC" }, false, ["decrypt"]);
  const P = await crypto.subtle.decrypt({ name: "AES-CBC", iv: y }, i, T);
  const streamUrl = new TextDecoder().decode(P).trim();
  if (!streamUrl.startsWith("http")) throw new Error("Decryption resulted in empty URL");
  return streamUrl;
}

async function main() {
  const [embedUrl, outDir] = process.argv.slice(2);
  if (!embedUrl || !outDir) {
    console.error("usage: node flixcloud_resolve.js <embed_url> <output_dir>");
    process.exit(2);
  }
  const headers = { Referer: BASE + "/", "User-Agent": "Mozilla/5.0" };
  const streamUrl = await resolveStream(embedUrl);

  const mem = le.exports.memory;
  const cAddr = le.exports._c();
  const pk = Buffer.from(new Uint8Array(mem.buffer).slice(cAddr, cAddr + 32)).toString("base64");
  const key = atob(pk);
  const decryptPlaylist = (text) => {
    const trimmed = text.trim();
    if (trimmed.startsWith("#EXTM3U")) return trimmed;
    const enc = atob(trimmed);
    const dec = [];
    for (let h = 0; h < enc.length; h++) dec.push(enc.charCodeAt(h) ^ key.charCodeAt(h % key.length));
    return new TextDecoder().decode(new Uint8Array(dec));
  };

  fs.mkdirSync(outDir, { recursive: true });
  const masterRaw = (await get(streamUrl, headers)).body.toString("utf8");
  const master = decryptPlaylist(masterRaw);
  if (!master.startsWith("#EXTM3U")) throw new Error("Decrypted manifest is invalid");

  // Every referenced playlist (video variants, audio renditions) is masked
  // the same way, so decrypt them all and write them next to the master.
  const playlists = [];
  for (const line of master.split("\n")) {
    const uriAttr = line.match(/URI="([^"]+)"/);
    if (uriAttr) playlists.push(new URL(uriAttr[1], streamUrl));
    const trimmed = line.trim();
    if (trimmed && !trimmed.startsWith("#") && trimmed.endsWith(".m3u8")) {
      playlists.push(new URL(trimmed, streamUrl));
    }
  }
  const localNames = new Set();
  for (const u of playlists) {
    const name = u.pathname.split("/").pop();
    if (!name || localNames.has(name)) continue;
    localNames.add(name);
    const body = (await get(u.toString(), headers)).body.toString("utf8");
    let plain = decryptPlaylist(body);
    if (!plain.startsWith("#EXTM3U")) throw new Error(`Playlist ${name} did not decrypt`);
    // KEY URIs (and any other relative refs) must stay reachable from the
    // local file, so absolutize them against the remote playlist URL.
    plain = plain.replace(/URI="([^"]+)"/g, (whole, ref) =>
      ref.startsWith("http") ? whole : `URI="${new URL(ref, u).toString()}"`
    );
    fs.writeFileSync(`${outDir}/${name}`, plain, "utf8");
  }

  const rewritten = master
    .split("\n")
    .map((line) => {
      const attr = line.match(/URI="([^"]+)"/);
      if (attr) {
        // Audio renditions are decrypted locally by us, so reference the
        // local copies by basename instead of re-fetching them encrypted.
        const localName = attr[1].split("/").pop().split("?")[0];
        if (localNames.has(localName)) return line.replace(attr[0], `URI="${localName}"`);
        return line.replace(attr[0], `URI="${new URL(attr[1], streamUrl)}"`);
      }
      const trimmed = line.trim();
      if (trimmed && !trimmed.startsWith("#") && trimmed.endsWith(".m3u8")) {
        const localName = trimmed.split("/").pop().split("?")[0];
        if (localNames.has(localName)) return localName;
        return new URL(trimmed, streamUrl).toString();
      }
      return line;
    })
    .join("\n");
  const masterName = "master.m3u8";
  fs.writeFileSync(`${outDir}/${masterName}`, rewritten, "utf8");
  process.stdout.write(JSON.stringify({ dir: outDir, manifest: `${outDir}/${masterName}` }));
}

main().catch((error) => {
  console.error("flixcloud resolve failed:", error && error.message ? error.message : error);
  process.exit(1);
});
