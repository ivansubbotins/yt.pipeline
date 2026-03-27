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

// ── Auth: session tokens ──
const crypto = require('crypto');
const activeSessions = new Map(); // token -> { user, created }
const SESSION_MAX_AGE = 7 * 24 * 60 * 60 * 1000; // 7 days

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
  const cookies = parseCookies(req.headers.cookie);
  const token = cookies['yt_session'];
  if (!token) return false;
  const session = activeSessions.get(token);
  if (!session) return false;
  if (Date.now() - session.created > SESSION_MAX_AGE) {
    activeSessions.delete(token);
    return false;
  }
  return true;
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

// ── Anthropic API call ──
function callClaude(systemPrompt, userMessage) {
  return new Promise((resolve, reject) => {
    if (!ANTHROPIC_API_KEY) return reject(new Error('ANTHROPIC_API_KEY not set'));
    const https = require('https');
    const body = JSON.stringify({
      model: 'claude-sonnet-4-20250514',
      max_tokens: 4096,
      system: systemPrompt,
      messages: [{ role: 'user', content: userMessage }],
    });
    const req = https.request({
      hostname: 'api.anthropic.com',
      path: '/v1/messages',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
        'Content-Length': Buffer.byteLength(body),
      },
    }, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => {
        try {
          const json = JSON.parse(data);
          if (json.content && json.content[0]) resolve(json.content[0].text);
          else reject(new Error(json.error?.message || 'No content'));
        } catch (e) { reject(e); }
      });
    });
    req.on('error', reject);
    req.write(body);
    req.end();
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
    const pythonBin = path.join(PIPELINE_DIR, '.venv', 'bin', 'python3');
    const python = fs.existsSync(pythonBin) ? pythonBin : 'python3';
    const proc = spawn(python, [AGENT_PY, ...args], {
      cwd: PIPELINE_DIR,
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
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
  return JSON.parse(fs.readFileSync(stateFile, 'utf8'));
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
  const url = new URL(req.url, `http://${req.headers.host}`);
  const pathname = decodeURIComponent(url.pathname);

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

  // GET /oauth/callback — YouTube OAuth2 callback (no auth required)
  if (pathname === '/oauth/callback' && req.method === 'GET') {
    const code = url.searchParams.get('code');
    const error = url.searchParams.get('error');
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
          // Save token
          const tokenPath = path.join(PIPELINE_DIR, 'youtube_token.json');
          fs.writeFileSync(tokenPath, JSON.stringify(tokenData, null, 2));
          console.log('YouTube OAuth token saved successfully');
          res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
          res.end(`<html><body style="background:#0c0c0c;color:#22c55e;font-family:sans-serif;padding:40px;text-align:center;">
            <h2>YouTube подключён!</h2>
            <p style="color:#bbb;margin-top:12px;">Токен сохранён. Теперь можно публиковать видео.</p>
            <a href="/express.html" style="color:#3b82f6;font-size:16px;">Перейти к Express Publish →</a>
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

  // OPTIONS preflight — no auth
  if (req.method === 'OPTIONS') {
    res.writeHead(200, {
      'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    });
    res.end();
    return;
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
      } catch(e) {
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

  // POST /api/express/covers — Generate covers for quick publish
  if (pathname === '/api/express/covers' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const topic = body.topic || '';
      const title = body.title || topic;
      const script = body.script || '';
      const description = body.description || '';
      const styleId = body.style_id || '';
      const count = Math.min(body.count || 3, 5);

      // Generate smart cover prompts using Claude if we have context
      console.log('[Express Covers] Starting generation, topic:', topic, 'title:', title, 'count:', count);
      let coverPrompts = [];
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
      console.log('[Express Covers] Got', coverPrompts.length, 'smart prompts, generating images...');

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

  // POST /api/splittest/start — Start a split-test
  if (pathname === '/api/splittest/start' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const { project_id, video_id, variants, rotation_hours, duration_hours } = body;

      if (!project_id || !video_id || !variants || variants.length < 2) {
        throw new Error('Need project_id, video_id, and at least 2 variants');
      }

      // Resolve thumbnail paths from URLs to absolute paths
      const resolvedVariants = variants.map(v => {
        const resolved = { title: v.title || '' };
        if (v.thumbnail) {
          // thumbnail is like /api/file/express-xxx/thumbnails/custom_1.jpg
          const thumbParts = v.thumbnail.replace('/api/file/', '').split('/');
          const absPath = path.join(DATA_DIR, ...thumbParts);
          if (fs.existsSync(absPath)) resolved.thumbnail = absPath;
        }
        return resolved;
      });

      // Write variants config, then run Python splittest
      const configPath = path.join(DATA_DIR, project_id, 'splittest_config.json');
      fs.writeFileSync(configPath, JSON.stringify({
        video_id, variants: resolvedVariants, rotation_hours: rotation_hours || 6, duration_hours: duration_hours || 72
      }));

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
      const output = await runAgent(['new', body.topic]);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, output }));
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: err.message }));
    }
    return;
  }

  // POST /api/run-step — run a specific step
  if (pathname === '/api/run-step' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req));
      const output = await runAgent(['step', body.project_id, body.step]);
      const state = readState(body.project_id);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, output, state }));
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

  // POST /api/upload-expert — upload expert photo (multipart form or raw)
  if (pathname === '/api/upload-expert' && req.method === 'POST') {
    try {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const rawBuffer = Buffer.concat(chunks);
      const assetsDir = path.join(PIPELINE_DIR, 'assets');
      if (!fs.existsSync(assetsDir)) fs.mkdirSync(assetsDir, { recursive: true });
      const expertPath = path.join(assetsDir, 'expert.jpg');

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

  // GET /api/assets/expert.jpg — serve expert photo directly
  if (pathname === '/api/assets/expert.jpg' && req.method === 'GET') {
    const expertPath = path.join(PIPELINE_DIR, 'assets', 'expert.jpg');
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
