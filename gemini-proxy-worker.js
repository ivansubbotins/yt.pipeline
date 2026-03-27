/**
 * Cloudflare Worker — Gemini API Proxy
 *
 * Deploy: wrangler deploy gemini-proxy-worker.js --name gemini-proxy
 * URL: https://gemini-proxy.iv-subbotin1.workers.dev
 *
 * Usage: POST https://gemini-proxy.iv-subbotin1.workers.dev/
 * Body: same as Google Gemini API generateContent
 * Header: x-api-key: <GEMINI_API_KEY>
 */

export default {
  async fetch(request) {
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'POST, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type, x-api-key',
        },
      });
    }

    if (request.method !== 'POST') {
      return new Response(JSON.stringify({ error: 'POST only' }), { status: 405 });
    }

    try {
      const apiKey = request.headers.get('x-api-key');
      if (!apiKey) {
        return new Response(JSON.stringify({ error: 'Missing x-api-key header' }), { status: 401 });
      }

      const body = await request.json();
      const model = body.model || 'gemini-2.5-flash-preview-04-17';
      delete body.model;

      const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${apiKey}`;

      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      const data = await resp.text();
      return new Response(data, {
        status: resp.status,
        headers: {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': '*',
        },
      });
    } catch (e) {
      return new Response(JSON.stringify({ error: e.message }), { status: 500 });
    }
  },
};
