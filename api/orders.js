const { setCors, checkAuth, jsonResponse, fetchShopee } = require('./_utils');

module.exports = async (req, res) => {
  setCors(res, req.headers.origin);
  if (req.method === 'OPTIONS') { res.statusCode = 204; res.end(); return; }
  if (!checkAuth(req)) return jsonResponse(res, 401, { error: 'Unauthorized' });
  if (req.method !== 'GET') return jsonResponse(res, 405, { error: 'Method not allowed' });

  try {
    const urlObj = new URL(req.url, `http://${req.headers.host}`);
    const subId = urlObj.searchParams.get('sub_id');
    const start = urlObj.searchParams.get('start') || '';
    const end = urlObj.searchParams.get('end') || '';
    const pageNum = urlObj.searchParams.get('page_num') || '1';
    const pageSize = urlObj.searchParams.get('page_size') || '20';

    if (!subId) return jsonResponse(res, 400, { error: 'Missing sub_id' });

    const params = new URLSearchParams({
      page_size: pageSize, page_num: pageNum, sub_id: subId,
      purchase_time_s: start, purchase_time_e: end, version: '1'
    });

    const data = await fetchShopee(`https://affiliate.shopee.vn/api/v3/offer/orders?${params.toString()}`);
    if (data.code !== 0) return jsonResponse(res, 500, { error: 'Shopee API error', detail: data });

    const list = data.data?.list || [];
    const orders = list.map(order => {
      const comm = parseInt((order.commission || '0').replace(/[^\d]/g, '')) || 0;
      return {
        order_sn: order.order_sn,
        item_id: order.item_id,
        product_name: order.product_name || '',
        amount: order.amount,
        commission: order.commission,
        cashback: '₫' + Math.floor(comm / 2).toLocaleString('vi-VN'),
        status: order.status,
        purchase_time: order.purchase_time,
        shop_name: order.shop_name || ''
      };
    });

    jsonResponse(res, 200, {
      success: true, sub_id: subId,
      page_num: data.data?.page_num || 1,
      page_size: data.data?.page_size || 20,
      total_count: data.data?.total_count || 0,
      orders
    });
  } catch (err) {
    jsonResponse(res, 500, { error: err.message });
  }
};
