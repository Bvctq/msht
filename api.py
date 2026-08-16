from flask import Flask, request, jsonify
import os, json, re, urllib.parse, requests

app = Flask(__name__)
COOKIE = os.environ.get("SHOPEE_COOKIE", "")

# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def clean_cookie(raw):
    return (raw or "").replace('"', "").replace("'", "").strip()

def extract_csrf(cookie_str):
    """Lấy csrftoken từ cookie → dùng cho header x-csrftoken."""
    for part in (cookie_str or "").split(';'):
        p = part.strip()
        if p.lower().startswith('csrftoken='):
            return p.split('=', 1)[1].strip()
    return ''

def format_vnd(n):
    """180000 → ₫180.000"""
    try:
        return f"₫{int(n):,}".replace(',', '.')
    except:
        return '₫0'

def resolve_url(url):
    """Mở redirect của link rút gọn (s.shopee.vn, shp.ee)."""
    try:
        r = requests.get(
            url if url.startswith('http') else 'https://' + url,
            headers={'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15'},
            timeout=10, allow_redirects=True)
        return r.url
    except:
        return url

def extract_ids(url):
    """
    Trích (shopid, itemid) từ Shopee URL.
    Trả về (shopid_or_None, itemid_or_None).
    """
    # shopee.vn/product/shopid/itemid  hoặc  /shopid/itemid?...
    m = re.search(r'\/(\d{7,})\/([\d]{7,})(?:[?&\/]|$)', url)
    if m:
        return m.group(1), m.group(2)
    # item_id=xxx (không có shopid)
    m = re.search(r'[?&]item_id=(\d+)', url)
    if m:
        return None, m.group(1)
    # Tên-sản-phẩm-i.shopid.itemid
    m = re.search(r'-i\.(\d+)\.(\d+)', url)
    if m:
        return m.group(1), m.group(2)
    return None, None

def get_product_public(shopid, itemid):
    """
    Shopee PUBLIC API — không cần cookie, không bị block cloud IP.
    Cần CẢ shopid VÀ itemid.
    """
    try:
        r = requests.get(
            f"https://shopee.vn/api/v4/item/get?itemid={itemid}&shopid={shopid}",
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Referer': f'https://shopee.vn/product/{shopid}/{itemid}',
                'x-shopee-language': 'vi',
            },
            timeout=12
        )
        d = r.json()
        item = (d.get('data') or {}).get('item')
        if not item:
            return None

        # Giá Shopee lưu * 100000  (18000000000 = 180.000₫)
        p_min    = int(item.get('price_min',    item.get('price', 0)) or 0)
        p_before = int(item.get('price_min_before_discount',
                                item.get('price_before_discount', 0)) or 0)
        p_vnd        = p_min    // 100000
        p_before_vnd = p_before // 100000

        return {
            'name':         item.get('name', ''),
            'image':        item.get('image', ''),
            'price_vnd':    p_vnd,
            'price':        format_vnd(p_vnd),
            'price_before': format_vnd(p_before_vnd) if p_before_vnd > p_vnd else '',
            'discount':     f"{item.get('raw_discount', 0)}%" if item.get('raw_discount') else '',
        }
    except Exception as e:
        print(f"[PublicAPI] {e}")
        return None


# ══════════════════════════════════════════════════════════════
# /api/convert — Tạo link affiliate có sub_id tracking
# ══════════════════════════════════════════════════════════════

