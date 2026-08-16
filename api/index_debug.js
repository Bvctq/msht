const https = require('https');
const { URL } = require('url');

const SHOPEE_COOKIE = process.env.SHOPEE_COOKIE || '';
const INTERNAL_API_KEY = process.env.INTERNAL_API_KEY || '';
const CORS_ORIGIN = process.env.CORS_ORIGIN || '*';

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

function shopeeRequest(urlStr, method, postData, customCookie) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(urlStr);
    const cookie = customCookie || SHOPEE_COOKIE;

    const options = {
      hostname: urlObj.hostname,
      path: urlObj.pathname + urlObj.search,
      method: method,
      headers: {
        'Cookie': cookie,
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148',
        'Accept': 'application/json',
        'Accept-Language': 'vi-VN,vi;q=0.9',
        'Referer': 'https://affiliate.shopee.vn/',
        'Origin': 'https://affiliate.shopee.vn',
        'Content-Type': 'application/json'
      }
    };

    console.log('[DEBUG] URL:', urlStr);
    console.log('[DEBUG] Cookie len:', cookie.length);
    console.log('[DEBUG] Cookie start:', cookie.substring(0, 80));

    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        console.log('[DEBUG] Status:', res.statusCode);
        console.log('[DEBUG] Body:', data.substring(0, 150));
        if (res.statusCode !== 200) {
          reject(new Error('Shopee ' + res.statusCode + ': ' + data.substring(0, 300)));
        } else {
          try { resolve(JSON.parse(data)); } catch(e) { resolve(data); }
        }
      });
    });

    req.on('error', (err) => reject(err));
    if (postData) req.write(JSON.stringify(postData));
    req.end();
  });
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

// ===== DEBUG: khong can auth =====
function handleDebug(req, res) {
  json(res, 200, {
    env_cookie_length: SHOPEE_COOKIE.length,
    env_cookie_preview: SHOPEE_COOKIE.substring(0, 300) + (SHOPEE_COOKIE.length > 300 ? '...' : ''),
    env_cookie_has_spc_ec: SHOPEE_COOKIE.includes('SPC_EC'),
    env_cookie_has_csrftoken: SHOPEE_COOKIE.includes('csrftoken'),
    api_key_set: INTERNAL_API_KEY.length > 0,
    node_version: process.version
  });
}

// ===== TEST COOKIE: nhan cookie tu body =====
async function handleTestCookie(req, res) {
  let body = '';
  for await (const chunk of req) body += chunk;
  const { cookie, url } = JSON.parse(body || '{}');

  if (!cookie) return json(res, 400, { error: 'Missing cookie in body' });

  const testUrl = url || 'https://shopee.vn/product/913225759/23881637574';
  const payload = {
    operationName: "batchGetCustomLink",
    query: "query batchGetCustomLink($linkParams: [CustomLinkParam!], $sourceCaller: SourceCaller){ batchCustomLink(linkParams: $linkParams, sourceCaller: $sourceCaller){ shortLink longLink failCode } }",
    variables: {
      linkParams: [{ originalLink: testUrl }],
      sourceCaller: "CUSTOM_LINK_CALLER"
    }
  };

  try {
    const result = await shopeeRequest('https://affiliate.shopee.vn/api/v3/gql?q=batchCustomLink', 'POST', payload, cookie);
    const link = result?.data?.batchCustomLink?.[0];
    json(res, 200, {
      success: link && link.failCode === 0,
      used_cookie_length: cookie.length,
      result: link || result
    });
  } catch (err) {
    json(res, 500, { error: err.message });
  }
}

async function handleConvert(req, res) {
  let body = '';
  for await (const chunk of req) body += chunk;
  const { url, sub_id } = JSON.parse(body || '{}');
  if (!url) return json(res, 400, { error: 'Missing url' });

  const payload = {
    operationName: "batchGetCustomLink",
    query: "query batchGetCustomLink($linkParams: [CustomLinkParam!], $sourceCaller: SourceCaller){ batchCustomLink(linkParams: $linkParams, sourceCaller: $sourceCaller){ shortLink longLink failCode } }",
    variables: {
      linkParams: [{ originalLink: url }],
      sourceCaller: "CUSTOM_LINK_CALLER"
    }
  };

  try {
    const result = await shopeeRequest('https://affiliate.shopee.vn/api/v3/gql?q=batchCustomLink', 'POST', payload);
    const link = result?.data?.batchCustomLink?.[0];
    if (!link || link.failCode !== 0) {
      return json(res, 500, { error: 'Shopee failed', detail: link });
    }
    let long = link.longLink;
    if (sub_id) long += (long.includes('?') ? '&' : '?') + 'sub_id=' + encodeURIComponent(sub_id);
    json(res, 200, { success: true, original_url: url, sub_id: sub_id || null, affiliate_url: long, short_link: link.shortLink });
  } catch (err) {
    json(res, 500, { error: err.message });
  }
}

