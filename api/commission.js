const { setCors, checkAuth, jsonResponse, fetchShopee, resolveShortLink, extractItemId } = require('./_utils');

module.exports = async (req, res) => {
  setCors(res, req.headers.origin);
  if (req.method === 'OPTIONS') { res.statusCode = 204; res.end(); return; }
  if (!checkAuth(req)) return jsonResponse(res, 401, { error: 'Unauthorized' });
  if (req.method !== 'GET') return jsonResponse(res, 405, { error: 'Method not allowed' });

  try {
    const urlObj = new URL(req.url, `http://${req.headers.host}`);
    let itemId = urlObj.searchParams.get('item_id');
    const productUrl = urlObj.searchParams.get('url');

    if (!itemId && productUrl) {
      const resolved = await resolveShortLink(productUrl);
      itemId = extractItemId(resolved);
    }
    if (!itemId) return jsonResponse(res, 400, { error: 'Missing item_id or url' });

    const data = await fetchShopee(`https://affiliate.shopee.vn/api/v3/offer/product?item_id=${itemId}`);
    if (data.code !== 0 || !data.data) {
      return jsonResponse(res, 500, { error: 'Shopee API error', detail: data });
    }

    const d = data.data;
    const commissionStr = d.commission || '0';
    const rateStr = d.commission_rate?.seller_commission_rate || d.commission_rate?.default_commission_rate || '0%';
    const commissionNum = parseInt(commissionStr.replace(/[^\d]/g, '')) || 0;
    const cashback = Math.floor(commissionNum / 2);
    const formatVND = (n) => '₫' + n.toLocaleString('vi-VN');

    jsonResponse(res, 200, {
      success: true,
      item_id: itemId,
      product_name: d.batch_item_for_item_card_full?.name || '',
      seller_commission_rate: rateStr,
      estimated_commission: commissionStr,
      estimated_cashback: formatVND(cashback),
      cashback_percent: 50,
      price: d.batch_item_for_item_card_full?.price
        ? (parseInt(d.batch_item_for_item_card_full.price) / 100000).toLocaleString('vi-VN') + 'đ'
        : '',
      image: d.batch_item_for_item_card_full?.image || ''
    });
  } catch (err) {
    jsonResponse(res, 500, { error: err.message });
  }
};