@app.route("/api/convert", methods=["POST"])
def convert():
    data = request.get_json() or {}
    url  = data.get('url', '').strip()
    sub  = data.get('sub_id', '').strip()

    if not url:
        return jsonify({'error': 'Missing url'}), 400

    cookie = clean_cookie(COOKIE)
    if not cookie:
        return jsonify({'error': 'No cookie'}), 500

    api_url = url if url.startswith('http') else 'https://' + url
    lp = [{"originalLink": api_url}]
    if sub:
        lp[0]["advancedLinkParams"] = {"subId1": sub}  # gắn username vào sub_id tracking

    payload = {
        "operationName": "batchGetCustomLink",
        "query": (
            "query batchGetCustomLink($linkParams: [CustomLinkParam!], $sourceCaller: SourceCaller)"
            "{batchCustomLink(linkParams: $linkParams, sourceCaller: $sourceCaller)"
            "{shortLink longLink failCode}}"
        ),
        "variables": {"linkParams": lp, "sourceCaller": "CUSTOM_LINK_CALLER"}
    }
    h = {
        "content-type": "application/json",
        "cookie": cookie,
        "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    }

    try:
        r = requests.post(
            "https://affiliate.shopee.vn/api/v3/gql?q=batchCustomLink",
            headers=h, json=payload, timeout=20
        )
        d = r.json()
        batch = (d.get("data") or {}).get("batchCustomLink", [])
        if not batch:
            return jsonify({'error': 'empty batch', 'raw': d}), 500
        item = batch[0]
        if item.get("failCode") != 0:
            return jsonify({'error': f'failCode {item.get("failCode")}'}), 500
        sl = item.get("shortLink")
        ll = item.get("longLink") or sl   # longLink chứa utm_content=sub_id
        if not sl:
            return jsonify({'error': 'no shortLink'}), 500
        return jsonify({
            'success': True,
            'affiliate_url': ll,   # long link có tracking sub_id
            'short_link': sl,      # short link để chia sẻ
            'sub_id': sub or None
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ══════════════════════════════════════════════════════════════
# /api/commission — Lấy hoa hồng, 2 tầng fallback
# ══════════════════════════════════════════════════════════════

@app.route("/api/commission", methods=["GET"])
def commission():
    """
    Chấp nhận:
      ?item_id=xxx            (từ convert.php cũ)
      ?url=https://shopee.vn/...  (để trích shopid)
      ?item_id=xxx&url=...    (tốt nhất — để lấy cả shopid)

    Tầng 1: Affiliate API (cần cookie, hay bị block cloud IP)
    Tầng 2: Shopee Public API (không cần cookie, lấy ảnh/tên/giá + ước tính HH)
    """
    item_id = request.args.get('item_id', '').strip()
    raw_url = request.args.get('url', '').strip()

    # ─── Trích IDs từ URL ───
    shopid = None
    if raw_url:
        # Giải ngắn link nếu cần
        if 's.shopee.vn' in raw_url or 'shp.ee' in raw_url:
            raw_url = resolve_url(raw_url)
        shopid, url_itemid = extract_ids(raw_url)
        if not item_id and url_itemid:
            item_id = url_itemid

    if not item_id:
        return jsonify({'success': False, 'error': 'Không tìm được item_id'}), 200

    cookie = clean_cookie(COOKIE)
    csrf   = extract_csrf(cookie)

    # ════════════════════════════════════════════════
    # TẦNG 1 — Shopee Affiliate API (thực tế)
    # Cần cookie affiliate + hay bị block từ cloud IP
    # ════════════════════════════════════════════════
    if cookie:
        aff_headers = {
            "content-type":   "application/json",
            "cookie":         cookie,
            "user-agent":     "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
            "referer":        "https://affiliate.shopee.vn/offer/product_offers",
            "origin":         "https://affiliate.shopee.vn",
            "accept":         "application/json, text/plain, */*",
            "accept-language":"vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            "x-shopee-language": "vi",
        }
        if csrf:
            aff_headers["x-csrftoken"] = csrf  # Header bắt buộc mà code cũ thiếu

        try:
            resp = requests.get(
                f"https://affiliate.shopee.vn/api/v3/offer/product?item_id={item_id}",
                headers=aff_headers, timeout=15
            )
            if resp.status_code == 200:
                d = resp.json()
                if d.get("code") == 0 and d.get("data"):
                    info      = d["data"]
                    item_info = info.get("batch_item_for_item_card_full", {})

                    comm_str = info.get("commission", "0")
                    comm_num = int(re.sub(r"[^\d]", "", comm_str)) if comm_str else 0
                    rate     = info.get("commission_rate", {}).get("seller_commission_rate", "0%")

                    p_raw = item_info.get("price_min") or item_info.get("price", 0)
                    p_vnd = int(str(p_raw)) // 100000

                    p_before_raw = item_info.get("price_min_before_discount",
                                                 item_info.get("price_before_discount", 0))
                    p_before_vnd = int(str(p_before_raw or 0)) // 100000

                    return jsonify({
                        'success': True,
                        'item_id': item_id,
                        # Thông tin sản phẩm
                        'product_name': item_info.get("name", ""),
                        'image':        item_info.get("image", ""),
                        'shop_id':      str(item_info.get("shopid", shopid or "")),
                        'price':        format_vnd(p_vnd),
                        'price_before': format_vnd(p_before_vnd) if p_before_vnd > p_vnd else '',
                        'discount':     item_info.get("discount", ""),
                        # Hoa hồng thực
                        'seller_commission_rate': rate,
                        'estimated_commission':   comm_str,
                        'estimated_cashback':     format_vnd(comm_num // 2),
                        'cashback_percent':       50,
                        'is_estimated':           False,  # Số THỰC từ affiliate API
                    })
            # Nếu status != 200 → xuống tầng 2
            print(f"[AffAPI] HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"[AffAPI] Error: {e}")

    # ════════════════════════════════════════════════
    # TẦNG 2 — Shopee Public API (ước tính hoa hồng)
    # Không cần cookie, không bị block cloud IP
    # Cần CẢ shopid VÀ itemid
    # ════════════════════════════════════════════════
    product = None
    if shopid and item_id:
        product = get_product_public(shopid, item_id)

    if product:
        p_vnd = product['price_vnd']
        # Ước tính HH ~5% (Shopee seller thường 3-10%)
        comm_est = int(p_vnd * 0.05)
        return jsonify({
            'success': True,
            'item_id': item_id,
            # Thông tin sản phẩm THỰC từ public API
            'product_name': product['name'],
            'image':        product['image'],
            'shop_id':      shopid,
            'price':        product['price'],
            'price_before': product.get('price_before', ''),
            'discount':     product.get('discount', ''),
            # Hoa hồng ƯỚC TÍNH
            'seller_commission_rate': '~5%',
            'estimated_commission':   format_vnd(comm_est),
            'estimated_cashback':     format_vnd(comm_est // 2),
            'cashback_percent':       50,
            'is_estimated':           True,  # Hiển thị badge "ước tính"
        })

    # ════════════════════════════════════════════════
    # TẦNG 3 — Không lấy được gì (thiếu shopid)
    # Trả về success=False nhẹ nhàng để PHP xử lý
    # ════════════════════════════════════════════════
    return jsonify({
        'success': False,
        'item_id': item_id,
        'error': 'affiliate_blocked',
        'note':  'Shopee chặn API từ cloud IP. Thêm ?url= để dùng public API.'
    }), 200


# ══════════════════════════════════════════════════════════════
# /api/orders — Lấy danh sách đơn hàng theo sub_id
# ══════════════════════════════════════════════════════════════

@app.route("/api/orders", methods=["GET"])
def orders():
    sub_id = request.args.get('sub_id')
    if not sub_id:
        return jsonify({'error': 'Missing sub_id'}), 400

    qs = urllib.parse.urlencode({
        'page_size':       request.args.get('page_size', '20'),
        'page_num':        request.args.get('page_num', '1'),
        'sub_id':          sub_id,
        'purchase_time_s': request.args.get('start', ''),
        'purchase_time_e': request.args.get('end', ''),
        'version':         '1'
    })
    cookie = clean_cookie(COOKIE)
    h = {
        "content-type": "application/json",
        "cookie": cookie,
        "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    }
    try:
        r = requests.get(
            f"https://affiliate.shopee.vn/api/v3/report/list?{qs}",
            headers=h, timeout=20
        )
        d = r.json()
        if d.get('code') != 0:
            return jsonify({'error': 'Shopee error', 'detail': d}), 500

        lst = (d.get('data') or {}).get('list') or []
        out = []
        for o in lst:
            c_str = str(o.get('commission', '0'))
            c_num = int(re.sub(r"[^\d]", "", c_str)) if c_str else 0
            out.append({
                'order_sn':     o.get('order_sn'),
                'item_id':      o.get('item_id'),
                'product_name': o.get('product_name', ''),
                'amount':       o.get('amount'),
                'commission':   o.get('commission'),
                'cashback':     format_vnd(c_num // 2),
                'status':       o.get('status'),
                'purchase_time':o.get('purchase_time'),
                'shop_name':    o.get('shop_name', ''),
            })

        return jsonify({
            'success':     True,
            'sub_id':      sub_id,
            'page_num':    (d.get('data') or {}).get('page_num', 1),
            'page_size':   (d.get('data') or {}).get('page_size', 20),
            'total_count': (d.get('data') or {}).get('total_count', 0),
            'orders':      out
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route("/", methods=["GET"])
def health():
    return "SaleVN API OK", 200


if __name__ == "__main__":
    app.run(debug=True)