async function handleCommission(req, res, urlObj) {
  let itemId = urlObj.searchParams.get('item_id');
  const purl = urlObj.searchParams.get('url');
  if (!itemId && purl) itemId = extractItemId(purl);
  if (!itemId) return json(res, 400, { error: 'Missing item_id or url' });

  try {
    const data = await shopeeRequest('https://affiliate.shopee.vn/api/v3/offer/product?item_id=' + itemId, 'GET');
    if (data.code !== 0 || !data.data) return json(res, 500, { error: 'Shopee API error', detail: data });
    const d = data.data;
    const commStr = d.commission || '0';
    const rateStr = d.commission_rate?.seller_commission_rate || d.commission_rate?.default_commission_rate || '0%';
    const commNum = parseInt(commStr.replace(/[^\d]/g, '')) || 0;
    json(res, 200, { success: true, item_id: itemId, product_name: d.batch_item_for_item_card_full?.name || '', seller_commission_rate: rateStr, estimated_commission: commStr, estimated_cashback: fmtVND(Math.floor(commNum / 2)), cashback_percent: 50 });
  } catch (err) {
    json(res, 500, { error: err.message });
  }
}

async function handleOrders(req, res, urlObj) {
  const subId = urlObj.searchParams.get('sub_id');
  if (!subId) return json(res, 400, { error: 'Missing sub_id' });
  const start = urlObj.searchParams.get('start') || '';
  const end = urlObj.searchParams.get('end') || '';
  const pageNum = urlObj.searchParams.get('page_num') || '1';
  const pageSize = urlObj.searchParams.get('page_size') || '20';
  const qs = new URLSearchParams({ page_size: pageSize, page_num: pageNum, sub_id: subId, purchase_time_s: start, purchase_time_e: end, version: '1' });

  try {
    const data = await shopeeRequest('https://affiliate.shopee.vn/api/v3/offer/orders?' + qs.toString(), 'GET');
    if (data.code !== 0) return json(res, 500, { error: 'Shopee API error', detail: data });
    const list = data.data?.list || [];
    const orders = list.map(o => {
      const comm = parseInt((o.commission || '0').replace(/[^\d]/g, '')) || 0;
      return { order_sn: o.order_sn, item_id: o.item_id, product_name: o.product_name || '', amount: o.amount, commission: o.commission, cashback: fmtVND(Math.floor(comm / 2)), status: o.status, purchase_time: o.purchase_time, shop_name: o.shop_name || '' };
    });
    json(res, 200, { success: true, sub_id: subId, page_num: data.data?.page_num || 1, page_size: data.data?.page_size || 20, total_count: data.data?.total_count || 0, orders });
  } catch (err) {
    json(res, 500, { error: err.message });
  }
}

module.exports = async (req, res) => {
  try {
    cors(res, req.headers.origin);
    if (req.method === 'OPTIONS') { res.statusCode = 204; return res.end(); }

    const urlObj = new URL(req.url, 'http://localhost');
    const path = urlObj.pathname.replace(/^\/api/, '').replace(/^\//, '');

    if (path === 'debug') return handleDebug(req, res);
    if (path === 'test-cookie' && req.method === 'POST') return handleTestCookie(req, res);

    if (!auth(req)) return json(res, 401, { error: 'Unauthorized' });

    if (path === 'convert' && req.method === 'POST') return await handleConvert(req, res);
    if (path === 'commission' && req.method === 'GET') return await handleCommission(req, res, urlObj);
    if (path === 'orders' && req.method === 'GET') return await handleOrders(req, res, urlObj);

    json(res, 404, { error: 'Not found', path });
  } catch (err) {
    console.error('API ERROR:', err.message);
    json(res, 500, { error: err.message });
  }
};
