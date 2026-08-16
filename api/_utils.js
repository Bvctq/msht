function setCors(res, origin) {
  const allowed = process.env.CORS_ORIGIN || '*';
  res.setHeader('Access-Control-Allow-Origin', allowed === '*' ? '*' : origin);
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, x-api-key');
}

function checkAuth(req) {
  const key = req.headers['x-api-key'] || '';
  return key === (process.env.INTERNAL_API_KEY || '');
}

function jsonResponse(res, status, data) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.end(JSON.stringify(data));
}

async function fetchShopee(url, options = {}) {
  const cookie = process.env.SHOPEE_COOKIE || '';
  const defaultHeaders = {
    'Cookie': cookie,
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json',
    'Accept-Language': 'vi-VN,vi;q=0.9',
    'Referer': 'https://affiliate.shopee.vn/',
    'Origin': 'https://affiliate.shopee.vn'
  };

  const res = await fetch(url, {
    ...options,
    headers: { ...defaultHeaders, ...(options.headers || {}) }
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Shopee API ${res.status}: ${text.substring(0, 200)}`);
  }
  return res.json();
}

async function resolveShortLink(shortUrl) {
  try {
    const res = await fetch(shortUrl, {
      method: 'HEAD',
      redirect: 'manual',
      headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' }
    });
    return res.headers.get('location') || '';
  } catch (e) { return ''; }
}

function extractItemId(url) {
  let m = url.match(/product\/(\d+)\/(\d+)/);
  if (m) return m[2];
  m = url.match(/-i\.(\d+)\.(\d+)/);
  if (m) return m[2];
  m = url.match(/[?&]item_id=(\d+)/);
  if (m) return m[1];
  return null;
}

module.exports = { setCors, checkAuth, jsonResponse, fetchShopee, resolveShortLink, extractItemId };
