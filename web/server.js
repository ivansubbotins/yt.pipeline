const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const PORT = 8199;
const PIPELINE_DIR = path.join(__dirname, '..');
const AGENT_PY = path.join(PIPELINE_DIR, 'agent.py');
const DATA_DIR = path.join(PIPELINE_DIR, 'data');
const CONTEXT_FILE = path.join(PIPELINE_DIR, 'channel_context.json');
const STYLES_DIR = path.join(PIPELINE_DIR, 'assets', 'styles');
const STYLES_META_FILE = path.join(STYLES_DIR, 'styles.json');
const CLOTHING_DIR = path.join(PIPELINE_DIR, 'assets', 'clothing');
const CLOTHING_META_FILE = path.join(CLOTHING_DIR, 'presets.json');
const FONTS_DIR = path.join(PIPELINE_DIR, 'fonts');
const ASSETS_DIR = path.join(PIPELINE_DIR, 'assets');
const REFERENCES_DIR = path.join(ASSETS_DIR, 'references');
const TEXTURES_DIR = path.join(ASSETS_DIR, 'textures');
const CLOTHING_MALE_DIR = path.join(ASSETS_DIR, 'clothing', 'male');
const CLOTHING_FEMALE_DIR = path.join(ASSETS_DIR, 'clothing', 'female');
const FONT_PREVIEWS_DIR = path.join(ASSETS_DIR, 'fonts');

// ── Read .env ──
let ANTHROPIC_API_KEY = '';
let ADMIN_USER = 'admin';
let ADMIN_PASS = 'changeme';
let YOUTUBE_CLIENT_ID = '';
let YOUTUBE_CLIENT_SECRET = '';
let YOUTUBE_REDIRECT_URI = '';
try {
  const envContent = fs.readFileSync(path.join(PIPELINE_DIR, '.env'), 'utf8');
  const match = envContent.match(/ANTHROPIC_API_KEY=(.+)/);
  if (match) ANTHROPIC_API_KEY = match[1].trim();
  const userMatch = envContent.match(/ADMIN_USER=(.+)/);
  if (userMatch) ADMIN_USER = userMatch[1].trim();
  const passMatch = envContent.match(/ADMIN_PASS=(.+)/);
  if (passMatch) ADMIN_PASS = passMatch[1].trim();
  const ytIdMatch = envContent.match(/YOUTUBE_CLIENT_ID=(.+)/);
  if (ytIdMatch) YOUTUBE_CLIENT_ID = ytIdMatch[1].trim();
  const ytSecMatch = envContent.match(/YOUTUBE_CLIENT_SECRET=(.+)/);
  if (ytSecMatch) YOUTUBE_CLIENT_SECRET = ytSecMatch[1].trim();
  const ytRedMatch = envContent.match(/YOUTUBE_REDIRECT_URI=(.+)/);
  if (ytRedMatch) YOUTUBE_REDIRECT_URI = ytRedMatch[1].trim();
} catch (e) {}

// ── API Keys for external clients ──
const API_KEYS = new Map(); // key -> { name, created }
try {
  const envContent = fs.readFileSync(path.join(PIPELINE_DIR, '.env'), 'utf8');
  const apiKeysMatch = envContent.match(/API_KEYS=(.+)/);
  if (apiKeysMatch) {
    apiKeysMatch[1].trim().split(',').forEach(pair => {
      const [name, key] = pair.trim().split(':');
      if (name && key) API_KEYS.set(key.trim(), { name: name.trim(), created: Date.now() });
    });
    if (API_KEYS.size > 0) console.log(`[API] Loaded ${API_KEYS.size} API key(s)`);
  }
} catch(e) {}

// ── Rate Limiting ──
const rateLimits = new Map(); // apiKeyName -> { count, windowStart }
const RATE_LIMIT_PER_MIN = 60;
const RATE_WINDOW_MS = 60000;

function checkRateLimit(apiKeyName) {
  const now = Date.now();
  let entry = rateLimits.get(apiKeyName);
  if (!entry || now - entry.windowStart > RATE_WINDOW_MS) {
    entry = { count: 0, windowStart: now };
  }
  entry.count++;
  rateLimits.set(apiKeyName, entry);
  return {
    allowed: entry.count <= RATE_LIMIT_PER_MIN,
    remaining: Math.max(0, RATE_LIMIT_PER_MIN - entry.count),
    reset: Math.ceil((entry.windowStart + RATE_WINDOW_MS - now) / 1000),
  };
}

// ── Auth: session tokens ──
const crypto = require('crypto');
const activeSessions = new Map(); // token -> { user, created }
const SESSION_MAX_AGE = 7 * 24 * 60 * 60 * 1000; // 7 days

// ── 2FA: TOTP (Google Authenticator) ──
const TOTP_SECRET_FILE = path.join(__dirname, '..', '.totp_secret');
let TOTP_ENABLED = false;
let TOTP_SECRET = '';

// Load or check TOTP
try {
  if (fs.existsSync(TOTP_SECRET_FILE)) {
    TOTP_SECRET = fs.readFileSync(TOTP_SECRET_FILE, 'utf8').trim();
    TOTP_ENABLED = TOTP_SECRET.length > 0;
  }
} catch(e) {}

function base32Decode(base32) {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const c of base32.toUpperCase().replace(/=+$/, '')) {
    const val = chars.indexOf(c);
    if (val === -1) continue;
    bits += val.toString(2).padStart(5, '0');
  }
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) {
    bytes.push(parseInt(bits.substring(i, i + 8), 2));
  }
  return Buffer.from(bytes);
}

function base32Encode(buffer) {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const byte of buffer) bits += byte.toString(2).padStart(8, '0');
  let result = '';
  for (let i = 0; i < bits.length; i += 5) {
    const chunk = bits.substring(i, i + 5).padEnd(5, '0');
    result += chars[parseInt(chunk, 2)];
  }
  return result;
}

function generateTOTP(secret, timeStep = 30, digits = 6) {
  const time = Math.floor(Date.now() / 1000 / timeStep);
  const timeBuffer = Buffer.alloc(8);
  timeBuffer.writeUInt32BE(Math.floor(time / 0x100000000), 0);
  timeBuffer.writeUInt32BE(time & 0xFFFFFFFF, 4);
  const key = base32Decode(secret);
  const hmac = crypto.createHmac('sha1', key).update(timeBuffer).digest();
  const offset = hmac[hmac.length - 1] & 0x0f;
  const code = ((hmac[offset] & 0x7f) << 24 | hmac[offset+1] << 16 | hmac[offset+2] << 8 | hmac[offset+3]) % (10 ** digits);
  return code.toString().padStart(digits, '0');
}

function verifyTOTP(secret, token) {
  // Check current and ±1 time window (30 sec tolerance)
  for (const offset of [0, -1, 1]) {
    const time = Math.floor(Date.now() / 1000 / 30) + offset;
    const timeBuffer = Buffer.alloc(8);
    timeBuffer.writeUInt32BE(Math.floor(time / 0x100000000), 0);
    timeBuffer.writeUInt32BE(time & 0xFFFFFFFF, 4);
    const key = base32Decode(secret);
    const hmac = crypto.createHmac('sha1', key).update(timeBuffer).digest();
    const off = hmac[hmac.length - 1] & 0x0f;
    const code = ((hmac[off] & 0x7f) << 24 | hmac[off+1] << 16 | hmac[off+2] << 8 | hmac[off+3]) % 1000000;
    if (code.toString().padStart(6, '0') === token) return true;
  }
  return false;
}

function generateTOTPSecret() {
  return base32Encode(crypto.randomBytes(20));
}

function generateToken() {
  return crypto.randomBytes(32).toString('hex');
}

function parseCookies(cookieHeader) {
  const cookies = {};
  if (!cookieHeader) return cookies;
  cookieHeader.split(';').forEach(c => {
    const [k, ...v] = c.trim().split('=');
    if (k) cookies[k.trim()] = v.join('=').trim();
  });
  return cookies;
}

function isAuthenticated(req) {
  return getAuthInfo(req).authenticated;
}

function getAuthInfo(req) {
  // 1. Check API key (Bearer token)
  const authHeader = req.headers['authorization'] || '';
  if (authHeader.startsWith('Bearer ')) {
    const key = authHeader.slice(7);
    const apiKey = API_KEYS.get(key);
    if (apiKey) return { authenticated: true, type: 'api_key', name: apiKey.name, key };
  }
  // 2. Fall back to cookie session
  const cookies = parseCookies(req.headers.cookie);
  const token = cookies['yt_session'];
  if (!token) return { authenticated: false };
  const session = activeSessions.get(token);
  if (!session) return { authenticated: false };
  if (Date.now() - session.created > SESSION_MAX_AGE) {
    activeSessions.delete(token);
    return { authenticated: false };
  }
  return { authenticated: true, type: 'session', name: session.user };
}

// Cleanup expired sessions every hour
setInterval(() => {
  const now = Date.now();
  for (const [token, session] of activeSessions) {
    if (now - session.created > SESSION_MAX_AGE) activeSessions.delete(token);
  }
}, 60 * 60 * 1000);

// ── Styles helpers ──
function ensureStylesDir() {
  if (!fs.existsSync(STYLES_DIR)) fs.mkdirSync(STYLES_DIR, { recursive: true });
}

function readStylesMeta() {
  ensureStylesDir();
  if (!fs.existsSync(STYLES_META_FILE)) return [];
  try { return JSON.parse(fs.readFileSync(STYLES_META_FILE, 'utf8')); }
  catch { return []; }
}

function writeStylesMeta(styles) {
  ensureStylesDir();
  fs.writeFileSync(STYLES_META_FILE, JSON.stringify(styles, null, 2), 'utf8');
}

// ── Clothing helpers ──
function ensureClothingDir() {
  if (!fs.existsSync(CLOTHING_DIR)) fs.mkdirSync(CLOTHING_DIR, { recursive: true });
}
function readClothingMeta() {
  ensureClothingDir();
  if (!fs.existsSync(CLOTHING_META_FILE)) return [];
  try { return JSON.parse(fs.readFileSync(CLOTHING_META_FILE, 'utf8')); }
  catch { return []; }
}
function writeClothingMeta(presets) {
  ensureClothingDir();
  fs.writeFileSync(CLOTHING_META_FILE, JSON.stringify(presets, null, 2), 'utf8');
}

// ── Webhooks ──
const WEBHOOKS_FILE = path.join(DATA_DIR, 'webhooks.json');

function readWebhooksFile() {
  try { return fs.existsSync(WEBHOOKS_FILE) ? JSON.parse(fs.readFileSync(WEBHOOKS_FILE, 'utf8')) : []; }
  catch { return []; }
}
function writeWebhooksFile(hooks) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.writeFileSync(WEBHOOKS_FILE, JSON.stringify(hooks, null, 2));
}

function notifyWebhooks(event, projectId, data) {
  const hooks = readWebhooksFile().filter(h => h.active && h.events.includes(event));
  for (const hook of hooks) {
    const payload = JSON.stringify({ event, timestamp: new Date().toISOString(), project_id: projectId, data });
    const signature = crypto.createHmac('sha256', hook.secret || '').update(payload).digest('hex');
    try {
      const https = require('https');
      const http = require('http');
      const urlObj = new URL(hook.url);
      const transport = urlObj.protocol === 'https:' ? https : http;
      const req = transport.request(hook.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Webhook-Signature': 'sha256=' + signature, 'Content-Length': Buffer.byteLength(payload) },
        timeout: 10000,
      });
      req.on('error', () => {}); // Fire and forget
      req.write(payload);
      req.end();
    } catch(e) {}
  }
}

// ── Anthropic API call (via Python SDK — bypasses Node.js ETIMEDOUT on Russian VPS) ──
function callClaude(systemPrompt, userMessage) {
  return new Promise((resolve, reject) => {
    const { execFile } = require('child_process');
    const pythonScript = path.join(__dirname, 'claude_call.py');
    console.log('[Claude] Calling via Python SDK...');
    execFile('python3', [pythonScript, systemPrompt, userMessage], {
      timeout: 120000,
      maxBuffer: 10 * 1024 * 1024,
      cwd: path.join(__dirname, '..'),
    }, (err, stdout, stderr) => {
      // Python ALWAYS prints JSON to stdout, even when exiting non-zero on API error.
      // Try parsing stdout FIRST — that gives us the structured error message.
      // Only fall back to err.message if stdout doesn't contain valid JSON.
      if (stdout) {
        try {
          const result = JSON.parse(stdout.trim());
          if (result.ok) {
            const u = result.usage || {};
            console.log(`[Claude] OK: ${u.input_tokens || '?'} in / ${u.output_tokens || '?'} out`);
            return resolve(result.text);
          }
          // result.ok === false — extract clean error message
          let errMsg = result.error || 'Claude API error';
          // Pretty-print common errors
          if (/credit balance is too low/i.test(errMsg)) {
            errMsg = 'Anthropic credit balance is too low. Top up at console.anthropic.com → Plans & Billing.';
          } else if (/invalid x-api-key/i.test(errMsg)) {
            errMsg = 'Invalid ANTHROPIC_API_KEY. Check .env on the server.';
          } else if (/rate limit/i.test(errMsg)) {
            errMsg = 'Anthropic rate limit hit. Wait a minute and retry.';
          }
          console.error('[Claude API Error]', errMsg);
          return reject(new Error(errMsg));
        } catch (e) {
          // stdout wasn't valid JSON — fall through to err handling
          console.error('[Claude Parse Error] stdout:', stdout.substring(0, 300));
        }
      }
      if (err) {
        console.error('[Claude Python Error]', err.code, stderr?.substring(0, 300));
        // Don't expose the full command in the message — it's huge and useless to the user
        const shortMsg = err.code === 'ETIMEDOUT' ? 'Claude request timed out (120s)'
                       : stderr ? stderr.substring(0, 200)
                       : 'Claude script failed (exit ' + (err.code || '?') + ')';
        return reject(new Error(shortMsg));
      }
      reject(new Error('Claude returned no output'));
    });
  });
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.jpg': 'image/jpeg',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.ttf': 'font/ttf',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.webp': 'image/webp',
};

// ── Run python agent command ──
function runAgent(args) {
  return new Promise((resolve, reject) => {
    // Try venv (Linux then Windows), then system python
    const venvUnix = path.join(PIPELINE_DIR, '.venv', 'bin', 'python3');
    const venvWin = path.join(PIPELINE_DIR, '.venv', 'Scripts', 'python.exe');
    const python = fs.existsSync(venvUnix) ? venvUnix
                 : fs.existsSync(venvWin) ? venvWin
                 : (process.platform === 'win32' ? 'python' : 'python3');
    const proc = spawn(python, [AGENT_PY, ...args], {
      cwd: PIPELINE_DIR,
      env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
    });
    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', d => stdout += d);
    proc.stderr.on('data', d => stderr += d);
    proc.on('close', code => {
      if (code === 0) resolve(stdout.trim());
      else reject(new Error(stderr || stdout || `Exit code ${code}`));
    });
    proc.on('error', reject);
  });
}

// ── Read project state ──
function readState(projectId) {
  const stateFile = path.join(DATA_DIR, projectId, 'state.json');
  if (!fs.existsSync(stateFile)) return null;
  try {
    return JSON.parse(fs.readFileSync(stateFile, 'utf8'));
  } catch (e) {
    console.error(`Error reading state for ${projectId}:`, e.message);
    return null;
  }
}

// ── List projects ──
function listProjects() {
  if (!fs.existsSync(DATA_DIR)) return [];
  return fs.readdirSync(DATA_DIR)
    .filter(d => fs.existsSync(path.join(DATA_DIR, d, 'state.json')))
    .map(d => {
      const state = readState(d);
      return {
        id: d,
        topic: state?.topic || '—',
        channel_id: state?.channel_id || '',
        current_step: state?.current_step || '—',
        created_at: state?.created_at || '—',
        updated_at: state?.updated_at || '—',
        steps: state?.steps || {},
      };
    })
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
}

// ── Read POST body ──
function readBody(req) {
  return new Promise((resolve, reject) => {
    req.setEncoding('utf8');
    let body = '';
    req.on('data', c => { body += c; if (body.length > 1e6) req.destroy(); });
    req.on('end', () => resolve(body));
    req.on('error', reject);
  });
}

// ── List project files (thumbnails, texts) ──
function listProjectFiles(projectId) {
  const projDir = path.join(DATA_DIR, projectId);
  const files = {};

  // Thumbnails
  const thumbDir = path.join(projDir, 'thumbnails');
  if (fs.existsSync(thumbDir)) {
    files.thumbnails = fs.readdirSync(thumbDir)
      .filter(f => /\.(jpg|jpeg|png)$/i.test(f))
      .map(f => `/api/file/${projectId}/thumbnails/${f}`);
  }

  // Main thumbnail
  const mainThumb = path.join(projDir, 'thumbnail.jpg');
  if (fs.existsSync(mainThumb)) {
    files.main_thumbnail = `/api/file/${projectId}/thumbnail.jpg`;
  }

  // Text files
  for (const name of ['teleprompter.txt', 'teleprompter_raw.txt', 'description.txt', 'REVIEW.txt']) {
    const fp = path.join(projDir, name);
    if (fs.existsSync(fp)) {
      files[name.replace('.txt', '')] = fs.readFileSync(fp, 'utf8');
    }
  }

  return files;
}

