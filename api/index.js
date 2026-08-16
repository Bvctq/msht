// Shopee Affiliate Cashback API - Single File
// Routes: /api/convert, /api/commission, /api/orders

const SHOPEE_COOKIE = process.env.SHOPEE_COOKIE || '';
const INTERNAL_API_KEY = process.env.INTERNAL_API_KEY || '';
const CORS_ORIGIN = process.env.CORS_ORIGIN || '*';

// ===== UTILS =====
function json(res, status, data) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.end(JSON.stringify(data));
}

function cors(res, origin) {
  res.setHeader('Access-Control-Allow-Origin', CORS_ORIGIN === '*' ? '*' : (origin || '*'));
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, x-api-key');
}

function auth(req) {
  return (req.headers['x-api-key'] || '') === INTERNAL_API_KEY;
}

async function shopeeFetch(url, opts = {}) {
  const res = await fetch(url, {
    ...opts,
    headers: {
      'Cookie': SHOPEE_COOKIE,
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
      'Accept': 'application/json',
      'Accept-Language': 'vi-VN,vi;q=0.9',
      'Referer': 'https://affiliate.shopee.vn/',
      'Origin': 'https://affiliate.shopee.vn',
      'Content-Type': 'application/json',
      ...(opts.headers || {})
    }
  });
  if (!res.ok) {
    const txt = await res.text();
    throw new Error('Shopee ' + res.status + ': ' + txt.slice(0, 200));
  }
  return res.json();
}

async function resolveShort(url) {
  try {
    const r = await fetch(url, { method: 'HEAD', redirect: 'manual',
      headers: { 'User-Agent': 'Mozilla/5.0' } });
    return r.headers.get('location') || '';
  } catch(e) { return ''; }
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

function fmtVND(n) {
  return '\u20AB' + n.toLocaleString('vi-VN');
}

// ===== HANDLERS =====

async function handleConvert(req, res) {
  let body = '';
  for await (const chunk of req) body += chunk;
  const { url, sub_id } = JSON.parse(body || '{}');

  if (!url) return json(res, 400, { error: 'Missing url' });

  const payload = {
    operationName: "batchGetCustomLink",
    query: `query batchGetCustomLink($linkParams: [CustomLinkParam!], $sourceCaller: SourceCaller){
      batchCustomLink(linkParams: $linkParams, sourceCaller: $sourceCaller){
        shortLink
        longLink
        failCode
      }
    }`,
    variables: {
      linkParams: [{ originalLink: url, advancedLinkParams: {} }],
      sourceCaller: "CUSTOM_LINK_CALLER"
    }
  };

  const result = await shopeeFetch('https://affiliate.shopee.vn/api/v3/gql?q=batchCustomLink', {
    method: 'POST',
    body: JSON.stringify(payload)
  });

  const link = result?.data?.batchCustomLink?.[0];
  if (!link || link.failCode !== 0) {
    return json(res, 500, { error: 'Shopee failed', detail: link });
  }

  let long = link.longLink;
  if (sub_id) {
    long += (long.includes('?') ? '&' : '?') + 'sub_id=' + encodeURIComponent(sub_id);
  }

  json(res, 200, {
    success: true,
    original_url: url,
    sub_id: sub_id || null,
    affiliate_url: long,
    short_link: link.shortLink
  });
}

async function handleCommission(req, res, urlObj) {
  let itemId = urlObj.searchParams.get('item_id');
  const purl = urlObj.searchParams.get('url');

  if (!itemId && purl) {
    const resolved = await resolveShort(purl);
    itemId = extractItemId(resolved);
  }
  if (!itemId) return json(res, 400, { error: 'Missing item_id or url' });

  const data = await shopeeFetch('https://affiliate.shopee.vn/api/v3/offer/product?item_id=' + itemId);
  if (data.code !== 0 || !data.data) {
    return json(res, 500, { error: 'Shopee API error', detail: data });
  }

  const d = data.data;
  const commStr = d.commission || '0';
  const rateStr = d.commission_rate?.seller_commission_rate || d.commission_rate?.default_commission_rate || '0%';
  const commNum = parseInt(commStr.replace(/[^\d]/g, '')) || 0;
  const cashback = Math.floor(commNum / 2);

  json(res, 200, {
    success: true,
    item_id: itemId,
    product_name: d.batch_item_for_item_card_full?.name || '',
    seller_commission_rate: rateStr,
    estimated_commission: commStr,
    estimated_cashback: fmtVND(cashback),
    cashback_percent: 50,
    price: d.batch_item_for_item_card_full?.price
      ? (parseInt(d.batch_item_for_item_card_full.price) / 100000).toLocaleString('vi-VN') + '\u0111'
      : '',
    image: d.batch_item_for_item_card_full?.image || ''
  });
}

async function handleOrders(req, res, urlObj) {
  const subId = urlObj.searchParams.get('sub_id');
  if (!subId) return json(res, 400, { error: 'Missing sub_id' });

  const start = urlObj.searchParams.get('start') || '';
  const end = urlObj.searchParams.get('end') || '';
  const pageNum = urlObj.searchParams.get('page_num') || '1';
  const pageSize = urlObj.searchParams.get('page_size') || '20';

  const qs = new URLSearchParams({ page_size: pageSize, page_num: pageNum, sub_id: subId,
    purchase_time_s: start, purchase_time_e: end, version: '1' });

  const data = await shopeeFetch('https://affiliate.shopee.vn/api/v3/offer/orders?' + qs.toString());
  if (data.code !== 0) return json(res, 500, { error: 'Shopee API error', detail: data });

  const list = data.data?.list || [];
  const orders = list.map(o => {
    const comm = parseInt((o.commission || '0').replace(/[^\d]/g, '')) || 0;
    return {
      order_sn: o.order_sn,
      item_id: o.item_id,
      product_name: o.product_name || '',
      amount: o.amount,
      commission: o.commission,
      cashback: fmtVND(Math.floor(comm / 2)),
      status: o.status,
      purchase_time: o.purchase_time,
      shop_name: o.shop_name || ''
    };
  });

  json(res, 200, {
    success: true, sub_id: subId,
    page_num: data.data?.page_num || 1,
    page_size: data.data?.page_size || 20,
    total_count: data.data?.total_count || 0,
    orders
  });
}

// ===== MAIN ENTRY =====
module.exports = async (req, res) => {
  try {
    cors(res, req.headers.origin);
    if (req.method === 'OPTIONS') { res.statusCode = 204; return res.end(); }
    if (!auth(req)) return json(res, 401, { error: 'Unauthorized' });

    const urlObj = new URL(req.url, 'http://localhost');
    const path = urlObj.pathname.replace(/^\/api/, '').replace(/^\//, '');

    if (path === 'convert' && req.method === 'POST') {
      return await handleConvert(req, res);
    }
    if (path === 'commission' && req.method === 'GET') {
      return await handleCommission(req, res, urlObj);
    }
    if (path === 'orders' && req.method === 'GET') {
      return await handleOrders(req, res, urlObj);
    }

    json(res, 404, { error: 'Not found', path });
  } catch (err) {
    console.error('API ERROR:', err.message);
    json(res, 500, { error: err.message });
  }
};
