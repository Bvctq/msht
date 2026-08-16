// pages/api/index.js
// API hoàn tiền Shopee - All-in-one

export default async function handler(req, res) {
  // --- CORS ---
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-API-Secret');
  if (req.method === 'OPTIONS') return res.status(200).end();

  // --- Auth (tùy chọn) ---
  const secret = process.env.API_SECRET;
  if (secret && (req.headers['x-api-secret'] || req.query.secret) !== secret) {
    return res.status(401).json({ ok: false, error: 'Unauthorized' });
  }

  const { action } = req.query;
  const cookie = process.env.SHOPEE_AFFILIATE_COOKIE || '';

  try {
    if (req.method === 'POST' && action === 'link') {
      return await createLink(req, res, cookie);
    }
    if (req.method === 'GET' && action === 'commission') {
      return await getCommission(req, res, cookie);
    }
    if (req.method === 'GET' && action === 'orders') {
      return await getOrders(req, res, cookie);
    }
    return res.status(400).json({ ok: false, error: 'Invalid action. Use ?action=link (POST), ?action=commission (GET), ?action=orders (GET)' });
  } catch (e) {
    return res.status(500).json({ ok: false, error: e.message });
  }
}

/* ================= HELPERS ================= */

async function shopeeFetch(path, opts = {}, cookie) {
  return fetch(`https://affiliate.shopee.vn${path}`, {
    ...opts,
    headers: {
      'Cookie': cookie,
      'Content-Type': 'application/json',
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
      'Accept': 'application/json',
      ...(opts.headers || {}),
    },
  });
}

// Giải mã link rút gọn s.shopee.vn
async function resolveShortLink(url) {
  if (!url.includes('s.shopee.vn')) return url;
  let cur = url;
  for (let i = 0; i < 5; i++) {
    const r = await fetch(cur, { redirect: 'manual', headers: { 'User-Agent': 'Mozilla/5.0' } });
    if (r.status >= 300 && r.status < 400) {
      const loc = r.headers.get('location');
      if (!loc) break;
      cur = loc.startsWith('http') ? loc : new URL(loc, cur).href;
      if (!cur.includes('s.shopee.vn')) return cur;
    } else break;
  }
  return cur;
}

// Trích item_id, shop_id
function extractIds(url) {
  const m = url.match(/shopee\.vn\/(?:universal-link\/)?product\/(\d+)\/(\d+)/);
  if (m) return { shopId: m[1], itemId: m[2] };
  const item = url.match(/[?&]item_id=(\d+)/);
  const shop = url.match(/[?&]shopid=(\d+)/i);
  return { shopId: shop?.[1] || '', itemId: item?.[1] || '' };
}

// Parse "₫3.570" → 3570
function parseVnd(s) {
  if (!s) return 0;
  const n = parseInt(String(s).replace(/[₫\s.]/g, '').replace(/,/g, ''), 10);
  return isNaN(n) ? 0 : n;
}

function imgUrl(id) {
  return id ? `https://down-vn.img.susercontent.com/file/${id}` : '';
}

/* ================= ACTIONS ================= */

// 1. Tạo link affiliate
async function createLink(req, res, cookie) {
  const { url } = req.body || {};
  if (!url?.includes('shopee.vn')) {
    return res.status(400).json({ ok: false, error: 'Thiếu hoặc sai link Shopee' });
  }

  const body = {
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

  const r = await shopeeFetch('/api/v3/gql?q=batchCustomLink', { method: 'POST', body: JSON.stringify(body) }, cookie);
  const j = await r.json();
  const item = j?.data?.batchCustomLink?.[0];

  if (!item || item.failCode !== 0) {
    return res.status(502).json({ ok: false, error: 'Shopee trả về lỗi', raw: j });
  }

  return res.json({ ok: true, data: { shortLink: item.shortLink, longLink: item.longLink } });
}

// 2. Lấy hoa hồng → tính hoàn tiền 50% cho user
async function getCommission(req, res, cookie) {
  let { url } = req.query;
  if (!url) return res.status(400).json({ ok: false, error: 'Thiếu ?url=' });

  const resolved = await resolveShortLink(url);
  const { itemId, shopId } = extractIds(resolved);
  if (!itemId) return res.status(400).json({ ok: false, error: 'Không lấy được item_id từ link' });

  const r = await shopeeFetch(`/api/v3/offer/product?item_id=${itemId}`, {}, cookie);
  const j = await r.json();
  if (j.code !== 0 || !j.data) {
    return res.status(502).json({ ok: false, error: 'Lỗi dữ liệu Shopee', raw: j });
  }

  const item = j.data.batch_item_for_item_card_full || {};
  const rateInfo = j.data.commission_rate || {};
  const rateDetail = j.data.commission_rate_detail || {};

  // Giá Shopee nhân 100000
  const priceVnd = parseInt(item.price || item.price_min || '0', 10) / 100000;

  // Ưu tiên lấy seller_commission đã tính sẵn, nếu không thì tính từ rate
  let sellerComm = parseVnd(rateInfo.seller_commission);
  if (!sellerComm && rateDetail.seller_commission_rate) {
    const rate = parseInt(rateDetail.seller_commission_rate, 10) / 100000; // 21000 = 21%
    sellerComm = Math.round(priceVnd * rate);
  }

  const userCashback = Math.round(sellerComm / 2);
  const platformKeep = sellerComm - userCashback;

  return res.json({
    ok: true,
    data: {
      item_id: itemId,
      shop_id: shopId || item.shopid || '',
      name: item.name || '',
      image: imgUrl(item.image),
      price: Math.round(priceVnd),
      discount: item.discount || '',
      seller_commission: sellerComm,   // Tổng hoa hồng người bán
      user_cashback: userCashback,     // User được hoàn (50%)
      platform_keep: platformKeep,     // Hệ thống giữ (50%)
    }
  });
}

// 3. Lấy đơn hàng theo sub_id
async function getOrders(req, res, cookie) {
  const { sub_id, page_num = 1, page_size = 20, purchase_time_s, purchase_time_e, version = 1 } = req.query;
  if (!sub_id) return res.status(400).json({ ok: false, error: 'Thiếu ?sub_id=' });

  const qs = new URLSearchParams();
  qs.set('page_size', String(page_size));
  qs.set('page_num', String(page_num));
  qs.set('sub_id', String(sub_id));
  qs.set('version', String(version));
  if (purchase_time_s) qs.set('purchase_time_s', purchase_time_s);
  if (purchase_time_e) qs.set('purchase_time_e', purchase_time_e);

  const r = await shopeeFetch(`/api/v3/offer/orders?${qs.toString()}`, {}, cookie);
  const j = await r.json();

  return res.json({ ok: j.code === 0, data: j.data || null, msg: j.msg, raw: j });
}