// ── HTTP Server ──
http.createServer(async (req, res) => {
  let url, pathname;
  try {
    url = new URL(req.url, `http://${req.headers.host}`);
    try { pathname = decodeURIComponent(url.pathname); }
    catch { pathname = url.pathname; } // malformed %-encoding — use raw pathname
  } catch (e) {
    res.writeHead(400, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: false, error: 'Bad request URL' }));
    return;
  }

  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');

  // ── Auth routes (no auth required) ──

  // POST /api/login
  if (pathname === '/api/login' && req.method === 'POST') {
    let body;
    let rawBody = await readBody(req);
    // Fix escaped special chars (some clients/browsers escape !)
    rawBody = rawBody.replace(/\\!/g, '!');
    try { body = JSON.parse(rawBody); } catch(e) {
      console.error('Login JSON parse error:', e.message, 'raw:', rawBody.substring(0, 100));
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'Invalid request' }));
      return;
    }
    if (body.user === ADMIN_USER && body.pass === ADMIN_PASS) {
      // Check 2FA if enabled
      if (TOTP_ENABLED) {
        if (!body.totp) {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, need_totp: true, error: 'Введите код из Google Authenticator' }));
          return;
        }
        if (!verifyTOTP(TOTP_SECRET, body.totp)) {
          res.writeHead(401, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: 'Неверный код 2FA' }));
          return;
        }
      }
      const token = generateToken();
      activeSessions.set(token, { user: body.user, created: Date.now() });
      res.writeHead(200, {
        'Content-Type': 'application/json',
        'Set-Cookie': `yt_session=${token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${7*24*60*60}`,
      });
      res.end(JSON.stringify({ ok: true }));
    } else {
      res.writeHead(401, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'Неверный логин или пароль' }));
    }
    return;
  }

  // GET /api/logout
  if (pathname === '/api/logout') {
    const cookies = parseCookies(req.headers.cookie);
    if (cookies['yt_session']) activeSessions.delete(cookies['yt_session']);
    res.writeHead(302, {
      'Set-Cookie': 'yt_session=; Path=/; HttpOnly; Max-Age=0',
      'Location': '/login.html',
    });
    res.end();
    return;
  }

  // GET /api/2fa/status — Check if 2FA is enabled (no auth required for login page)
  if (pathname === '/api/2fa/status' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, enabled: TOTP_ENABLED }));
    return;
  }

  // POST /api/2fa/setup — Generate TOTP secret and QR URL (auth required)
  if (pathname === '/api/2fa/setup' && req.method === 'POST') {
    if (!isAuthenticated(req)) { res.writeHead(401); res.end('Unauthorized'); return; }
    const secret = TOTP_SECRET || generateTOTPSecret();
    const issuer = 'YT-Pipeline';
    const account = ADMIN_USER;
    const otpauthUrl = `otpauth://totp/${issuer}:${account}?secret=${secret}&issuer=${issuer}&digits=6&period=30`;
    // Don't save yet — user must verify first
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, secret, otpauth_url: otpauthUrl, qr_text: otpauthUrl }));
    return;
  }

  // POST /api/2fa/verify — Verify TOTP code and enable 2FA
  if (pathname === '/api/2fa/verify' && req.method === 'POST') {
    if (!isAuthenticated(req)) { res.writeHead(401); res.end('Unauthorized'); return; }
    const body = JSON.parse(await readBody(req));
    const { secret, code } = body;
    if (!secret || !code) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'Secret and code required' }));
      return;
    }
    if (verifyTOTP(secret, code)) {
      // Save secret and enable
      fs.writeFileSync(TOTP_SECRET_FILE, secret);
      TOTP_SECRET = secret;
      TOTP_ENABLED = true;
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, message: '2FA включена!' }));
    } else {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'Неверный код. Попробуйте ещё раз.' }));
    }
    return;
  }

  // POST /api/2fa/disable — Disable 2FA
  if (pathname === '/api/2fa/disable' && req.method === 'POST') {
    if (!isAuthenticated(req)) { res.writeHead(401); res.end('Unauthorized'); return; }
    const body = JSON.parse(await readBody(req));
    if (TOTP_ENABLED && !verifyTOTP(TOTP_SECRET, body.code || '')) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'Введите текущий код 2FA для отключения' }));
      return;
    }
    try { fs.unlinkSync(TOTP_SECRET_FILE); } catch(e) {}
    TOTP_SECRET = '';
    TOTP_ENABLED = false;
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, message: '2FA отключена' }));
    return;
  }

  // GET /oauth/start — Initiate YouTube OAuth (optionally for a specific channel)
  if (pathname === '/oauth/start' && req.method === 'GET') {
    if (!isAuthenticated(req)) {
      res.writeHead(302, { 'Location': '/login.html' });
      res.end();
      return;
    }
    const channelId = url.searchParams.get('channel_id') || '';
    const scopes = [
      'https://www.googleapis.com/auth/youtube.upload',
      'https://www.googleapis.com/auth/youtube',
      'https://www.googleapis.com/auth/youtube.readonly',
    ];
    const state = channelId ? Buffer.from(JSON.stringify({ channel_id: channelId })).toString('base64url') : '';
    const authUrl = `https://accounts.google.com/o/oauth2/auth?` +
      `client_id=${encodeURIComponent(YOUTUBE_CLIENT_ID)}` +
      `&redirect_uri=${encodeURIComponent(YOUTUBE_REDIRECT_URI)}` +
      `&response_type=code` +
      `&scope=${encodeURIComponent(scopes.join(' '))}` +
      `&access_type=offline` +
      `&prompt=consent` +
      (state ? `&state=${state}` : '');
    res.writeHead(302, { 'Location': authUrl });
    res.end();
    return;
  }

  // GET /oauth/callback — YouTube OAuth2 callback (no auth required)
  if (pathname === '/oauth/callback' && req.method === 'GET') {
    const code = url.searchParams.get('code');
    const error = url.searchParams.get('error');
    const stateRaw = url.searchParams.get('state') || '';
    let callbackChannelId = '';
    if (stateRaw) {
      try {
        const decoded = JSON.parse(Buffer.from(stateRaw, 'base64url').toString('utf8'));
        callbackChannelId = decoded.channel_id || '';
      } catch(e) { console.warn('OAuth state parse failed:', e.message); }
    }
    if (error) {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(`<html><body style="background:#0c0c0c;color:#ef4444;font-family:sans-serif;padding:40px;text-align:center;"><h2>Ошибка OAuth: ${error}</h2><a href="/" style="color:#3b82f6;">Назад</a></body></html>`);
      return;
    }
    if (!code) {
      res.writeHead(400, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end('<html><body style="background:#0c0c0c;color:#ef4444;font-family:sans-serif;padding:40px;text-align:center;"><h2>Нет кода авторизации</h2></body></html>');
      return;
    }
    // Exchange code for token using Google OAuth2
    const https = require('https');
    const tokenBody = new URLSearchParams({
      code,
      client_id: YOUTUBE_CLIENT_ID,
      client_secret: YOUTUBE_CLIENT_SECRET,
      redirect_uri: YOUTUBE_REDIRECT_URI,
      grant_type: 'authorization_code',
    }).toString();

    const tokenReq = https.request({
      hostname: 'oauth2.googleapis.com',
      path: '/token',
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'Content-Length': Buffer.byteLength(tokenBody) },
    }, tokenRes => {
      let data = '';
      tokenRes.on('data', c => data += c);
      tokenRes.on('end', () => {
        try {
          const tokenData = JSON.parse(data);
          if (tokenData.error) {
            res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
            res.end(`<html><body style="background:#0c0c0c;color:#ef4444;font-family:sans-serif;padding:40px;text-align:center;"><h2>Ошибка: ${tokenData.error_description || tokenData.error}</h2><a href="/" style="color:#3b82f6;">Назад</a></body></html>`);
            return;
          }
          // Save token — to per-channel location if state had channel_id, otherwise default
          let tokenPath;
          let savedFor;
          if (callbackChannelId) {
            const channelDir = path.join(DATA_DIR, 'channels', callbackChannelId);
            fs.mkdirSync(channelDir, { recursive: true });
            tokenPath = path.join(channelDir, 'token.json');
            savedFor = `канала ${callbackChannelId}`;
            // Also invalidate stale videos cache for this channel
            const cacheFile = path.join(channelDir, 'videos_cache.json');
            if (fs.existsSync(cacheFile)) { fs.unlinkSync(cacheFile); console.log('Invalidated stale cache for', callbackChannelId); }
          } else {
            tokenPath = path.join(PIPELINE_DIR, 'youtube_token.json');
            savedFor = 'основного аккаунта';
          }
          fs.writeFileSync(tokenPath, JSON.stringify(tokenData, null, 2));
          console.log('YouTube OAuth token saved:', tokenPath);
          res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
          res.end(`<html><body style="background:#0c0c0c;color:#22c55e;font-family:sans-serif;padding:40px;text-align:center;">
            <h2>YouTube подключён для ${savedFor}!</h2>
            <p style="color:#bbb;margin-top:12px;">Токен сохранён. Теперь можно загружать видео канала.</p>
            <a href="/" style="color:#3b82f6;font-size:16px;">Вернуться в админку →</a>
          </body></html>`);
        } catch(e) {
          res.writeHead(500, { 'Content-Type': 'text/html; charset=utf-8' });
          res.end(`<html><body style="background:#0c0c0c;color:#ef4444;font-family:sans-serif;padding:40px;"><h2>Ошибка: ${e.message}</h2></body></html>`);
        }
      });
    });
    tokenReq.on('error', e => {
      res.writeHead(500, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(`<html><body style="background:#0c0c0c;color:#ef4444;font-family:sans-serif;padding:40px;"><h2>Ошибка сети: ${e.message}</h2></body></html>`);
    });
    tokenReq.write(tokenBody);
    tokenReq.end();
    return;
  }

  // GET /api/youtube/auth-url — Generate OAuth URL for YouTube connection
  if (pathname === '/api/youtube/auth-url' && req.method === 'GET') {
    if (!isAuthenticated(req)) {
      res.writeHead(401, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'Unauthorized' }));
      return;
    }
    const scopes = [
      'https://www.googleapis.com/auth/youtube.upload',
      'https://www.googleapis.com/auth/youtube',
      'https://www.googleapis.com/auth/youtube.readonly',
    ];
    const authUrl = `https://accounts.google.com/o/oauth2/auth?` +
      `client_id=${encodeURIComponent(YOUTUBE_CLIENT_ID)}` +
      `&redirect_uri=${encodeURIComponent(YOUTUBE_REDIRECT_URI)}` +
      `&response_type=code` +
      `&scope=${encodeURIComponent(scopes.join(' '))}` +
      `&access_type=offline` +
      `&prompt=consent`;
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, url: authUrl }));
    return;
  }

  // GET /api/youtube/status — Check if YouTube token exists
  if (pathname === '/api/youtube/status' && req.method === 'GET') {
    if (!isAuthenticated(req)) {
      res.writeHead(401, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false }));
      return;
    }
    const tokenPath = path.join(PIPELINE_DIR, 'youtube_token.json');
    const connected = fs.existsSync(tokenPath);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, connected }));
    return;
  }

  // Login page — serve without auth
  if (pathname === '/login.html' || pathname === '/login') {
    const loginPath = path.join(__dirname, 'public', 'login.html');
    if (fs.existsSync(loginPath)) {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      fs.createReadStream(loginPath).pipe(res);
    } else {
      res.writeHead(404); res.end('Login page not found');
    }
    return;
  }

  // Serve shared CSS without auth (needed by login page)
  if (pathname === '/shared.css') {
    const cssPath = path.join(__dirname, 'public', 'shared.css');
    if (fs.existsSync(cssPath)) {
      res.writeHead(200, { 'Content-Type': 'text/css; charset=utf-8', 'Cache-Control': 'public, max-age=3600' });
      fs.createReadStream(cssPath).pipe(res);
    } else {
      res.writeHead(404); res.end('Not found');
    }
    return;
  }

  // OPTIONS preflight — no auth
  if (req.method === 'OPTIONS') {
    res.writeHead(200, {
      'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    });
    res.end();
    return;
  }

  // ══════════════════════════════════════════
  // ══  REST API v1 (for external clients)  ══
  // ══════════════════════════════════════════
  if (pathname.startsWith('/api/v1/')) {
    const auth = getAuthInfo(req);
    if (!auth.authenticated) {
      res.writeHead(401, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: { code: 'UNAUTHORIZED', message: 'Invalid or missing API key. Use Authorization: Bearer <key>' } }));
      return;
    }

    // Rate limiting (API keys only)
    if (auth.type === 'api_key') {
      const rl = checkRateLimit(auth.name);
      res.setHeader('X-RateLimit-Limit', RATE_LIMIT_PER_MIN);
      res.setHeader('X-RateLimit-Remaining', rl.remaining);
      res.setHeader('X-RateLimit-Reset', rl.reset);
      if (!rl.allowed) {
        res.writeHead(429, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: { code: 'RATE_LIMITED', message: `Rate limit exceeded. Retry in ${rl.reset}s` } }));
        return;
      }
    }

    const reqId = 'req_' + crypto.randomBytes(8).toString('hex');
    const v1path = pathname.replace('/api/v1', '');
    const json200 = (data) => { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ ok: true, data, meta: { request_id: reqId, timestamp: new Date().toISOString() } })); };
    const jsonErr = (code, msg, status = 400) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ ok: false, error: { code, message: msg }, meta: { request_id: reqId } })); };

    try {

    // ── Projects ──

    // GET /api/v1/projects
    if (v1path === '/projects' && req.method === 'GET') {
      const projects = listProjects();
      return json200({ projects });
    }

    // POST /api/v1/projects
    if (v1path === '/projects' && req.method === 'POST') {
      const body = JSON.parse(await readBody(req));
      if (!body.topic) return jsonErr('INVALID_INPUT', 'topic is required');
      const args = ['new', body.topic];
      if (body.channel_id) args.push('--channel', body.channel_id);
      const output = await runAgent(args);
      const idMatch = output.match(/([0-9]{8}-[\w-]+)/);
      const projectId = idMatch ? idMatch[1] : null;
      notifyWebhooks('project.created', projectId, { topic: body.topic });
      return json200({ project_id: projectId, output: output.trim() });
    }

    // GET /api/v1/projects/:id
    if (v1path.match(/^\/projects\/[^/]+$/) && req.method === 'GET') {
      const projectId = v1path.split('/')[2];
      const stateFile = path.join(DATA_DIR, projectId, 'state.json');
      if (!fs.existsSync(stateFile)) return jsonErr('NOT_FOUND', 'Project not found', 404);
      const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
      return json200({ project: state });
    }

    // DELETE /api/v1/projects/:id
    if (v1path.match(/^\/projects\/[^/]+$/) && req.method === 'DELETE') {
      const projectId = v1path.split('/')[2];
      const projDir = path.join(DATA_DIR, projectId);
      if (!fs.existsSync(projDir)) return jsonErr('NOT_FOUND', 'Project not found', 404);
      fs.rmSync(projDir, { recursive: true, force: true });
      return json200({ deleted: projectId });
    }

    // ── Pipeline ──

    // GET /api/v1/projects/:id/status
    if (v1path.match(/^\/projects\/[^/]+\/status$/) && req.method === 'GET') {
      const projectId = v1path.split('/')[2];
      const stateFile = path.join(DATA_DIR, projectId, 'state.json');
      if (!fs.existsSync(stateFile)) return jsonErr('NOT_FOUND', 'Project not found', 404);
      const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
      return json200({ project_id: projectId, current_step: state.current_step, steps: state.steps });
    }

    // POST /api/v1/projects/:id/run
    if (v1path.match(/^\/projects\/[^/]+\/run$/) && req.method === 'POST') {
      const projectId = v1path.split('/')[2];
      const output = await runAgent(['run', projectId]);
      notifyWebhooks('step.completed', projectId, { step: 'all', output: output.substring(0, 500) });
      return json200({ project_id: projectId, output: output.trim() });
    }

    // POST /api/v1/projects/:id/steps/:step/run
    if (v1path.match(/^\/projects\/[^/]+\/steps\/[^/]+\/run$/) && req.method === 'POST') {
      const parts = v1path.split('/');
      const projectId = parts[2];
      const step = parts[4];
      try {
        const output = await runAgent(['step', projectId, step]);
        notifyWebhooks('step.completed', projectId, { step });
        return json200({ project_id: projectId, step, output: output.trim() });
      } catch(e) {
        notifyWebhooks('step.failed', projectId, { step, error: e.message });
        return jsonErr('STEP_FAILED', e.message, 500);
      }
    }

    // POST /api/v1/projects/:id/steps/:step/reset
    if (v1path.match(/^\/projects\/[^/]+\/steps\/[^/]+\/reset$/) && req.method === 'POST') {
      const parts = v1path.split('/');
      const projectId = parts[2];
      const step = parts[4];
      const stateFile = path.join(DATA_DIR, projectId, 'state.json');
      if (!fs.existsSync(stateFile)) return jsonErr('NOT_FOUND', 'Project not found', 404);
      const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
      if (state.steps[step]) {
        state.steps[step].status = 'pending';
        state.steps[step].data = {};
        fs.writeFileSync(stateFile, JSON.stringify(state, null, 2));
      }
      return json200({ project_id: projectId, step, status: 'pending' });
    }

    // ── Content Selection ──

    // GET/PUT /api/v1/projects/:id/titles
    if (v1path.match(/^\/projects\/[^/]+\/titles$/) && (req.method === 'GET' || req.method === 'PUT')) {
      const projectId = v1path.split('/')[2];
      const filePath = path.join(DATA_DIR, projectId, 'selected_titles.json');
      if (req.method === 'GET') {
        const data = fs.existsSync(filePath) ? JSON.parse(fs.readFileSync(filePath, 'utf8')) : { titles: [] };
        return json200(data);
      }
      const body = JSON.parse(await readBody(req));
      fs.writeFileSync(filePath, JSON.stringify(body, null, 2));
      return json200({ saved: true });
    }

    // GET/PUT /api/v1/projects/:id/angle
    if (v1path.match(/^\/projects\/[^/]+\/angle$/) && (req.method === 'GET' || req.method === 'PUT')) {
      const projectId = v1path.split('/')[2];
      const filePath = path.join(DATA_DIR, projectId, 'selected_angle.json');
      if (req.method === 'GET') {
        const data = fs.existsSync(filePath) ? JSON.parse(fs.readFileSync(filePath, 'utf8')) : { angle: null };
        return json200(data);
      }
      const body = JSON.parse(await readBody(req));
      fs.writeFileSync(filePath, JSON.stringify(body, null, 2));
      return json200({ saved: true });
    }

    // GET/PUT /api/v1/projects/:id/hook
    if (v1path.match(/^\/projects\/[^/]+\/hook$/) && (req.method === 'GET' || req.method === 'PUT')) {
      const projectId = v1path.split('/')[2];
      const filePath = path.join(DATA_DIR, projectId, 'selected_hook.json');
      if (req.method === 'GET') {
        const data = fs.existsSync(filePath) ? JSON.parse(fs.readFileSync(filePath, 'utf8')) : { hookIndex: -1 };
        return json200(data);
      }
      const body = JSON.parse(await readBody(req));
      fs.writeFileSync(filePath, JSON.stringify(body, null, 2));
      return json200({ saved: true });
    }

    // ── Sources ──

    // GET /api/v1/projects/:id/sources
    if (v1path.match(/^\/projects\/[^/]+\/sources$/) && req.method === 'GET') {
      const projectId = v1path.split('/')[2];
      const filePath = path.join(DATA_DIR, projectId, 'sources.json');
      const data = fs.existsSync(filePath) ? JSON.parse(fs.readFileSync(filePath, 'utf8')) : { items: [] };
      return json200(data);
    }

    // POST /api/v1/projects/:id/sources
    if (v1path.match(/^\/projects\/[^/]+\/sources$/) && req.method === 'POST') {
      const projectId = v1path.split('/')[2];
      const body = JSON.parse(await readBody(req));
      const filePath = path.join(DATA_DIR, projectId, 'sources.json');
      let sourcesData = fs.existsSync(filePath) ? JSON.parse(fs.readFileSync(filePath, 'utf8')) : { items: [] };
      const newItem = { id: String(Date.now()), type: body.type || 'url', url: body.url || '', content: body.content || '', notebook_id: body.notebook_id || '', title: body.title || '', status: 'pending', added_at: new Date().toISOString() };
      sourcesData.items.push(newItem);
      const projDir = path.join(DATA_DIR, projectId);
      fs.mkdirSync(projDir, { recursive: true });
      fs.writeFileSync(filePath, JSON.stringify(sourcesData, null, 2));
      return json200({ item: newItem });
    }

    // DELETE /api/v1/projects/:id/sources/:sourceId
    if (v1path.match(/^\/projects\/[^/]+\/sources\/[^/]+$/) && req.method === 'DELETE') {
      const parts = v1path.split('/');
      const projectId = parts[2];
      const sourceId = parts[4];
      const filePath = path.join(DATA_DIR, projectId, 'sources.json');
      if (fs.existsSync(filePath)) {
        const data = JSON.parse(fs.readFileSync(filePath, 'utf8'));
        data.items = data.items.filter(i => i.id !== sourceId);
        fs.writeFileSync(filePath, JSON.stringify(data, null, 2));
      }
      return json200({ deleted: sourceId });
    }

    // ── Generated Content ──

    // GET /api/v1/projects/:id/script
    if (v1path.match(/^\/projects\/[^/]+\/script$/) && req.method === 'GET') {
      const projectId = v1path.split('/')[2];
      const stateFile = path.join(DATA_DIR, projectId, 'state.json');
      if (!fs.existsSync(stateFile)) return jsonErr('NOT_FOUND', 'Project not found', 404);
      const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
      return json200({ script: state.steps?.script?.data || null });
    }

    // GET /api/v1/projects/:id/teleprompter
    if (v1path.match(/^\/projects\/[^/]+\/teleprompter$/) && req.method === 'GET') {
      const projectId = v1path.split('/')[2];
      const txtFile = path.join(DATA_DIR, projectId, 'teleprompter.txt');
      const stateFile = path.join(DATA_DIR, projectId, 'state.json');
      let text = '';
      if (fs.existsSync(txtFile)) text = fs.readFileSync(txtFile, 'utf8');
      let data = null;
      if (fs.existsSync(stateFile)) {
        const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
        data = state.steps?.teleprompter?.data || null;
      }
      return json200({ text, data });
    }

    // GET /api/v1/projects/:id/description
    if (v1path.match(/^\/projects\/[^/]+\/description$/) && req.method === 'GET') {
      const projectId = v1path.split('/')[2];
      const txtFile = path.join(DATA_DIR, projectId, 'description.txt');
      const stateFile = path.join(DATA_DIR, projectId, 'state.json');
      let text = '';
      if (fs.existsSync(txtFile)) text = fs.readFileSync(txtFile, 'utf8');
      let data = null;
      if (fs.existsSync(stateFile)) {
        const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
        data = state.steps?.description?.data || null;
      }
      return json200({ text, data });
    }

    // GET /api/v1/projects/:id/covers
    if (v1path.match(/^\/projects\/[^/]+\/covers$/) && req.method === 'GET') {
      const projectId = v1path.split('/')[2];
      const thumbDir = path.join(DATA_DIR, projectId, 'thumbnails');
      let covers = [];
      if (fs.existsSync(thumbDir)) {
        covers = fs.readdirSync(thumbDir).filter(f => f.endsWith('.jpg') || f.endsWith('.png')).map(f => ({ filename: f, url: `/api/file/${projectId}/thumbnails/${f}` }));
      }
      return json200({ covers });
    }

    // ── Manual Steps ──

    // POST /api/v1/projects/:id/shooting-done
    if (v1path.match(/^\/projects\/[^/]+\/shooting-done$/) && req.method === 'POST') {
      const projectId = v1path.split('/')[2];
      const output = await runAgent(['shot-done', projectId]);
      return json200({ project_id: projectId, output: output.trim() });
    }

    // POST /api/v1/projects/:id/editing-done
    if (v1path.match(/^\/projects\/[^/]+\/editing-done$/) && req.method === 'POST') {
      const projectId = v1path.split('/')[2];
      const body = JSON.parse(await readBody(req));
      const args = ['edit-done', projectId];
      if (body.video_file) args.push(body.video_file);
      const output = await runAgent(args);
      return json200({ project_id: projectId, output: output.trim() });
    }

    // ── Publishing ──

    // POST /api/v1/projects/:id/publish
    if (v1path.match(/^\/projects\/[^/]+\/publish$/) && req.method === 'POST') {
      const projectId = v1path.split('/')[2];
      const body = JSON.parse(await readBody(req));
      const args = ['publish', projectId, '--approve'];
      if (body.schedule) args.push('--schedule', body.schedule);
      if (body.playlist_id) args.push('--playlist', body.playlist_id);
      if (body.category_id) args.push('--category', body.category_id);
      const output = await runAgent(args);
      notifyWebhooks('publish.completed', projectId, { output: output.substring(0, 500) });
      return json200({ project_id: projectId, output: output.trim() });
    }

    // GET /api/v1/playlists
    if (v1path === '/playlists' && req.method === 'GET') {
      const output = await runAgent(['playlists']);
      let playlists = [];
      try { const m = output.match(/\[[\s\S]*\]/); if (m) playlists = JSON.parse(m[0]); } catch(e) {}
      return json200({ playlists });
    }

    // ── Dubbing ──

    // POST /api/v1/projects/:id/dubbing — start dubbing
    if (v1path.match(/^\/projects\/[^/]+\/dubbing$/) && req.method === 'POST') {
      const projectId = v1path.split('/')[2];
      const body = JSON.parse(await readBody(req));
      const languages = body.languages || []; // e.g. ["en", "es", "pt"]
      const args = ['dub', projectId];
      if (languages.length > 0) args.push('--languages', languages.join(','));
      const output = await runAgent(args);
      notifyWebhooks('dubbing.completed', projectId, { languages, output: output.substring(0, 500) });
      return json200({ project_id: projectId, output: output.trim() });
    }

    // GET /api/v1/projects/:id/dubbing — dubbing status
    if (v1path.match(/^\/projects\/[^/]+\/dubbing$/) && req.method === 'GET') {
      const projectId = v1path.split('/')[2];
      const stateFile = path.join(DATA_DIR, projectId, 'state.json');
      if (!fs.existsSync(stateFile)) return jsonErr('NOT_FOUND', 'Project not found', 404);
      const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
      const dubStep = state.steps?.dubbing || { status: 'pending', data: {} };

      // Also read dubbing config if exists
      const configFile = path.join(DATA_DIR, projectId, 'dubbing_config.json');
      const config = fs.existsSync(configFile) ? JSON.parse(fs.readFileSync(configFile, 'utf8')) : null;

      // Read per-language results
      const dubbingDir = path.join(DATA_DIR, projectId, 'dubbing');
      const langResults = {};
      if (fs.existsSync(dubbingDir)) {
        fs.readdirSync(dubbingDir).filter(d => {
          const p = path.join(dubbingDir, d);
          return fs.statSync(p).isDirectory() && d.length <= 3;
        }).forEach(lang => {
          const metaFile = path.join(dubbingDir, lang, 'metadata.json');
          const videoFile = path.join(dubbingDir, lang, 'final.mp4');
          const combinedFile = path.join(dubbingDir, lang, 'combined.wav');
          langResults[lang] = {
            has_video: fs.existsSync(videoFile),
            has_audio: fs.existsSync(combinedFile),
            has_metadata: fs.existsSync(metaFile),
            metadata: fs.existsSync(metaFile) ? JSON.parse(fs.readFileSync(metaFile, 'utf8')) : null,
          };
        });
      }

      // Read dubbing progress
      const progressFile = path.join(dubbingDir, 'dubbing_state.json');
      const progress = fs.existsSync(progressFile) ? JSON.parse(fs.readFileSync(progressFile, 'utf8')) : {};

      return json200({ dubbing: dubStep, config, languages: langResults, progress });
    }

    // GET /api/v1/projects/:id/dubbing/:lang — download dubbed video
    if (v1path.match(/^\/projects\/[^/]+\/dubbing\/[a-z]{2}$/) && req.method === 'GET') {
      const parts = v1path.split('/');
      const projectId = parts[2];
      const lang = parts[4];
      // Try combined.wav first (dubbed audio track), then final.mp4 (legacy)
      let audioFile = path.join(DATA_DIR, projectId, 'dubbing', lang, 'combined.wav');
      let contentType = 'audio/wav';
      let ext = 'wav';
      if (!fs.existsSync(audioFile)) {
        audioFile = path.join(DATA_DIR, projectId, 'dubbing', lang, 'final.mp4');
        contentType = 'video/mp4';
        ext = 'mp4';
      }
      if (!fs.existsSync(audioFile)) return jsonErr('NOT_FOUND', `Dubbed audio not found for ${lang}`, 404);
      const stat = fs.statSync(audioFile);
      res.writeHead(200, {
        'Content-Type': contentType,
        'Content-Length': stat.size,
        'Content-Disposition': `attachment; filename="dubbed_${lang}.${ext}"`,
      });
      fs.createReadStream(audioFile).pipe(res);
      return;
    }

    // POST /api/v1/projects/:id/dubbing/:lang/publish — publish dubbed video to YouTube
    if (v1path.match(/^\/projects\/[^/]+\/dubbing\/[a-z]{2}\/publish$/) && req.method === 'POST') {
      const parts = v1path.split('/');
      const projectId = parts[2];
      const lang = parts[4];
      const body = JSON.parse(await readBody(req));
      const args = ['publish-dubbed', projectId, lang];
      if (body.schedule) args.push('--schedule', body.schedule);
      if (body.channel_id) args.push('--channel', body.channel_id);
      const output = await runAgent(args);
      notifyWebhooks('dubbing.published', projectId, { lang, output: output.substring(0, 500) });
      return json200({ project_id: projectId, lang, output: output.trim() });
    }

    // GET /api/v1/dubbing/languages — available languages
    if (v1path === '/dubbing/languages' && req.method === 'GET') {
      return json200({
        languages: {
          en: { name: 'English', provider: 'ElevenLabs TTS' },
          es: { name: 'Spanish', provider: 'ElevenLabs TTS' },
          pt: { name: 'Portuguese', provider: 'ElevenLabs TTS' },
          de: { name: 'German', provider: 'ElevenLabs TTS' },
          ko: { name: 'Korean', provider: 'ElevenLabs TTS' },
          ja: { name: 'Japanese', provider: 'ElevenLabs TTS' },
          zh: { name: 'Chinese', provider: 'ElevenLabs TTS' },
        }
      });
    }

    // ── Split Tests ──

    // POST /api/v1/projects/:id/splittest
    if (v1path.match(/^\/projects\/[^/]+\/splittest$/) && req.method === 'POST') {
      const projectId = v1path.split('/')[2];
      const output = await runAgent(['splittest-start', projectId]);
      return json200({ project_id: projectId, output: output.trim() });
    }

    // GET /api/v1/projects/:id/splittest
    if (v1path.match(/^\/projects\/[^/]+\/splittest$/) && req.method === 'GET') {
      const projectId = v1path.split('/')[2];
      const testFile = path.join(DATA_DIR, projectId, 'splittest.json');
      if (!fs.existsSync(testFile)) return json200({ test: null });
      return json200({ test: JSON.parse(fs.readFileSync(testFile, 'utf8')) });
    }

    // POST /api/v1/projects/:id/splittest/stop
    if (v1path.match(/^\/projects\/[^/]+\/splittest\/stop$/) && req.method === 'POST') {
      const projectId = v1path.split('/')[2];
      const body = JSON.parse(await readBody(req));
      const method = body.method || 'auto';
      const args = ['splittest-finish', projectId, method];
      if (body.winner_index !== undefined) args.push(String(body.winner_index));
      const output = await runAgent(args);
      notifyWebhooks('splittest.completed', projectId, { method, output: output.substring(0, 500) });
      return json200({ project_id: projectId, output: output.trim() });
    }

    // ── Channels ──

    // GET /api/v1/channels
    if (v1path === '/channels' && req.method === 'GET') {
      const channelsDir = path.join(DATA_DIR, 'channels');
      let channels = [];
      if (fs.existsSync(channelsDir)) {
        channels = fs.readdirSync(channelsDir).filter(d => fs.existsSync(path.join(channelsDir, d, 'context.json'))).map(d => {
          const ctx = JSON.parse(fs.readFileSync(path.join(channelsDir, d, 'context.json'), 'utf8'));
          return { id: d, name: ctx.name || d, niche: ctx.niche || '', audience: ctx.audience || '' };
        });
      }
      return json200({ channels });
    }

    // GET /api/v1/channels/:id
    if (v1path.match(/^\/channels\/[^/]+$/) && req.method === 'GET') {
      const channelId = v1path.split('/')[2];
      if (channelId === 'default') {
        const ctx = fs.existsSync(CONTEXT_FILE) ? JSON.parse(fs.readFileSync(CONTEXT_FILE, 'utf8')) : {};
        return json200({ channel: ctx });
      }
      const ctxFile = path.join(DATA_DIR, 'channels', channelId, 'context.json');
      if (!fs.existsSync(ctxFile)) return jsonErr('NOT_FOUND', 'Channel not found', 404);
      return json200({ channel: JSON.parse(fs.readFileSync(ctxFile, 'utf8')) });
    }

    // ── Webhooks ──

    // GET /api/v1/webhooks
    if (v1path === '/webhooks' && req.method === 'GET') {
      const hooks = readWebhooksFile();
      return json200({ webhooks: hooks.filter(h => h.api_key_name === auth.name) });
    }

    // POST /api/v1/webhooks
    if (v1path === '/webhooks' && req.method === 'POST') {
      const body = JSON.parse(await readBody(req));
      if (!body.url) return jsonErr('INVALID_INPUT', 'url is required');
      const hooks = readWebhooksFile();
      const hook = {
        id: 'wh_' + crypto.randomBytes(8).toString('hex'),
        url: body.url,
        events: body.events || ['step.completed', 'step.failed', 'project.created', 'publish.completed'],
        secret: body.secret || crypto.randomBytes(16).toString('hex'),
        api_key_name: auth.name,
        active: true,
        created_at: new Date().toISOString(),
      };
      hooks.push(hook);
      writeWebhooksFile(hooks);
      return json200({ webhook: hook });
    }

    // DELETE /api/v1/webhooks/:id
    if (v1path.match(/^\/webhooks\/[^/]+$/) && req.method === 'DELETE') {
      const hookId = v1path.split('/')[2];
      let hooks = readWebhooksFile();
      hooks = hooks.filter(h => !(h.id === hookId && h.api_key_name === auth.name));
      writeWebhooksFile(hooks);
      return json200({ deleted: hookId });
    }

    // 404 for unknown v1 paths
    return jsonErr('NOT_FOUND', `Unknown endpoint: ${req.method} ${pathname}`, 404);

    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: { code: 'INTERNAL_ERROR', message: err.message }, meta: { request_id: reqId } }));
      return;
    }
  }

  // ── Auth check for everything else ──
  if (!isAuthenticated(req)) {
    // API calls get 401
    if (pathname.startsWith('/api/')) {
      res.writeHead(401, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'Unauthorized' }));
      return;
    }
    // Pages redirect to login
    res.writeHead(302, { 'Location': '/login.html' });
    res.end();
    return;
  }

  // ── API Routes (auth required) ──

  // ── Channel Management ──

  // GET /api/channels — List all channels
  if (pathname === '/api/channels' && req.method === 'GET') {
    const indexFile = path.join(DATA_DIR, 'channels', 'channels.json');
    const channels = fs.existsSync(indexFile) ? JSON.parse(fs.readFileSync(indexFile, 'utf8')) : [];
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, channels }));
    return;
  }

  // POST /api/channels — Create channel
  if (pathname === '/api/channels' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const indexFile = path.join(DATA_DIR, 'channels', 'channels.json');
      const channels = fs.existsSync(indexFile) ? JSON.parse(fs.readFileSync(indexFile, 'utf8')) : [];
      // Simple transliteration for channel ID
      const translit = { 'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'yo','ж':'zh','з':'z','и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh','щ':'sch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya' };
      const slug = body.name.toLowerCase().split('').map(c => translit[c] || c).join('').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').substring(0, 40);
      const channelId = slug || 'channel';
      const channelDir = path.join(DATA_DIR, 'channels', channelId);
      if (!fs.existsSync(channelDir)) {
        fs.mkdirSync(channelDir, { recursive: true });
        const context = {
          author: { name: '', full_name: '', who: '', expertise: [], experience: '', tone: '' },
          channel: { name: body.name || '', youtube_url: body.youtube_url || '', telegram_url: '', telegram_group: '', website: '', social_links: {} },
          niche: body.niche || '',
          target_audience: body.target_audience || '',
          cta: { subscribe: '', like_comment: '', lead_magnet: { enabled: false }, mid_roll: { enabled: false }, end_screen: { enabled: true, text: '' } },
          description_links: [], hashtags_always: [], tags_always: [],
        };
        fs.writeFileSync(path.join(channelDir, 'context.json'), JSON.stringify(context, null, 2));
        // Update index
        const entry = { id: channelId, name: body.name, niche: body.niche || '', youtube_url: body.youtube_url || '', target_audience: body.target_audience || '', created_at: new Date().toISOString() };
        // Check if already exists
        const existing = channels.find(c => c.id === channelId);
        if (!existing) channels.push(entry);
        const idxDir = path.join(DATA_DIR, 'channels');
        fs.mkdirSync(idxDir, { recursive: true });
        fs.writeFileSync(path.join(idxDir, 'channels.json'), JSON.stringify(channels, null, 2));
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, channel_id: channelId }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // GET /api/channels/:id — Get channel context
  if (pathname.match(/^\/api\/channels\/[^/]+$/) && req.method === 'GET') {
    const channelId = pathname.split('/')[3];
    const ctxFile = path.join(DATA_DIR, 'channels', channelId, 'context.json');
    if (fs.existsSync(ctxFile)) {
      const ctx = JSON.parse(fs.readFileSync(ctxFile, 'utf8'));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, context: ctx, channel_id: channelId }));
    } else {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'Channel not found' }));
    }
    return;
  }

  // PUT /api/channels/:id — Update channel context
  if (pathname.match(/^\/api\/channels\/[^/]+$/) && req.method === 'PUT') {
    try {
      const channelId = pathname.split('/')[3];
      const body = JSON.parse(await readBody(req));
      const channelDir = path.join(DATA_DIR, 'channels', channelId);
      fs.mkdirSync(channelDir, { recursive: true });
      fs.writeFileSync(path.join(channelDir, 'context.json'), JSON.stringify(body.context || body, null, 2));
      // Update index entry
      const indexFile = path.join(DATA_DIR, 'channels', 'channels.json');
      if (fs.existsSync(indexFile)) {
        const channels = JSON.parse(fs.readFileSync(indexFile, 'utf8'));
        const idx = channels.findIndex(c => c.id === channelId);
        if (idx >= 0) {
          if (body.context?.channel?.name) channels[idx].name = body.context.channel.name;
          if (body.context?.niche) channels[idx].niche = body.context.niche;
          if (body.context?.target_audience) channels[idx].target_audience = body.context.target_audience;
          fs.writeFileSync(indexFile, JSON.stringify(channels, null, 2));
        }
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // GET /api/channels/:id/auth-status — Check YouTube auth for channel
  if (pathname.match(/^\/api\/channels\/[^/]+\/auth-status$/) && req.method === 'GET') {
    const channelId = pathname.split('/')[3];
    const tokenFile = path.join(DATA_DIR, 'channels', channelId, 'token.json');
    const hasToken = fs.existsSync(tokenFile);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, connected: hasToken }));
    return;
  }

  // GET /api/channels/:id/videos — Fetch channel's own videos
  if (pathname.match(/^\/api\/channels\/[^/]+\/videos$/) && req.method === 'GET') {
    try {
      let channelId = pathname.split('/')[3];
      const isDefault = channelId === 'default';
      // Check cache first (TTL 24h)
      const cacheDir = isDefault ? PIPELINE_DIR : path.join(DATA_DIR, 'channels', channelId);
      const cacheFile = path.join(cacheDir, 'videos_cache.json');
      if (fs.existsSync(cacheFile)) {
        const cached = JSON.parse(fs.readFileSync(cacheFile, 'utf8'));
        const cachedAt = new Date(cached.cached_at || 0);
        const hoursOld = (Date.now() - cachedAt.getTime()) / 3600000;
        if (hoursOld < 24 && cached.videos) {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: true, videos: cached.videos, cached: true }));
          return;
        }
      }
      // Fetch fresh
      const output = await runAgent(['channel-videos', isDefault ? '' : channelId]);
      console.log('channel-videos output length:', output.length, 'first 200:', output.substring(0, 200));

      // Check for not_authorized error from Python
      const notAuthMatch = output.match(/\{"error":\s*"not_authorized"[\s\S]*?\}/);
      if (notAuthMatch) {
        try {
          const errObj = JSON.parse(notAuthMatch[0]);
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: 'channel_not_authorized', message: errObj.message || 'Канал не авторизован через OAuth. Сначала подключите YouTube-аккаунт для этого канала.', channel_id: errObj.channel_id }));
          return;
        } catch(e) {}
      }

      let videos = [];
      try {
        // Output may contain Python log lines before JSON — find the JSON array starting with [\n  {
        const jsonStart = output.indexOf('[\n');
        if (jsonStart === -1) {
          // Try compact format [{"
          const compactStart = output.indexOf('[{');
          if (compactStart >= 0) videos = JSON.parse(output.substring(compactStart));
          else console.error('channel-videos: no JSON array found');
        } else {
          videos = JSON.parse(output.substring(jsonStart));
        }
      } catch(e) { console.error('channel-videos parse error:', e.message); }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, videos }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // GET /api/channels/:id/recommendations — Topic recommendations (with cache)
  if (pathname.match(/^\/api\/channels\/[^/]+\/recommendations$/) && req.method === 'GET') {
    try {
      const channelId = pathname.split('/')[3];
      const forceRefresh = url.searchParams.get('refresh') === '1';

      // Check cache first (no TTL — only refreshed on explicit request or by adding new videos)
      if (channelId !== 'default' && !forceRefresh) {
        const cacheFile = path.join(DATA_DIR, 'channels', channelId, 'recommendations_cache.json');
        if (fs.existsSync(cacheFile)) {
          try {
            const cached = JSON.parse(fs.readFileSync(cacheFile, 'utf8'));
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ ok: true, cached: true, ...cached }));
            return;
          } catch(e) { console.warn('recs cache parse failed:', e.message); }
        }
      }

      const output = await runAgent(['recommend-topics', channelId === 'default' ? '' : channelId]);

      // Check for not_authorized error
      const notAuthMatch = output.match(/\{"error":\s*"not_authorized"[\s\S]*?\}/);
      if (notAuthMatch) {
        try {
          const errObj = JSON.parse(notAuthMatch[0]);
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: 'channel_not_authorized', message: errObj.message || 'Канал не авторизован. Подключите YouTube для этого канала.', channel_id: errObj.channel_id }));
          return;
        } catch(e) {}
      }

      let recommendations = {};
      try {
        const match = output.match(/\{[\s\S]*\}/);
        if (match) recommendations = JSON.parse(match[0]);
      } catch(e) {}
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, cached: false, ...recommendations }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // DELETE /api/channels/:id/recommendations — clear cache (manual "refresh")
  if (pathname.match(/^\/api\/channels\/[^/]+\/recommendations$/) && req.method === 'DELETE') {
    const channelId = pathname.split('/')[3];
    const cacheFile = path.join(DATA_DIR, 'channels', channelId, 'recommendations_cache.json');
    if (fs.existsSync(cacheFile)) fs.unlinkSync(cacheFile);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  // POST /api/audience-fit — Check topic vs channel audience fit
  if (pathname === '/api/audience-fit' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const { channel_id, topic } = body;
      if (!topic) throw new Error('Topic required');

      // Load channel context
      let niche = '', audience = '', channelName = '';
      if (channel_id) {
        const ctxFile = path.join(DATA_DIR, 'channels', channel_id, 'context.json');
        if (fs.existsSync(ctxFile)) {
          const ctx = JSON.parse(fs.readFileSync(ctxFile, 'utf8'));
          niche = ctx.niche || '';
          audience = ctx.target_audience || '';
          channelName = ctx.channel?.name || '';
        }
      }

      if (!niche && !audience) {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, fit: 'good', message: 'Ниша канала не задана — проверка невозможна' }));
        return;
      }

      const result = await callClaude(
        'Ты оцениваешь соответствие темы видео целевой аудитории канала. Ответ СТРОГО JSON, без markdown.',
        `Канал: ${channelName}\nНиша канала: ${niche}\nЦелевая аудитория: ${audience}\nПредложенная тема видео: ${topic}\n\nОцени, насколько эта тема подходит для данного канала и его аудитории.\n\nОтвет JSON:\n{"fit": "good" или "warning" или "poor", "message": "Объяснение на русском (1-2 предложения). Если warning/poor — объясни почему ЦА может не совпасть и предложи как адаптировать тему."}`
      );

      let fitResult = { fit: 'good', message: '' };
      try {
        const match = result.match(/\{[\s\S]*\}/);
        if (match) fitResult = JSON.parse(match[0]);
      } catch(e) {}

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, ...fitResult }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // ── Personas Management ──

  // GET /api/personas — List all personas
  if (pathname === '/api/personas' && req.method === 'GET') {
    const personasFile = path.join(PIPELINE_DIR, 'assets', 'personas', 'personas.json');
    const personas = fs.existsSync(personasFile) ? JSON.parse(fs.readFileSync(personasFile, 'utf8')) : [];
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, personas }));
    return;
  }

  // POST /api/personas — Add persona (multipart: photo + name + description)
  if (pathname === '/api/personas' && req.method === 'POST') {
    try {
      const personasDir = path.join(PIPELINE_DIR, 'assets', 'personas');
      fs.mkdirSync(personasDir, { recursive: true });

      // Parse multipart
      const contentType = req.headers['content-type'] || '';
      let name = '', description = '', role = '';

      if (contentType.includes('multipart/form-data')) {
        const boundary = contentType.split('boundary=')[1];
        const rawBody = await new Promise((resolve) => {
          const chunks = []; req.on('data', c => chunks.push(c)); req.on('end', () => resolve(Buffer.concat(chunks)));
        });
        const parts = rawBody.toString('binary').split('--' + boundary);
        let photoData = null, photoExt = 'jpg';

        for (const part of parts) {
          if (part.includes('name="name"')) {
            name = Buffer.from(part.split('\r\n\r\n')[1].split('\r\n')[0], 'binary').toString('utf8').trim();
          } else if (part.includes('name="description"')) {
            description = Buffer.from(part.split('\r\n\r\n')[1].split('\r\n')[0], 'binary').toString('utf8').trim();
          } else if (part.includes('name="role"')) {
            role = Buffer.from(part.split('\r\n\r\n')[1].split('\r\n')[0], 'binary').toString('utf8').trim();
          } else if (part.includes('name="photo"')) {
            const dataStart = part.indexOf('\r\n\r\n') + 4;
            const dataEnd = part.lastIndexOf('\r\n');
            photoData = Buffer.from(part.substring(dataStart, dataEnd), 'binary');
            if (part.includes('.png')) photoExt = 'png';
          }
        }

        if (!name) throw new Error('Name required');

        const id = Date.now().toString();
        // Save photo
        let photoPath = '';
        if (photoData && photoData.length > 100) {
          const filename = `${id}.${photoExt}`;
          fs.writeFileSync(path.join(personasDir, filename), photoData);
          photoPath = filename;
        }

        // Update personas.json
        const personasFile = path.join(personasDir, 'personas.json');
        const personas = fs.existsSync(personasFile) ? JSON.parse(fs.readFileSync(personasFile, 'utf8')) : [];
        const persona = { id, name, description, role, photo: photoPath, created_at: new Date().toISOString() };
        personas.push(persona);
        fs.writeFileSync(personasFile, JSON.stringify(personas, null, 2));

        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, persona }));
      } else {
        throw new Error('Multipart form data required');
      }
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // DELETE /api/personas/:id — Remove persona
  if (pathname.match(/^\/api\/personas\/[^/]+$/) && req.method === 'DELETE') {
    try {
      const personaId = pathname.split('/')[3];
      const personasDir = path.join(PIPELINE_DIR, 'assets', 'personas');
      const personasFile = path.join(personasDir, 'personas.json');
      if (fs.existsSync(personasFile)) {
        let personas = JSON.parse(fs.readFileSync(personasFile, 'utf8'));
        const persona = personas.find(p => p.id === personaId);
        // Delete photo file
        if (persona && persona.photo) {
          const photoPath = path.join(personasDir, persona.photo);
          if (fs.existsSync(photoPath)) fs.unlinkSync(photoPath);
        }
        personas = personas.filter(p => p.id !== personaId);
        fs.writeFileSync(personasFile, JSON.stringify(personas, null, 2));
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // GET /api/personas/:id/photo — Serve persona photo
  if (pathname.match(/^\/api\/personas\/[^/]+\/photo$/) && req.method === 'GET') {
    const personaId = pathname.split('/')[3];
    const personasDir = path.join(PIPELINE_DIR, 'assets', 'personas');
    const personasFile = path.join(personasDir, 'personas.json');
    if (fs.existsSync(personasFile)) {
      const personas = JSON.parse(fs.readFileSync(personasFile, 'utf8'));
      const persona = personas.find(p => p.id === personaId);
      if (persona && persona.photo) {
        const photoPath = path.join(personasDir, persona.photo);
        if (fs.existsSync(photoPath)) {
          const ext = path.extname(photoPath).toLowerCase();
          const mime = ext === '.png' ? 'image/png' : 'image/jpeg';
          res.writeHead(200, { 'Content-Type': mime, 'Cache-Control': 'public, max-age=86400' });
          fs.createReadStream(photoPath).pipe(res);
          return;
        }
      }
    }
    res.writeHead(404); res.end('Not found');
    return;
  }

  // ── Channel Setup Wizard ──

  // POST /api/channels/wizard/description — Generate channel description
  if (pathname === '/api/channels/wizard/description' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const { name, niche, audience, tone, expertise, goals } = body;

      const result = await callClaude(
        'Ты — эксперт по YouTube-каналам. Напиши описание канала для YouTube. Ответ СТРОГО JSON: {"description": "текст до 1000 символов", "short_description": "текст до 150 символов для мета-описания", "keywords": ["тег1","тег2"]}',
        `Создай описание YouTube-канала.
Название: ${name || 'не указано'}
Ниша: ${niche || 'не указана'}
Целевая аудитория: ${audience || 'не указана'}
Тон подачи: ${tone || 'экспертный, дружелюбный'}
Экспертиза автора: ${expertise || 'не указана'}
Цели канала: ${goals || 'не указаны'}

Требования:
- Описание до 1000 символов (основное для YouTube)
- Короткое описание до 150 символов (для поисковиков)
- 10-15 ключевых слов/тегов для канала
- Русский язык
- Включи CTA (подписка, уведомления)
- Упомяни что зритель получит от канала`
      );

      let data = {};
      try {
        const match = result.match(/\{[\s\S]*\}/);
        if (match) data = JSON.parse(match[0]);
      } catch(e) { data = { description: result }; }

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, ...data }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/channels/wizard/banner — Generate channel banner prompt
  if (pathname === '/api/channels/wizard/banner' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const { name, niche, audience, tone, style_preference } = body;

      const result = await callClaude(
        'Ты — дизайнер YouTube-баннеров. Создай промпт для AI-генерации баннера канала. Ответ СТРОГО JSON: {"prompt": "English prompt for AI image generator", "text_on_banner": "текст на баннере (рус)", "color_scheme": ["#hex1","#hex2","#hex3"], "layout_description": "описание композиции на русском"}',
        `Создай промпт для баннера YouTube-канала (2560×1440).
Название канала: ${name}
Ниша: ${niche}
Аудитория: ${audience}
Тон: ${tone || 'профессиональный'}
Стиль: ${style_preference || 'современный, минималистичный'}

Требования к баннеру:
- Размер 2560×1440 (YouTube banner)
- Безопасная зона для текста: центр 1546×423
- Крупное название канала
- Краткий слоган (1 строка)
- Профессиональный вид
- Должен работать на мобильных (центральная часть)`
      );

      let data = {};
      try {
        const match = result.match(/\{[\s\S]*\}/);
        if (match) data = JSON.parse(match[0]);
      } catch(e) { data = { prompt: result }; }

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, ...data }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/channels/wizard/avatar — Generate channel avatar prompt
  if (pathname === '/api/channels/wizard/avatar' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const { name, niche, style_preference, use_photo } = body;

      const result = await callClaude(
        'Ты — дизайнер. Создай промпт для AI-генерации аватара YouTube-канала. Ответ СТРОГО JSON: {"prompt": "English prompt for AI (800x800 square)", "style": "тип: logo/photo/illustration", "colors": ["#hex1","#hex2"], "description": "описание на русском"}',
        `Создай промпт для аватара YouTube-канала (800×800).
Название канала: ${name}
Ниша: ${niche}
Стиль: ${style_preference || 'современный логотип'}
Фото эксперта: ${use_photo ? 'да, использовать фото' : 'нет, сделать логотип/иконку'}

Требования:
- Квадрат 800×800
- Хорошо читается в маленьком размере (32px)
- Узнаваемый на обложках видео рядом с названием
- Если логотип: простой, 1-2 цвета, без мелких деталей
- Если фото: стилизованное, с цветовым акцентом`
      );

      let data = {};
      try {
        const match = result.match(/\{[\s\S]*\}/);
        if (match) data = JSON.parse(match[0]);
      } catch(e) { data = { prompt: result }; }

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, ...data }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/generate-image — Generate image via Recraft API
  if (pathname === '/api/generate-image' && req.method === 'POST') {
    if (!isAuthenticated(req)) { res.writeHead(401); res.end('Unauthorized'); return; }
    try {
      const body = JSON.parse(await readBody(req));
      const { prompt, width, height, filename } = body;
      if (!prompt) throw new Error('Prompt required');

      // Use Python to generate via Recraft
      const script = `
import os, sys, requests, json
from PIL import Image
import io
from dotenv import load_dotenv
load_dotenv()

RECRAFT_API_KEY = os.getenv('RECRAFT_API_KEY', '')
FAL_KEY = os.getenv('FAL_KEY', '')

prompt = ${JSON.stringify(prompt)}
width = ${width || 1280}
height = ${height || 720}
filename = ${JSON.stringify(filename || 'generated.jpg')}

output_dir = os.path.join(os.path.dirname(__file__), 'assets', 'generated')
os.makedirs(output_dir, exist_ok=True)
output_path = os.path.join(output_dir, filename)

# Try Recraft first
if RECRAFT_API_KEY:
    try:
        # Recraft supports specific sizes
        size_map = {
            (2560, 1440): '1820x1024',
            (800, 800): '1024x1024',
            (1280, 720): '1820x1024',
        }
        recraft_size = size_map.get((width, height), '1024x1024')
        resp = requests.post('https://external.api.recraft.ai/v1/images/generations', headers={
            'Authorization': f'Bearer {RECRAFT_API_KEY}',
            'Content-Type': 'application/json',
        }, json={
            'prompt': prompt,
            'style': 'digital_illustration',
            'size': recraft_size,
            'response_format': 'url',
        }, timeout=120)
        resp.raise_for_status()
        image_url = resp.json()['data'][0]['url']
        img_resp = requests.get(image_url, timeout=60)
        img = Image.open(io.BytesIO(img_resp.content))
        img = img.resize((width, height), Image.LANCZOS)
        img.save(output_path, 'JPEG', quality=95)
        print(json.dumps({'ok': True, 'path': output_path, 'url': '/api/assets/generated/' + filename}))
        sys.exit(0)
    except Exception as e:
        print(json.dumps({'ok': False, 'error': f'Recraft: {e}'}), file=sys.stderr)

# Try fal.ai
if FAL_KEY:
    try:
        import fal_client
        os.environ['FAL_KEY'] = FAL_KEY
        ar = f'{width}:{height}'
        if width > height: ar = '16:9'
        elif width == height: ar = '1:1'
        result = fal_client.subscribe('fal-ai/flux/schnell', arguments={
            'prompt': prompt,
            'image_size': {'width': width, 'height': height},
            'num_images': 1,
        })
        image_url = result['images'][0]['url']
        img_resp = requests.get(image_url, timeout=60)
        img = Image.open(io.BytesIO(img_resp.content))
        img = img.resize((width, height), Image.LANCZOS)
        img.save(output_path, 'JPEG', quality=95)
        print(json.dumps({'ok': True, 'path': output_path, 'url': '/api/assets/generated/' + filename}))
        sys.exit(0)
    except Exception as e:
        print(json.dumps({'ok': False, 'error': f'fal.ai: {e}'}), file=sys.stderr)

print(json.dumps({'ok': False, 'error': 'No image generation API available'}))
`;
      const scriptPath = path.join(PIPELINE_DIR, '_gen_image.py');
      fs.writeFileSync(scriptPath, script);

      // Run Python directly (not through agent.py)
      const output = await new Promise((resolve, reject) => {
        const venvUnix = path.join(PIPELINE_DIR, '.venv', 'bin', 'python3');
        const venvWin = path.join(PIPELINE_DIR, '.venv', 'Scripts', 'python.exe');
        const python = fs.existsSync(venvUnix) ? venvUnix : fs.existsSync(venvWin) ? venvWin : (process.platform === 'win32' ? 'python' : 'python3');
        const proc = spawn(python, [scriptPath], { cwd: PIPELINE_DIR, env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' } });
        let stdout = '', stderr = '';
        proc.stdout.on('data', d => stdout += d);
        proc.stderr.on('data', d => stderr += d);
        proc.on('close', code => { if (code === 0) resolve(stdout.trim()); else reject(new Error(stderr || stdout || `Exit ${code}`)); });
        proc.on('error', reject);
      });
      // Clean up
      try { fs.unlinkSync(scriptPath); } catch(e) {}

      let result = { ok: false };
      try {
        const jsonMatch = output.match(/\{[\s\S]*\}/);
        if (jsonMatch) result = JSON.parse(jsonMatch[0]);
      } catch(e) {}

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(result));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // Serve generated assets
  if (pathname.startsWith('/api/assets/generated/') && req.method === 'GET') {
    const file = pathname.replace('/api/assets/generated/', '');
    const filePath = path.join(PIPELINE_DIR, 'assets', 'generated', file);
    if (fs.existsSync(filePath)) {
      res.writeHead(200, { 'Content-Type': 'image/jpeg', 'Cache-Control': 'public, max-age=3600' });
      fs.createReadStream(filePath).pipe(res);
    } else {
      res.writeHead(404); res.end('Not found');
    }
    return;
  }

  // ── Sources Management ──

  // GET /api/project/:id/sources — List project sources (auto-populate from research)
  if (pathname.match(/^\/api\/project\/[^/]+\/sources$/) && req.method === 'GET') {
    const projectId = pathname.split('/')[3];
    const filePath = path.join(DATA_DIR, projectId, 'sources.json');

    // Auto-populate from research if sources empty/missing
    if (!fs.existsSync(filePath) || JSON.parse(fs.readFileSync(filePath, 'utf8')).items.length === 0) {
      const stateFile = path.join(DATA_DIR, projectId, 'state.json');
      if (fs.existsSync(stateFile)) {
        try {
          const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
          const research = state.steps?.research?.data;
          if (research && (research._breakthroughs || research.competitors)) {
            const items = [];
            const seen = new Set();
            // Add breakthroughs
            for (const v of (research._breakthroughs || []).slice(0, 5)) {
              if (!v.video_id || seen.has(v.video_id)) continue;
              seen.add(v.video_id);
              items.push({
                id: String(Date.now()) + items.length,
                type: 'youtube', url: `https://youtube.com/watch?v=${v.video_id}`,
                title: v.title || '', status: 'pending', auto_added: true,
                views: v.views || 0, breakthrough_score: v.breakthrough_score || 0,
              });
            }
            // Add top competitors
            for (const c of (research.competitors || []).slice(0, 3)) {
              if (!c.video_id || seen.has(c.video_id)) continue;
              seen.add(c.video_id);
              items.push({
                id: String(Date.now()) + items.length,
                type: 'youtube', url: `https://youtube.com/watch?v=${c.video_id}`,
                title: c.video_title || '', status: 'pending', auto_added: true,
              });
            }
            if (items.length > 0) {
              const projDir = path.join(DATA_DIR, projectId);
              fs.mkdirSync(projDir, { recursive: true });
              fs.writeFileSync(filePath, JSON.stringify({ items, extracted: null }, null, 2));
            }
          }
        } catch(e) {}
      }
    }

    if (fs.existsSync(filePath)) {
      const data = JSON.parse(fs.readFileSync(filePath, 'utf8'));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, ...data }));
    } else {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, items: [], extracted: null }));
    }
    return;
  }

  // POST /api/project/:id/sources — Add source (url or text)
  if (pathname.match(/^\/api\/project\/[^/]+\/sources$/) && req.method === 'POST') {
    try {
      const projectId = pathname.split('/')[3];
      const body = JSON.parse(await readBody(req));
      const filePath = path.join(DATA_DIR, projectId, 'sources.json');

      // Load existing
      let sourcesData = { items: [], extracted: null };
      if (fs.existsSync(filePath)) {
        sourcesData = JSON.parse(fs.readFileSync(filePath, 'utf8'));
      }

      // Add new item
      const newItem = {
        id: String(Date.now()),
        type: body.type || 'url', // 'url', 'text', or 'notebook'
        url: body.url || '',
        content: body.content || '',
        notebook_id: body.notebook_id || '',
        title: body.title || '',
        status: 'pending',
        added_at: new Date().toISOString(),
      };
      sourcesData.items.push(newItem);

      // Ensure project dir exists
      const projDir = path.join(DATA_DIR, projectId);
      fs.mkdirSync(projDir, { recursive: true });
      fs.writeFileSync(filePath, JSON.stringify(sourcesData, null, 2));

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, item: newItem, total: sourcesData.items.length }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // DELETE /api/project/:id/sources/:sourceId — Remove source
  if (pathname.match(/^\/api\/project\/[^/]+\/sources\/[^/]+$/) && req.method === 'DELETE') {
    try {
      const parts = pathname.split('/');
      const projectId = parts[3];
      const sourceId = parts[5];
      const filePath = path.join(DATA_DIR, projectId, 'sources.json');

      if (fs.existsSync(filePath)) {
        const sourcesData = JSON.parse(fs.readFileSync(filePath, 'utf8'));
        sourcesData.items = sourcesData.items.filter(i => i.id !== sourceId);
        fs.writeFileSync(filePath, JSON.stringify(sourcesData, null, 2));
      }

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // Modify POST /api/new to accept channel_id — handled below in existing endpoint

  // POST /api/express/generate — Generate titles, descriptions for quick publish
  if (pathname === '/api/express/generate' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const topic = body.topic || '';
      const brief = body.brief || '';
      const script = body.script || '';
      const channelName = body.channel_name || '';

      // Read channel context for author style
      let ctx = {};
      try { ctx = JSON.parse(fs.readFileSync(CONTEXT_FILE, 'utf8')); } catch(e) {}

      const authorInfo = ctx.author
        ? `Автор: ${ctx.author.full_name || ctx.author.name}. ${ctx.author.who || ''}. Опыт: ${ctx.author.experience || ''}. Тон: ${ctx.author.tone || 'экспертный'}.`
        : '';

      const hasScript = script.length > 50;

      const systemPrompt = `You are a YouTube SEO and content specialist for a Russian-language channel.
${authorInfo}
Generate content in Russian. Return ONLY valid JSON, no markdown, no explanation.
${hasScript ? 'IMPORTANT: The user provided the actual script/text of the video. Base ALL titles and descriptions on the REAL content of this script. Extract key points, insights, and quotes from the script.' : ''}`;

      // Truncate script to ~6000 chars to fit in context
      const scriptExcerpt = script.length > 6000 ? script.substring(0, 6000) + '\n\n[...текст обрезан...]' : script;

      const userMessage = `Тема видео: "${topic}"
${brief ? `Краткое описание: ${brief}` : ''}
${channelName ? `Канал: ${channelName}` : ''}
${hasScript ? `\n═══ ТЕКСТ / СЦЕНАРИЙ РОЛИКА ═══\n${scriptExcerpt}\n═══ КОНЕЦ ТЕКСТА ═══\n` : ''}

Сгенерируй JSON:
{
  "titles": ["заголовок1", "заголовок2", "заголовок3", "заголовок4", "заголовок5"],
  "descriptions": ["описание1 (200-400 слов с SEO, таймкодами-заглушками, хештегами)", "описание2", "описание3"],
  "tags": ["тег1", "тег2", "тег3", "тег4", "тег5", "тег6", "тег7", "тег8", "тег9", "тег10", "тег11", "тег12", "тег13", "тег14", "тег15"]
}

Правила для заголовков:
- 5 вариантов, кликбейтные но честные
- До 70 символов
- Используй цифры, эмоции, интригу
- Разные подходы: вопрос, утверждение, провокация, список, шок
${hasScript ? '- ОБЯЗАТЕЛЬНО основывай заголовки на реальном содержании сценария, не выдумывай факты' : ''}

Правила для описаний:
- 3 варианта
- SEO-оптимизированные
- Включить: краткое содержание, таймкоды-заглушки (00:00 Начало, 01:30 ...), хештеги, призыв подписаться
${hasScript ? '- Описания должны точно отражать содержание сценария, упоминать конкретные темы и тезисы из текста' : ''}
- Стиль: ${ctx.author?.tone || 'экспертный но дружелюбный'}

Правила для тегов:
- 15-20 тегов для YouTube SEO
- Микс: широкие (1-2 слова) + длинные ключевые фразы (3-5 слов)
- На русском языке, некоторые на английском если тема международная
- Релевантные теме и содержанию видео`;

      const result = await callClaude(systemPrompt, userMessage);

      // Parse JSON from Claude response
      let parsed;
      try {
        // Try to extract JSON if wrapped in markdown
        const jsonMatch = result.match(/\{[\s\S]*\}/);
        parsed = JSON.parse(jsonMatch ? jsonMatch[0] : result);
        console.log('[Express Generate] Parsed OK, titles:', (parsed.titles || []).length, 'descriptions:', (parsed.descriptions || []).length);
      } catch(e) {
        console.error('[Express Generate] JSON parse failed:', result.substring(0, 300));
        parsed = { titles: [], descriptions: [], raw: result };
      }

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, ...parsed }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/express/cover-prompts — Generate cover prompts WITHOUT images (for approval)
  if (pathname === '/api/express/cover-prompts' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const topic = body.topic || '';
      const title = body.title || topic;
      const script = body.script || '';
      const description = body.description || '';
      const count = Math.min(body.count || 3, 5);

      console.log('[Cover Prompts] Generating prompts for approval, topic:', topic, 'count:', count);

      const promptResult = await callClaude(
        `You generate creative prompts for YouTube thumbnail images. Return ONLY valid JSON, no markdown.`,
        `Video title: "${title}"
${script ? `Script excerpt: ${script.substring(0, 2000)}` : ''}
${description ? `Description: ${description.substring(0, 500)}` : ''}

Generate ${count} DIFFERENT creative prompts for YouTube thumbnail images.

Return JSON array of objects:
[
  {
    "prompt": "Full English prompt describing the visual scene (person pose, background, lighting, mood, effects)",
    "text_on_image": "2-4 слова НА РУССКОМ для текста на обложке (КРУПНЫЙ, кликбейтный)",
    "emotion": "emotion/mood (e.g. shocked, confident, excited)",
    "style_hint": "visual style keywords (e.g. neon glow, cinematic, bright colors)",
    "concept": "Краткое описание концепции на русском (1 предложение)"
  }
]

Each prompt must have a DIFFERENT visual concept:
- Different background (office, studio, dark, bright, outdoor, abstract)
- Different pose (pointing, arms crossed, surprised face, thinking)
- Different mood (dramatic, energetic, mysterious, confident)
- Different color scheme`
      );

      let prompts = [];
      const match = promptResult.match(/\[[\s\S]*\]/);
      if (match) {
        prompts = JSON.parse(match[0]);
      }

      console.log('[Cover Prompts] Generated', prompts.length, 'prompts');
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, prompts }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/express/covers — Generate covers for quick publish (accepts pre-approved prompts)
  if (pathname === '/api/express/covers' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const topic = body.topic || '';
      const title = body.title || topic;
      const script = body.script || '';
      const description = body.description || '';
      const styleId = body.style_id || '';
      const count = Math.min(body.count || 3, 5);

      // Use pre-approved prompts if provided, otherwise generate via Claude
      console.log('[Express Covers] Starting generation, topic:', topic, 'title:', title, 'count:', count);
      let coverPrompts = [];

      if (body.prompts && body.prompts.length > 0) {
        // Pre-approved prompts from the approval step
        console.log('[Express Covers] Using', body.prompts.length, 'pre-approved prompts');
        coverPrompts = body.prompts.map(p => typeof p === 'string' ? p : p.prompt || '');
      } else {
        // Fallback: generate via Claude
        const contextText = script || description || topic;
        if (contextText.length > 30) {
          try {
            console.log('[Express Covers] Generating smart prompts via Claude...');
            const promptResult = await callClaude(
              `You generate creative prompts for YouTube thumbnail images. Return ONLY valid JSON array of strings, no markdown. Each prompt describes a visual scene for a thumbnail photo.`,
              `Video title: "${title}"
${script ? `Script excerpt: ${script.substring(0, 2000)}` : ''}
${description ? `Description: ${description.substring(0, 500)}` : ''}

Generate ${count} DIFFERENT creative prompts for YouTube thumbnail images. Each prompt should:
- Describe a dramatic, eye-catching scene with the person (expert) relevant to the video topic
- Include specific pose, emotion, background, lighting
- Include text to render on the image (short, bold, in Russian)
- Each prompt should have a DIFFERENT visual concept (different background, pose, mood)
- Write in English, except text-on-image which should be in Russian

Return JSON: ["prompt1", "prompt2", "prompt3"]`
            );
            const promptMatch = promptResult.match(/\[[\s\S]*\]/);
            if (promptMatch) coverPrompts = JSON.parse(promptMatch[0]);
          } catch(e) {
            console.error('Cover prompt gen error:', e.message);
          }
        }
      }
      console.log('[Express Covers] Got', coverPrompts.length, 'prompts, generating images...');

      // Create temp express project dir
      const expressId = 'express-' + Date.now();
      const expressDir = path.join(DATA_DIR, expressId, 'thumbnails');
      fs.mkdirSync(expressDir, { recursive: true });

      const covers = [];
      for (let i = 0; i < count; i++) {
        const smartPrompt = coverPrompts[i] || `YouTube thumbnail for video "${title}". Vibrant, eye-catching, clickable. Person with expressive face, dramatic lighting, bold text "${title}".`;
        const params = JSON.stringify({
          prompt: smartPrompt,
          text_on_image: '',
          style_id: styleId,
          text_style_id: '',
          neon_color: ['#00BFFF', '#FF00FF', '#FFD700'][i % 3],
          clothing_id: '',
          clothing_url: '',
        });
        try {
          console.log(`[Express Covers] Generating cover ${i+1}/${count}...`);
          await runAgent(['generate-cover-custom', expressId, params]);
          console.log(`[Express Covers] Cover ${i+1} done`);
        } catch(e) {
          console.error(`Cover gen error ${i+1}:`, e.message);
        }
      }

      // Collect generated files
      if (fs.existsSync(expressDir)) {
        const files = fs.readdirSync(expressDir).filter(f => f.startsWith('custom_')).sort();
        for (const f of files) {
          covers.push(`/api/file/${expressId}/thumbnails/${f}`);
        }
      }

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, covers, express_id: expressId }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/upload-audio — Upload audio track for dubbing (mp3/wav, much lighter than video)
  if (pathname === '/api/upload-audio' && req.method === 'POST') {
    try {
      const contentType = req.headers['content-type'] || '';
      const boundary = contentType.split('boundary=')[1];
      if (!boundary) { res.writeHead(400); res.end('No boundary'); return; }

      const raw = await new Promise((resolve, reject) => {
        const chunks = [];
        req.on('data', c => chunks.push(c));
        req.on('end', () => resolve(Buffer.concat(chunks)));
        req.on('error', reject);
      });

      const rawStr = raw.toString('latin1');
      const parts = rawStr.split('--' + boundary).filter(p => p.includes('Content-Disposition'));

      let projectId = '';
      let audioBuffer = null;
      let audioFilename = 'source_audio.mp3';

      for (const part of parts) {
        if (part.includes('name="project_id"')) {
          const hEnd = part.indexOf('\r\n\r\n');
          if (hEnd >= 0) projectId = part.slice(hEnd + 4).trim().replace(/\r\n--$/, '').trim();
        }
        if (part.includes('name="audio"') && part.includes('filename=')) {
          const fnMatch = part.match(/filename="([^"]+)"/);
          if (fnMatch) audioFilename = fnMatch[1];
          const hEnd = part.indexOf('\r\n\r\n');
          if (hEnd >= 0) {
            const start = raw.indexOf(Buffer.from('\r\n\r\n', 'latin1'), raw.indexOf(Buffer.from('name="audio"', 'latin1'))) + 4;
            const partBoundary = Buffer.from('\r\n--' + boundary, 'latin1');
            let end = raw.length;
            for (let i = start; i < raw.length - partBoundary.length; i++) {
              if (raw.slice(i, i + partBoundary.length).equals(partBoundary)) { end = i; break; }
            }
            audioBuffer = raw.slice(start, end);
          }
        }
      }

      if (!projectId) { res.writeHead(400, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ ok: false, error: 'No project_id' })); return; }
      if (!audioBuffer) { res.writeHead(400, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ ok: false, error: 'No audio file' })); return; }

      // Save to dubbing directory
      const dubbingDir = path.join(DATA_DIR, projectId, 'dubbing');
      fs.mkdirSync(dubbingDir, { recursive: true });
      const audioPath = path.join(dubbingDir, 'source_audio' + path.extname(audioFilename));
      fs.writeFileSync(audioPath, audioBuffer);

      console.log(`[upload-audio] ${projectId}: saved ${audioFilename} (${(audioBuffer.length/(1024*1024)).toFixed(1)} MB)`);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, path: audioPath, size: audioBuffer.length, filename: audioFilename }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/express/upload-video — Upload video file for express publish
  if (pathname === '/api/express/upload-video' && req.method === 'POST') {
    try {
      const contentType = req.headers['content-type'] || '';
      const boundary = contentType.split('boundary=')[1];
      if (!boundary) { res.writeHead(400); res.end('No boundary'); return; }

      const raw = await new Promise((resolve, reject) => {
        const chunks = [];
        req.on('data', c => chunks.push(c));
        req.on('end', () => resolve(Buffer.concat(chunks)));
        req.on('error', reject);
      });

      const rawStr = raw.toString('latin1');
      const parts = rawStr.split('--' + boundary).filter(p => p.includes('Content-Disposition'));

      let expressId = '';
      let videoBuffer = null;
      let videoFilename = 'video.mp4';

      for (const part of parts) {
        if (part.includes('name="express_id"')) {
          const hEnd = part.indexOf('\r\n\r\n');
          if (hEnd >= 0) expressId = part.slice(hEnd + 4).trim().replace(/\r\n--$/, '').trim();
        }
        if (part.includes('name="video"') && part.includes('filename=')) {
          const fnMatch = part.match(/filename="([^"]+)"/);
          if (fnMatch) videoFilename = fnMatch[1];
          const hEnd = part.indexOf('\r\n\r\n');
          if (hEnd >= 0) {
            const start = raw.indexOf(Buffer.from('\r\n\r\n', 'latin1'), raw.indexOf(Buffer.from('name="video"', 'latin1'))) + 4;
            const partBoundary = Buffer.from('\r\n--' + boundary, 'latin1');
            let end = raw.length;
            for (let i = start; i < raw.length - partBoundary.length; i++) {
              if (raw.slice(i, i + partBoundary.length).equals(partBoundary)) { end = i; break; }
            }
            videoBuffer = raw.slice(start, end);
          }
        }
      }

      if (!expressId) expressId = 'express-' + Date.now();
      if (!videoBuffer) { res.writeHead(400, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ ok: false, error: 'No video file' })); return; }

      const projDir = path.join(DATA_DIR, expressId);
      fs.mkdirSync(projDir, { recursive: true });
      const videoPath = path.join(projDir, videoFilename);
      fs.writeFileSync(videoPath, videoBuffer);

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, express_id: expressId, video_path: videoPath, filename: videoFilename, size_mb: (videoBuffer.length / 1024 / 1024).toFixed(1) }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // GET /api/express/playlists — List YouTube playlists
  if (pathname === '/api/express/playlists' && req.method === 'GET') {
    try {
      const output = await runAgent(['playlists']);
      // Parse playlists from agent output
      const playlists = [];
      const lines = output.split('\n');
      for (const line of lines) {
        const match = line.match(/^[-•]\s*(.+?)\s*\(ID:\s*([^)]+)\)/);
        if (match) playlists.push({ title: match[1].trim(), id: match[2].trim() });
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, playlists, raw: output }));
    } catch (err) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, playlists: [], error: err.message }));
    }
    return;
  }

  // POST /api/express/save-project — Save Express session as a full pipeline project
  if (pathname === '/api/express/save-project' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const topic = body.topic || 'Express Project';
      const titles = body.titles || [];
      const descriptions = body.descriptions || [];
      const tags = body.tags || [];
      const selectedTitle = body.selected_title || titles[0] || topic;
      const selectedDesc = body.selected_description || descriptions[0] || '';
      const expressId = body.express_id || '';
      const script = body.script || '';
      const selectedCoverIndex = (typeof body.selected_cover_index === 'number') ? body.selected_cover_index : -1;

      // Create project slug
      const now = new Date();
      const dateStr = now.toISOString().slice(0, 10).replace(/-/g, '');
      const slugPart = topic.toLowerCase().replace(/[^а-яa-z0-9]/gi, '-').replace(/-+/g, '-').substring(0, 50);
      const projectId = `${dateStr}-${slugPart}`;
      const projectDir = path.join(DATA_DIR, projectId);
      const alreadyExists = fs.existsSync(projectDir);

      fs.mkdirSync(path.join(projectDir, 'thumbnails'), { recursive: true });

      // Copy thumbnails from express project if exists.
      // If project already exists, we still re-copy to pick up newly generated covers.
      let copiedFiles = [];
      if (expressId) {
        const expressThumbDir = path.join(DATA_DIR, expressId, 'thumbnails');
        if (fs.existsSync(expressThumbDir)) {
          const files = fs.readdirSync(expressThumbDir).filter(f => f.endsWith('.jpg') || f.endsWith('.png'));
          files.forEach((f, i) => {
            const dst = `thumbnail_${i + 1}.jpg`;
            fs.copyFileSync(path.join(expressThumbDir, f), path.join(projectDir, 'thumbnails', dst));
            copiedFiles.push(dst);
          });
          // Pick primary thumbnail based on selectedCoverIndex (else first)
          if (files.length > 0) {
            const primaryIdx = (selectedCoverIndex >= 0 && selectedCoverIndex < files.length) ? selectedCoverIndex : 0;
            fs.copyFileSync(path.join(expressThumbDir, files[primaryIdx]), path.join(projectDir, 'thumbnail.jpg'));
          }
        }
      }

      // If project already existed, just update covers data + return existing id
      if (alreadyExists) {
        const stateFile = path.join(projectDir, 'state.json');
        if (fs.existsSync(stateFile)) {
          try {
            const existingState = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
            if (copiedFiles.length > 0) {
              existingState.steps.covers = existingState.steps.covers || { status: 'pending', data: {}, log: [] };
              existingState.steps.covers.status = 'completed';
              existingState.steps.covers.data = {
                thumbnails: copiedFiles,
                generated_files: copiedFiles,
                generation_mode: 'express',
                primary_index: selectedCoverIndex >= 0 ? selectedCoverIndex : 0,
                _from_express: true,
              };
              existingState.updated_at = now.toISOString();
              fs.writeFileSync(stateFile, JSON.stringify(existingState, null, 2), 'utf8');
            }
          } catch(e) { console.warn('save-project: existing state update failed:', e.message); }
        }
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, project_id: projectId, updated: true, copied_covers: copiedFiles.length }));
        return;
      }

      // Build steps state
      const steps = {};
      const stepKeys = ['research','sources','content_plan','references','script','teleprompter','covers','description','shooting','editing','publish','dubbing'];
      stepKeys.forEach(s => { steps[s] = { status: 'pending', data: {}, log: [] }; });

      // Pre-fill completed steps from express data
      if (titles.length || descriptions.length) {
        steps.content_plan.status = 'completed';
        steps.content_plan.data = {
          title: selectedTitle,
          titles: titles,
          hook: '',
          structure: [],
          cta: '',
          _from_express: true
        };
      }
      if (selectedDesc) {
        steps.description.status = 'completed';
        steps.description.data = {
          title: selectedTitle,
          description: selectedDesc,
          tags: tags,
          hashtags: tags.slice(0, 5),
          _from_express: true
        };
      }
      if (expressId && fs.existsSync(path.join(projectDir, 'thumbnails'))) {
        const thumbFiles = fs.readdirSync(path.join(projectDir, 'thumbnails'));
        if (thumbFiles.length > 0) {
          steps.covers.status = 'completed';
          steps.covers.data = {
            thumbnails: thumbFiles,
            generated_files: thumbFiles,
            generation_mode: 'express',
            primary_index: selectedCoverIndex >= 0 ? selectedCoverIndex : 0,
            _from_express: true
          };
        }
      }
      if (script) {
        steps.script.status = 'completed';
        steps.script.data = { raw_script: script, _from_express: true };
      }

      // Determine current step
      let currentStep = 'research';
      if (steps.content_plan.status === 'completed') currentStep = 'references';
      if (steps.covers.status === 'completed') currentStep = 'shooting';

      const state = {
        project_id: projectId,
        created_at: now.toISOString(),
        updated_at: now.toISOString(),
        topic: topic,
        channel_id: body.channel_id || '',
        current_step: currentStep,
        steps: steps
      };

      fs.writeFileSync(path.join(projectDir, 'state.json'), JSON.stringify(state, null, 2), 'utf8');

      console.log(`[Express Save] Created project ${projectId} from express session`);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, project_id: projectId }));
    } catch (err) {
      console.error('[Express Save Error]', err.message);
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/express/publish — Publish video to YouTube
  if (pathname === '/api/express/publish' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const { express_id, title, description, playlist_id, category_id, privacy, schedule } = body;

      if (!express_id) throw new Error('No express_id');
      const projDir = path.join(DATA_DIR, express_id);
      if (!fs.existsSync(projDir)) throw new Error('Express project not found');

      // Find video file
      const videoFiles = fs.readdirSync(projDir).filter(f => /\.(mp4|mov|mkv|avi|webm)$/i.test(f));
      if (videoFiles.length === 0) throw new Error('No video file found. Upload video first.');
      const videoPath = path.join(projDir, videoFiles[0]);

      // Find thumbnail (selected cover)
      let thumbnailPath = null;
      if (body.cover_url) {
        // cover_url is like /api/file/express-xxx/thumbnails/custom_1.jpg
        const coverParts = body.cover_url.replace('/api/file/', '').split('/');
        const coverFile = path.join(DATA_DIR, ...coverParts);
        if (fs.existsSync(coverFile)) thumbnailPath = coverFile;
      }

      // Write metadata for the publish step
      const stateFile = path.join(projDir, 'state.json');
      const state = {
        project_id: express_id,
        topic: title,
        created_at: new Date().toISOString(),
        current_step: 'publish',
        steps: {
          description: { status: 'completed', data: { title, description, tags: [], keywords: [] } },
          publish: { status: 'approved', data: { category_id: category_id || '22', playlist_id: playlist_id || '', publish_at: schedule || '' } },
        }
      };
      fs.writeFileSync(stateFile, JSON.stringify(state, null, 2), 'utf8');

      // Copy thumbnail to expected location
      if (thumbnailPath) {
        const thumbDest = path.join(projDir, 'thumbnail.jpg');
        fs.copyFileSync(thumbnailPath, thumbDest);
      }

      // Run publish
      const args = ['publish', express_id, '--approve'];
      if (schedule) args.push('--schedule', schedule);
      if (playlist_id) args.push('--playlist', playlist_id);
      if (category_id) args.push('--category', category_id);

      const output = await runAgent(args);

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, output }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // ── Split-Test API ──

  // POST /api/splittest/start — Start a split-test (supports legacy pairs + matrix mode)
  if (pathname === '/api/splittest/start' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const { project_id, video_id, rotation_hours, duration_hours } = body;

      if (!project_id || !video_id) {
        throw new Error('Need project_id and video_id');
      }

      // Helper: resolve thumbnail URL to absolute path
      const resolveThumb = (url) => {
        if (!url) return '';
        const thumbParts = url.replace('/api/file/', '').split('/');
        const absPath = path.join(DATA_DIR, ...thumbParts);
        return fs.existsSync(absPath) ? absPath : '';
      };

      let configData;

      if (body.mode === 'matrix' && body.titles && body.thumbnails) {
        // Matrix mode: titles × thumbnails
        const titles = body.titles;
        const thumbnails = body.thumbnails.map(resolveThumb);
        const variants = [];
        for (let ti = 0; ti < titles.length; ti++) {
          for (let ci = 0; ci < thumbnails.length; ci++) {
            if (variants.length >= 12) break;
            variants.push({
              title: titles[ti],
              thumbnail: thumbnails[ci],
              title_index: ti,
              thumbnail_index: ci,
            });
          }
        }
        if (variants.length < 2) throw new Error('Need at least 2 variants (add more titles or thumbnails)');
        configData = {
          video_id, mode: 'matrix', titles, thumbnails,
          variants, rotation_hours: rotation_hours || 6, duration_hours: duration_hours || 72
        };
      } else {
        // Legacy pairs mode
        const variants = body.variants || [];
        if (variants.length < 2) throw new Error('Need at least 2 variants');
        const resolvedVariants = variants.map(v => ({
          title: v.title || '',
          thumbnail: resolveThumb(v.thumbnail),
        }));
        configData = {
          video_id, variants: resolvedVariants,
          rotation_hours: rotation_hours || 6, duration_hours: duration_hours || 72
        };
      }

      // Write config, then run Python splittest
      const configPath = path.join(DATA_DIR, project_id, 'splittest_config.json');
      fs.writeFileSync(configPath, JSON.stringify(configData));

      const output = await runAgent(['splittest-start', project_id]);

      // Read result
      const testFile = path.join(DATA_DIR, project_id, 'splittest.json');
      const testData = fs.existsSync(testFile) ? JSON.parse(fs.readFileSync(testFile, 'utf8')) : null;

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, test: testData, output }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/project/:id/selected-titles — Save selected titles
  if (pathname.match(/^\/api\/project\/[^/]+\/selected-titles$/) && req.method === 'POST') {
    try {
      const projectId = pathname.split('/')[3];
      const body = JSON.parse(await readBody(req));
      const filePath = path.join(DATA_DIR, projectId, 'selected_titles.json');
      fs.writeFileSync(filePath, JSON.stringify(body, null, 2));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // GET /api/project/:id/selected-titles — Get selected titles
  if (pathname.match(/^\/api\/project\/[^/]+\/selected-titles$/) && req.method === 'GET') {
    const projectId = pathname.split('/')[3];
    const filePath = path.join(DATA_DIR, projectId, 'selected_titles.json');
    if (fs.existsSync(filePath)) {
      const data = JSON.parse(fs.readFileSync(filePath, 'utf8'));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, ...data }));
    } else {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, titles: [], selectedIndices: [] }));
    }
    return;
  }

  // POST /api/project/:id/selected-angle — Save selected research angle
  if (pathname.match(/^\/api\/project\/[^/]+\/selected-angle$/) && req.method === 'POST') {
    try {
      const projectId = pathname.split('/')[3];
      const body = JSON.parse(await readBody(req));
      const filePath = path.join(DATA_DIR, projectId, 'selected_angle.json');
      fs.writeFileSync(filePath, JSON.stringify(body, null, 2));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // GET /api/project/:id/selected-angle — Get selected angle
  if (pathname.match(/^\/api\/project\/[^/]+\/selected-angle$/) && req.method === 'GET') {
    const projectId = pathname.split('/')[3];
    const filePath = path.join(DATA_DIR, projectId, 'selected_angle.json');
    if (fs.existsSync(filePath)) {
      const data = JSON.parse(fs.readFileSync(filePath, 'utf8'));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, ...data }));
    } else {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, angle: null }));
    }
    return;
  }

  // POST /api/project/:id/expert-notes — Save expert notes
  if (pathname.match(/^\/api\/project\/[^/]+\/expert-notes$/) && req.method === 'POST') {
    try {
      const projectId = pathname.split('/')[3];
      const body = JSON.parse(await readBody(req));
      const filePath = path.join(DATA_DIR, projectId, 'expert_notes.json');
      fs.writeFileSync(filePath, JSON.stringify(body, null, 2));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // GET /api/project/:id/expert-notes — Get expert notes
  if (pathname.match(/^\/api\/project\/[^/]+\/expert-notes$/) && req.method === 'GET') {
    const projectId = pathname.split('/')[3];
    const filePath = path.join(DATA_DIR, projectId, 'expert_notes.json');
    if (fs.existsSync(filePath)) {
      const data = JSON.parse(fs.readFileSync(filePath, 'utf8'));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, ...data }));
    } else {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, notes: [] }));
    }
    return;
  }

  // POST /api/project/:id/selected-hook — Save selected hook
  if (pathname.match(/^\/api\/project\/[^/]+\/selected-hook$/) && req.method === 'POST') {
    try {
      const projectId = pathname.split('/')[3];
      const body = JSON.parse(await readBody(req));
      const filePath = path.join(DATA_DIR, projectId, 'selected_hook.json');
      fs.writeFileSync(filePath, JSON.stringify(body, null, 2));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // GET /api/project/:id/selected-hook — Get selected hook
  if (pathname.match(/^\/api\/project\/[^/]+\/selected-hook$/) && req.method === 'GET') {
    const projectId = pathname.split('/')[3];
    const filePath = path.join(DATA_DIR, projectId, 'selected_hook.json');
    if (fs.existsSync(filePath)) {
      const data = JSON.parse(fs.readFileSync(filePath, 'utf8'));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, ...data }));
    } else {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, hookIndex: -1 }));
    }
    return;
  }

  // GET /api/project/:id/references — List reference thumbnails
  if (pathname.match(/^\/api\/project\/[^/]+\/references$/) && req.method === 'GET') {
    const projectId = pathname.split('/')[3];
    const refsDir = path.join(DATA_DIR, projectId, 'references');
    if (fs.existsSync(refsDir)) {
      const files = fs.readdirSync(refsDir)
        .filter(f => f.endsWith('.jpg') || f.endsWith('.png'))
        .sort((a, b) => {
          // Sort by view count (filename starts with Nk_)
          const va = parseInt(a.split('k_')[0]) || 0;
          const vb = parseInt(b.split('k_')[0]) || 0;
          return vb - va;
        })
        .map(f => `/api/file/${projectId}/references/${f}`);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, files }));
    } else {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, files: [] }));
    }
    return;
  }

  // GET /api/splittest/status/:project_id — Get split-test status
  if (pathname.startsWith('/api/splittest/status/') && req.method === 'GET') {
    const projectId = pathname.replace('/api/splittest/status/', '');
    const testFile = path.join(DATA_DIR, projectId, 'splittest.json');
    if (fs.existsSync(testFile)) {
      const testData = JSON.parse(fs.readFileSync(testFile, 'utf8'));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, test: testData }));
    } else {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, test: null }));
    }
    return;
  }

  // POST /api/splittest/stop — Stop test and pick winner
  if (pathname === '/api/splittest/stop' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const { project_id, winner_index } = body;
      const method = winner_index !== undefined ? 'manual' : 'auto';
      const args = ['splittest-finish', project_id, method];
      if (winner_index !== undefined) args.push(String(winner_index));
      const output = await runAgent(args);

      const testFile = path.join(DATA_DIR, project_id, 'splittest.json');
      const testData = fs.existsSync(testFile) ? JSON.parse(fs.readFileSync(testFile, 'utf8')) : null;

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, test: testData, output }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // GET /api/projects — list all
  if (pathname === '/api/projects' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, projects: listProjects() }));
    return;
  }

  // GET /api/project/:id — detail
  if (pathname.startsWith('/api/project/') && req.method === 'GET') {
    const id = pathname.split('/')[3];
    const state = readState(id);
    if (!state) { res.writeHead(404); res.end('Not found'); return; }
    const files = listProjectFiles(id);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, state, files }));
    return;
  }

  // POST /api/new — create project
  if (pathname === '/api/new' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const args = ['new', body.topic];
      if (body.channel_id) args.push('--channel', body.channel_id);
      const output = await runAgent(args);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, output }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/run-step — fire Python agent in background; client polls /api/project/:id
  if (pathname === '/api/run-step' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      // For dubbing step: save language config before running
      if (body.step === 'dubbing' && body.languages && body.languages.length > 0) {
        const configFile = path.join(DATA_DIR, body.project_id, 'dubbing_config.json');
        fs.writeFileSync(configFile, JSON.stringify({ languages: body.languages, auto_publish: false }, null, 2), 'utf8');
      }

      // Mark step as in_progress immediately so UI polling sees it
      const state = readState(body.project_id);
      if (state && state.steps[body.step]) {
        state.steps[body.step].status = 'in_progress';
        state.steps[body.step].log = state.steps[body.step].log || [];
        state.steps[body.step].log.push({ time: new Date().toISOString(), message: 'Queued via API' });
        state.updated_at = new Date().toISOString();
        fs.writeFileSync(path.join(DATA_DIR, body.project_id, 'state.json'), JSON.stringify(state, null, 2), 'utf8');
      }

      // Fire agent in background (no await)
      runAgent(['step', body.project_id, body.step])
        .then(output => {
          console.log(`[run-step] ${body.project_id}/${body.step} completed, output length:`, output.length);
        })
        .catch(err => {
          console.error(`[run-step] ${body.project_id}/${body.step} failed:`, err.message);
          // Python agent should have marked status=failed in state; nothing more to do here
        });

      // Respond immediately — client will poll state
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, started: true, async: true, step: body.step, state }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/run-all — run all auto steps
  if (pathname === '/api/run-all' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const output = await runAgent(['run', body.project_id]);
      const state = readState(body.project_id);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, output, state }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/reset-step — reset a step back to pending
  if (pathname === '/api/reset-step' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const state = readState(body.project_id);
      if (!state) { res.writeHead(404); res.end(JSON.stringify({ ok: false, error: 'Project not found' })); return; }
      const step = body.step;
      if (!state.steps[step]) { res.writeHead(400); res.end(JSON.stringify({ ok: false, error: 'Unknown step' })); return; }
      state.steps[step].status = 'pending';
      state.steps[step].data = {};
      state.steps[step].log.push({ time: new Date().toISOString(), message: 'Reset to pending (manual restart)' });
      state.updated_at = new Date().toISOString();
      const stateFile = path.join(DATA_DIR, body.project_id, 'state.json');
      fs.writeFileSync(stateFile, JSON.stringify(state, null, 2), 'utf8');
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/shot-done
  if (pathname === '/api/shot-done' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const output = await runAgent(['shot-done', body.project_id]);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, output }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/edit-done
  if (pathname === '/api/edit-done' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const args = ['edit-done', body.project_id];
      if (body.video_file) args.push(body.video_file);
      const output = await runAgent(args);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, output }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // GET /api/context — get channel context
  if (pathname === '/api/context' && req.method === 'GET') {
    try {
      const ctx = fs.existsSync(CONTEXT_FILE)
        ? JSON.parse(fs.readFileSync(CONTEXT_FILE, 'utf8'))
        : {};
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, context: ctx }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/context — save channel context
  if (pathname === '/api/context' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      fs.writeFileSync(CONTEXT_FILE, JSON.stringify(body.context || body, null, 2), 'utf8');
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/upload-expert?channel_id=X — upload expert photo
  // If channel_id given, saves to data/channels/<id>/expert.jpg (per-channel).
  // Otherwise saves to global assets/expert.jpg.
  if (pathname === '/api/upload-expert' && req.method === 'POST') {
    try {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const rawBuffer = Buffer.concat(chunks);
      const channelId = url.searchParams.get('channel_id') || '';
      let assetsDir, expertPath;
      if (channelId) {
        assetsDir = path.join(DATA_DIR, 'channels', channelId);
        if (!fs.existsSync(assetsDir)) fs.mkdirSync(assetsDir, { recursive: true });
        expertPath = path.join(assetsDir, 'expert.jpg');
      } else {
        assetsDir = path.join(PIPELINE_DIR, 'assets');
        if (!fs.existsSync(assetsDir)) fs.mkdirSync(assetsDir, { recursive: true });
        expertPath = path.join(assetsDir, 'expert.jpg');
      }

      const contentType = req.headers['content-type'] || '';
      if (contentType.includes('multipart/form-data')) {
        // Parse multipart boundary
        const boundaryMatch = contentType.match(/boundary=(.+)/);
        if (!boundaryMatch) throw new Error('No boundary in multipart');
        const boundary = boundaryMatch[1];
        const parts = rawBuffer.toString('binary').split('--' + boundary);
        for (const part of parts) {
          if (part.includes('filename=') && (part.includes('image/') || part.includes('application/octet'))) {
            const headerEnd = part.indexOf('\r\n\r\n');
            if (headerEnd >= 0) {
              let fileData = part.slice(headerEnd + 4);
              if (fileData.endsWith('\r\n')) fileData = fileData.slice(0, -2);
              fs.writeFileSync(expertPath, Buffer.from(fileData, 'binary'));
              break;
            }
          }
        }
      } else {
        fs.writeFileSync(expertPath, rawBuffer);
      }

      const exists = fs.existsSync(expertPath);
      const size = exists ? fs.statSync(expertPath).size : 0;
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, path: expertPath, size }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // GET /api/styles — list all styles
  if (pathname === '/api/styles' && req.method === 'GET') {
    const styles = readStylesMeta();
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, styles }));
    return;
  }

  // POST /api/upload-style — upload a style reference image (multipart)
  if (pathname === '/api/upload-style' && req.method === 'POST') {
    try {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const rawBuffer = Buffer.concat(chunks);
      ensureStylesDir();

      const contentType = req.headers['content-type'] || '';
      let styleName = 'style_' + Date.now();
      let fileData = rawBuffer;
      let promptHint = '';

      if (contentType.includes('multipart/form-data')) {
        const boundaryMatch = contentType.match(/boundary=(.+)/);
        if (!boundaryMatch) throw new Error('No boundary');
        const boundary = boundaryMatch[1];
        const parts = rawBuffer.toString('binary').split('--' + boundary);
        for (const part of parts) {
          // Extract style name field
          if (part.includes('name="name"') && !part.includes('filename=')) {
            const hEnd = part.indexOf('\r\n\r\n');
            if (hEnd >= 0) {
              let val = part.slice(hEnd + 4).trim();
              if (val.endsWith('\r\n')) val = val.slice(0, -2);
              if (val) styleName = val.trim();
            }
          }
          // Extract prompt hint field
          if (part.includes('name="prompt"') && !part.includes('filename=')) {
            const hEnd = part.indexOf('\r\n\r\n');
            if (hEnd >= 0) {
              let val = part.slice(hEnd + 4).trim();
              if (val.endsWith('\r\n')) val = val.slice(0, -2);
              promptHint = val.trim();
            }
          }
          // Extract image file
          if (part.includes('filename=') && (part.includes('image/') || part.includes('application/octet'))) {
            const headerEnd = part.indexOf('\r\n\r\n');
            if (headerEnd >= 0) {
              let data = part.slice(headerEnd + 4);
              if (data.endsWith('\r\n')) data = data.slice(0, -2);
              fileData = Buffer.from(data, 'binary');
            }
          }
        }
      }

      // Sanitize name for filename
      const safeId = styleName.replace(/[^a-zA-Zа-яА-Я0-9_-]/g, '_').slice(0, 50);
      const imgPath = path.join(STYLES_DIR, safeId + '.jpg');
      fs.writeFileSync(imgPath, fileData);

      // Update metadata
      const styles = readStylesMeta();
      // Remove existing with same id
      const filtered = styles.filter(s => s.id !== safeId);
      filtered.push({
        id: safeId,
        name: styleName,
        prompt: promptHint,
        file: safeId + '.jpg',
        created_at: new Date().toISOString(),
      });
      writeStylesMeta(filtered);

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, id: safeId, name: styleName }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // DELETE /api/styles/:id — delete a style
  if (pathname.startsWith('/api/styles/') && req.method === 'DELETE') {
    const styleId = pathname.split('/')[3];
    const styles = readStylesMeta();
    const style = styles.find(s => s.id === styleId);
    if (style) {
      const imgPath = path.join(STYLES_DIR, style.file);
      if (fs.existsSync(imgPath)) fs.unlinkSync(imgPath);
      writeStylesMeta(styles.filter(s => s.id !== styleId));
    }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  // GET /api/styles/:id/image — serve style image
  if (pathname.match(/^\/api\/styles\/[^/]+\/image$/) && req.method === 'GET') {
    const styleId = pathname.split('/')[3];
    const styles = readStylesMeta();
    const style = styles.find(s => s.id === styleId);
    if (!style) { res.writeHead(404); res.end('Not found'); return; }
    const imgPath = path.join(STYLES_DIR, style.file);
    if (!fs.existsSync(imgPath)) { res.writeHead(404); res.end('Not found'); return; }
    res.writeHead(200, { 'Content-Type': 'image/jpeg' });
    fs.createReadStream(imgPath).pipe(res);
    return;
  }

  // GET /api/fonts/:file — serve font files
  if (pathname.startsWith('/api/fonts/') && req.method === 'GET') {
    const fontFile = pathname.replace('/api/fonts/', '');
    const fontPath = path.join(FONTS_DIR, fontFile);
    if (!fs.existsSync(fontPath)) { res.writeHead(404); res.end('Not found'); return; }
    const ext = path.extname(fontPath).toLowerCase();
    res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream', 'Cache-Control': 'public, max-age=86400' });
    fs.createReadStream(fontPath).pipe(res);
    return;
  }

  // GET /api/assets/expert.jpg[?channel_id=X] — serve expert photo
  // With channel_id: prefer data/channels/<id>/expert.jpg, fall back to global.
  // Without: always global.
  if (pathname === '/api/assets/expert.jpg' && req.method === 'GET') {
    const channelId = url.searchParams.get('channel_id') || '';
    let expertPath;
    if (channelId) {
      const perChannel = path.join(DATA_DIR, 'channels', channelId, 'expert.jpg');
      expertPath = fs.existsSync(perChannel) ? perChannel : path.join(PIPELINE_DIR, 'assets', 'expert.jpg');
    } else {
      expertPath = path.join(PIPELINE_DIR, 'assets', 'expert.jpg');
    }
    if (!fs.existsSync(expertPath)) { res.writeHead(404); res.end('Not found'); return; }
    res.writeHead(200, { 'Content-Type': 'image/jpeg', 'Cache-Control': 'no-cache' });
    fs.createReadStream(expertPath).pipe(res);
    return;
  }

  // GET /api/assets/:type/:file — serve asset files (references, textures, clothing, font-previews)
  if (pathname.startsWith('/api/assets/') && req.method === 'GET') {
    const parts = pathname.replace('/api/assets/', '').split('/');
    const assetType = parts[0];
    const fileName = parts.slice(1).join('/');
    const dirs = {
      'references': REFERENCES_DIR,
      'textures': TEXTURES_DIR,
      'clothing-male': CLOTHING_MALE_DIR,
      'clothing-female': CLOTHING_FEMALE_DIR,
      'font-previews': FONT_PREVIEWS_DIR,
    };
    const dir = dirs[assetType];
    if (!dir || !fileName) { res.writeHead(404); res.end('Not found'); return; }
    const filePath = path.join(dir, path.basename(fileName));
    if (!fs.existsSync(filePath)) { res.writeHead(404); res.end('Not found'); return; }
    const ext = path.extname(filePath).toLowerCase();
    res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream', 'Cache-Control': 'public, max-age=3600' });
    fs.createReadStream(filePath).pipe(res);
    return;
  }

  // GET /api/assets-list/:type — list asset files in a directory
  if (pathname.startsWith('/api/assets-list/') && req.method === 'GET') {
    const assetType = pathname.replace('/api/assets-list/', '');
    const dirs = {
      'references': REFERENCES_DIR,
      'textures': TEXTURES_DIR,
      'clothing-male': CLOTHING_MALE_DIR,
      'clothing-female': CLOTHING_FEMALE_DIR,
      'font-previews': FONT_PREVIEWS_DIR,
    };
    const dir = dirs[assetType];
    if (!dir) { res.writeHead(404); res.end(JSON.stringify({ ok: false, error: 'Unknown type' })); return; }
    try {
      const files = fs.existsSync(dir) ? fs.readdirSync(dir).filter(f => /\.(webp|jpg|jpeg|png)$/i.test(f)).sort() : [];
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, files, type: assetType }));
    } catch (e) {
      res.writeHead(500); res.end(JSON.stringify({ ok: false, error: e.message }));
    }
    return;
  }

  // GET /api/clothing-presets — list clothing presets
  if (pathname === '/api/clothing-presets' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, presets: readClothingMeta() }));
    return;
  }

  // POST /api/upload-clothing — upload clothing preset
  if (pathname === '/api/upload-clothing' && req.method === 'POST') {
    try {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const rawBuffer = Buffer.concat(chunks);
      ensureClothingDir();
      const contentType = req.headers['content-type'] || '';
      let presetName = 'clothing_' + Date.now();
      let promptHint = '';
      let fileData = rawBuffer;

      if (contentType.includes('multipart/form-data')) {
        const boundaryMatch = contentType.match(/boundary=(.+)/);
        if (!boundaryMatch) throw new Error('No boundary');
        const boundary = boundaryMatch[1];
        const parts = rawBuffer.toString('binary').split('--' + boundary);
        for (const part of parts) {
          if (part.includes('name="name"') && !part.includes('filename=')) {
            const hEnd = part.indexOf('\r\n\r\n');
            if (hEnd >= 0) { let v = part.slice(hEnd+4).trim(); if (v.endsWith('\r\n')) v=v.slice(0,-2); if (v) presetName = v.trim(); }
          }
          if (part.includes('name="prompt"') && !part.includes('filename=')) {
            const hEnd = part.indexOf('\r\n\r\n');
            if (hEnd >= 0) { let v = part.slice(hEnd+4).trim(); if (v.endsWith('\r\n')) v=v.slice(0,-2); promptHint = v.trim(); }
          }
          if (part.includes('filename=') && (part.includes('image/') || part.includes('application/octet'))) {
            const headerEnd = part.indexOf('\r\n\r\n');
            if (headerEnd >= 0) { let d = part.slice(headerEnd+4); if (d.endsWith('\r\n')) d=d.slice(0,-2); fileData = Buffer.from(d, 'binary'); }
          }
        }
      }

      const safeId = presetName.replace(/[^a-zA-Zа-яА-Я0-9_-]/g, '_').slice(0, 50);
      fs.writeFileSync(path.join(CLOTHING_DIR, safeId + '.jpg'), fileData);
      const presets = readClothingMeta().filter(p => p.id !== safeId);
      presets.push({ id: safeId, name: presetName, prompt: promptHint, file: safeId + '.jpg', created_at: new Date().toISOString() });
      writeClothingMeta(presets);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, id: safeId }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // GET /api/clothing-presets/:id/image — serve clothing image
  if (pathname.match(/^\/api\/clothing-presets\/[^/]+\/image$/) && req.method === 'GET') {
    const presetId = pathname.split('/')[3];
    const presets = readClothingMeta();
    const preset = presets.find(p => p.id === presetId);
    if (!preset) { res.writeHead(404); res.end('Not found'); return; }
    const imgPath = path.join(CLOTHING_DIR, preset.file);
    if (!fs.existsSync(imgPath)) { res.writeHead(404); res.end('Not found'); return; }
    res.writeHead(200, { 'Content-Type': 'image/jpeg' });
    fs.createReadStream(imgPath).pipe(res);
    return;
  }

  // DELETE /api/clothing-presets/:id
  if (pathname.match(/^\/api\/clothing-presets\/[^/]+$/) && req.method === 'DELETE') {
    const presetId = pathname.split('/')[3];
    const presets = readClothingMeta();
    const preset = presets.find(p => p.id === presetId);
    if (preset) {
      const imgPath = path.join(CLOTHING_DIR, preset.file);
      if (fs.existsSync(imgPath)) fs.unlinkSync(imgPath);
      writeClothingMeta(presets.filter(p => p.id !== presetId));
    }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  // POST /api/improve-prompt — translate and improve prompt via Claude
  if (pathname === '/api/improve-prompt' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const systemPrompt = `You are a YouTube thumbnail prompt specialist. Take the user's Russian description and return an improved, detailed English prompt for AI image generation (Nano Banana 2 model).

Rules:
- Output ONLY the English prompt, nothing else
- Focus on: person pose/emotion, neon lighting effects, background, text placement, composition
- Always include: "YouTube thumbnail style, vibrant colors, 16:9 landscape"
- If the user mentions text on the image, include it as 'Large bold text says "TEXT"'
- Make the prompt vivid and specific for maximum click-through-rate`;

      const result = await callClaude(systemPrompt, body.text);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, prompt: result }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/download-yt-thumb — download YouTube video thumbnail by URL
  if (pathname === '/api/download-yt-thumb' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const url = body.url || '';
      // Extract video ID from various YouTube URL formats
      let videoId = '';
      const patterns = [
        /[?&]v=([a-zA-Z0-9_-]{11})/,
        /youtu\.be\/([a-zA-Z0-9_-]{11})/,
        /\/shorts\/([a-zA-Z0-9_-]{11})/,
        /\/embed\/([a-zA-Z0-9_-]{11})/,
      ];
      for (const p of patterns) {
        const m = url.match(p);
        if (m) { videoId = m[1]; break; }
      }
      if (!videoId) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'Не удалось извлечь ID видео из ссылки' }));
        return;
      }

      // YouTube thumbnail URLs (try maxresdefault first, then hqdefault)
      const https = require('https');
      const thumbUrls = [
        `https://img.youtube.com/vi/${videoId}/maxresdefault.jpg`,
        `https://img.youtube.com/vi/${videoId}/sddefault.jpg`,
        `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`,
      ];

      let thumbData = null;
      let usedUrl = '';
      for (const thumbUrl of thumbUrls) {
        try {
          thumbData = await new Promise((resolve, reject) => {
            https.get(thumbUrl, resp => {
              if (resp.statusCode !== 200) { reject(new Error('Not found')); return; }
              const chunks = [];
              resp.on('data', c => chunks.push(c));
              resp.on('end', () => {
                const buf = Buffer.concat(chunks);
                // YouTube returns a small placeholder for non-existent maxres
                if (buf.length < 5000) { reject(new Error('Too small')); return; }
                resolve(buf);
              });
              resp.on('error', reject);
            }).on('error', reject);
          });
          usedUrl = thumbUrl;
          break;
        } catch (e) { continue; }
      }

      if (!thumbData) {
        res.writeHead(404, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'Не удалось скачать обложку' }));
        return;
      }

      // Save as a style reference
      ensureStylesDir();
      const safeId = 'yt_' + videoId;
      const imgPath = path.join(STYLES_DIR, safeId + '.jpg');
      fs.writeFileSync(imgPath, thumbData);

      // Add to styles metadata
      const styles = readStylesMeta().filter(s => s.id !== safeId);
      styles.push({
        id: safeId,
        name: 'YT: ' + videoId,
        prompt: '',
        file: safeId + '.jpg',
        created_at: new Date().toISOString(),
        source: 'youtube',
        video_id: videoId,
      });
      writeStylesMeta(styles);

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        ok: true,
        id: safeId,
        video_id: videoId,
        image_url: `/api/styles/${safeId}/image`,
        size: thumbData.length,
      }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/generate-cover-editor — generate cover from editor state
  if (pathname === '/api/generate-cover-editor' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      // Text is rendered as CSS overlay — do NOT bake text into AI image
      let prompt = body.prompt || '';

      const args = ['generate-cover-custom', body.project_id, JSON.stringify({
        prompt,
        text_on_image: '',
        style_id: body.style_id || '',
        text_style_id: body.text_style_id || '',
        neon_color: body.neon_color || '#00BFFF',
        clothing_id: body.person?.clothing_id || '',
        clothing_url: body.person?.clothing_url || '',
      })];
      const output = await runAgent(args);

      // Find the generated file
      const projDir = path.join(DATA_DIR, body.project_id, 'thumbnails');
      let imageUrl = '';
      if (fs.existsSync(projDir)) {
        const files = fs.readdirSync(projDir).filter(f => f.startsWith('custom_'))
          .sort((a, b) => {
            const na = parseInt(a.match(/custom_(\d+)/)?.[1] || '0');
            const nb = parseInt(b.match(/custom_(\d+)/)?.[1] || '0');
            return nb - na;
          });
        if (files[0]) imageUrl = `/api/file/${body.project_id}/thumbnails/${files[0]}`;
      }

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, output, image_url: imageUrl }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // GET /api/editor-state/:projectId — load editor state
  if (pathname.match(/^\/api\/editor-state\/[^/]+$/) && req.method === 'GET') {
    const pid = pathname.split('/')[3];
    const stateFile = path.join(DATA_DIR, pid, 'editor_state.json');
    if (fs.existsSync(stateFile)) {
      const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, state }));
    } else {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, state: null }));
    }
    return;
  }

  // POST /api/editor-state/:projectId — save editor state
  if (pathname.match(/^\/api\/editor-state\/[^/]+$/) && req.method === 'POST') {
    try {
      const pid = pathname.split('/')[3];
      const body = JSON.parse(await readBody(req));
      const projDir = path.join(DATA_DIR, pid);
      if (!fs.existsSync(projDir)) fs.mkdirSync(projDir, { recursive: true });
      fs.writeFileSync(path.join(projDir, 'editor_state.json'), JSON.stringify(body.state, null, 2), 'utf8');
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/generate-cover-custom — generate a single cover with custom prompt/style
  if (pathname === '/api/generate-cover-custom' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      // body: { project_id, prompt, text_on_image, style_id, neon_color }
      const args = ['generate-cover-custom', body.project_id, JSON.stringify({
        prompt: body.prompt || '',
        text_on_image: body.text_on_image || '',
        style_id: body.style_id || '',
        neon_color: body.neon_color || '#00BFFF',
      })];
      const output = await runAgent(args);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, output }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // GET /api/file/:projectId/... — serve project files
  if (pathname.startsWith('/api/file/')) {
    const parts = pathname.replace('/api/file/', '').split('/');
    const projectId = parts[0];
    const filePath = path.join(DATA_DIR, projectId, ...parts.slice(1));
    if (!fs.existsSync(filePath)) { res.writeHead(404); res.end('Not found'); return; }
    const ext = path.extname(filePath).toLowerCase();
    res.writeHead(200, {
      'Content-Type': MIME[ext] || 'application/octet-stream',
      'Cache-Control': 'no-cache, no-store, must-revalidate',
      'Pragma': 'no-cache',
      'Expires': '0',
    });
    fs.createReadStream(filePath).pipe(res);
    return;
  }

  // ── Static files ──
  let filePath = pathname === '/' ? '/index.html' : pathname;
  const absPath = path.join(__dirname, 'public', filePath);
  if (!fs.existsSync(absPath) || fs.statSync(absPath).isDirectory()) {
    // SPA fallback
    const indexPath = path.join(__dirname, 'public', 'index.html');
    if (fs.existsSync(indexPath)) {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      fs.createReadStream(indexPath).pipe(res);
    } else {
      res.writeHead(404);
      res.end('Not found');
    }
    return;
  }
  const ext = path.extname(absPath).toLowerCase();
  res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
  fs.createReadStream(absPath).pipe(res);

}).listen(PORT, () => {
  console.log(`[YT Pipeline Admin] http://localhost:${PORT}`);
});
