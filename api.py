from flask import Flask, request, jsonify
import os, json, re, urllib.parse, requests

app = Flask(__name__)
COOKIE = os.environ.get("SHOPEE_COOKIE", "")

def clean_cookie(raw):
    return (raw or "").replace('"', "").replace("'", "").strip()

def resolve_url(url):
    try:
        r = requests.get(url if url.startswith('http') else 'https://' + url,
            headers={'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15'},
            timeout=10, allow_redirects=True)
        return r.url
    except:
        return url

def extract_ids(url):
    m = re.search(r'\/(\d+)\/(\d+)(?:\?|$|&)', url)
    if m: return m.group(1), m.group(2)
    m = re.search(r'[?&]item_id=(\d+)', url)
    if m: return None, m.group(1)
    m = re.search(r'-i\.(\d+)\.(\d+)', url)
    if m: return m.group(1), m.group(2)
    return None, None

def parse_vnd_number(vnd_str):
    if not vnd_str:
        return 0
    try:
        return int(re.sub(r"[^\d]", "", str(vnd_str)))
    except:
        return 0

def format_vnd(num):
    try:
        return f"₫{int(num):,}".replace(',', '.')
    except:
        return "₫0"

def get_csrf_from_cookie(cookie_str):
    """Extract csrftoken from cookie string if present"""
    m = re.search(r'csrftoken=([^;]+)', cookie_str)
    if m:
        return m.group(1)
    return ''

def get_product_public(shopid, itemid):
    """Fallback: lấy thông tin SP từ API public"""
    try:
        r = requests.get(
            f"https://shopee.vn/api/v4/item/get?itemid={itemid}&shopid={shopid}",
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json',
                'Referer': f'https://shopee.vn/product/{shopid}/{itemid}'
            }, timeout=10)
        d = r.json()
        if not d.get('data') or not d['data'].get('item'):
            return None
        item = d['data']['item']
        p = item.get('price', 0)
        try:
            price = f"₫{int(p)/100000:,.0f}".replace(',', '.')
        except:
            price = ''
        return {
            'name': item.get('name', 'Sản phẩm'),
            'image': item.get('image', ''),
            'price': price,
            'raw_price': int(p) if p else 0
        }
    except Exception as e:
        print(f"[SaleVN API] Public API error: {e}")
        return None

@app.route("/api/convert", methods=["POST"])
def convert():
    data = request.get_json() or {}
    url = data.get('url', '').strip()
    sub = data.get('sub_id', '')
    if not url: return jsonify({'error': 'Missing url'}), 400
    cookie = clean_cookie(COOKIE)
    if not cookie: return jsonify({'error': 'No cookie'}), 500
    api_url = url if url.startswith('http') else 'https://' + url
    lp = [{"originalLink": api_url}]
    if sub: lp[0]["advancedLinkParams"] = {"subId1": str(sub)}
    payload = {
        "operationName": "batchGetCustomLink",
        "query": "query batchGetCustomLink($linkParams: [CustomLinkParam!], $sourceCaller: SourceCaller){batchCustomLink(linkParams: $linkParams, sourceCaller: $sourceCaller){shortLink longLink failCode}}",
        "variables": {"linkParams": lp, "sourceCaller": "CUSTOM_LINK_CALLER"}
    }
    h = {
        "content-type": "application/json",
        "cookie": cookie,
        "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    }
    try:
        r = requests.post("https://affiliate.shopee.vn/api/v3/gql?q=batchCustomLink", headers=h, json=payload, timeout=20)
        d = r.json()
        batch = d.get("data", {}).get("batchCustomLink", [])
        if not batch: return jsonify({'error': 'empty batch'}), 500
        item = batch[0]
        if item.get("failCode") != 0: return jsonify({'error': f'failCode {item.get("failCode")}'}), 500
        sl = item.get("shortLink")
        if not sl: return jsonify({'error': 'no shortLink'}), 500
        return jsonify({'success': True, 'affiliate_url': sl, 'short_link': sl, 'sub_id': sub or None})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route("/api/commission", methods=["GET"])
def commission():
    item_id = request.args.get('item_id', '')
    shop_id = request.args.get('shop_id', '')
    raw_url = request.args.get('url', '')

    if not item_id:
        return jsonify({'success': False, 'error': 'Missing item_id'}), 200

    cookie = clean_cookie(COOKIE)
    if not cookie:
        return jsonify({'success': False, 'error': 'No cookie configured on server'}), 200

    # Thử lấy từ API affiliate trước
    affiliate_data = None
    try:
        csrf = get_csrf_from_cookie(cookie)
        h = {
            "accept": "application/json",
            "content-type": "application/json",
            "cookie": cookie,
            "origin": "https://affiliate.shopee.vn",
            "referer": f"https://affiliate.shopee.vn/offer/product?item_id={item_id}",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "x-requested-with": "XMLHttpRequest",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin"
        }
        if csrf:
            h["x-csrftoken"] = csrf

        # Thử GET trước
        qs = f"item_id={item_id}"
        if shop_id:
            qs += f"&shop_id={shop_id}"

        api_url = f"https://affiliate.shopee.vn/api/v3/offer/product?{qs}"
        print(f"[SaleVN API] GET {api_url}")
        r = requests.get(api_url, headers=h, timeout=20)
        print(f"[SaleVN API] GET status: {r.status_code}, len: {len(r.text)}")

        # Nếu GET 403, thử POST
        if r.status_code == 403:
            print("[SaleVN API] GET 403, trying POST...")
            post_body = {"item_id": str(item_id)}
            if shop_id:
                post_body["shop_id"] = str(shop_id)
            r = requests.post(
                "https://affiliate.shopee.vn/api/v3/offer/product",
                headers=h, json=post_body, timeout=20
            )
            print(f"[SaleVN API] POST status: {r.status_code}, len: {len(r.text)}")

        if r.status_code != 200:
            raw_snippet = r.text[:300]
            print(f"[SaleVN API] Raw: {raw_snippet}")
            # Không return lỗi ngay, chuyển sang fallback
            raise Exception(f"HTTP {r.status_code}")

        try:
            d = r.json()
        except Exception as je:
            print(f"[SaleVN API] JSON parse error: {je}")
            raise Exception("JSON parse error")

        shopee_code = d.get('code')
        shopee_msg = d.get('msg', '')

        if shopee_code != 0:
            print(f"[SaleVN API] Shopee code={shopee_code}, msg={shopee_msg}")
            raise Exception(f"Shopee API error: {shopee_msg} (code {shopee_code})")

        data = d.get('data')
        if not data:
            raise Exception("Empty data")

        affiliate_data = data

    except Exception as e:
        print(f"[SaleVN API] Affiliate API failed: {e}")
        affiliate_data = None

    # Nếu affiliate thất bại, dùng public API làm fallback
    if not affiliate_data:
        if not shop_id:
            # Thử extract shop_id từ URL
            sid, _ = extract_ids(raw_url)
            if sid:
                shop_id = sid

        if not shop_id:
            return jsonify({
                'success': False,
                'error': 'Không thể lấy thông tin hoa hồng. Vui lòng đảm bảo cookie affiliate còn hiệu lực.'
            }), 200

        pub = get_product_public(shop_id, item_id)
        if not pub:
            return jsonify({
                'success': False,
                'error': 'Không thể lấy thông tin sản phẩm từ cả 2 nguồn.'
            }), 200

        # Fallback: hiển thị thông tin SP + hoàn tiền ước tính theo % chung
        price_val = pub['raw_price']
        est_comm = int(price_val * 0.05 / 100000)  # ước tính 5%
        cashback = est_comm // 2

        return jsonify({
            'success': True,
            'item_id': item_id,
            'product_name': pub['name'],
            'image': pub['image'],
            'price': pub['price'],
            'seller_commission_rate': '~5% (ước tính)',
            'estimated_commission': format_vnd(est_comm),
            'estimated_cashback': format_vnd(cashback),
            'cashback_percent': 50,
            'note': 'Ước tính do API affiliate bị chặn (403). Số tiền thực tế có thể khác.'
        })

    # Xử lý dữ liệu affiliate
    try:
        data = affiliate_data
        item = data.get('batch_item_for_item_card_full') or {}

        product_name = item.get('name', 'Sản phẩm Shopee')
        image = item.get('image', '')

        raw_price = item.get('price', '0')
        try:
            price_val = int(raw_price) / 100000
            price_str = f"₫{price_val:,.0f}".replace(',', '.')
        except Exception:
            price_str = ''
            price_val = 0

        comm_rate = data.get('commission_rate', {})
        seller_commission_str = comm_rate.get('seller_commission') or data.get('commission', '₫0') or '₫0'
        seller_commission_num = parse_vnd_number(seller_commission_str)
        seller_rate = comm_rate.get('seller_commission_rate', '~5%')

        cashback_num = seller_commission_num // 2
        cashback_str = format_vnd(cashback_num)

        print(f"[SaleVN API] OK item={item_id} comm={seller_commission_str} cashback={cashback_str}")

        return jsonify({
            'success': True,
            'item_id': item_id,
            'product_name': product_name,
            'image': image,
            'price': price_str,
            'seller_commission_rate': seller_rate,
            'estimated_commission': seller_commission_str,
            'estimated_cashback': cashback_str,
            'cashback_percent': 50
        })
    except Exception as e:
        print(f"[SaleVN API] Parse affiliate data error: {e}")
        return jsonify({'success': False, 'error': f'Parse error: {str(e)}'}), 200

@app.route("/api/orders", methods=["GET"])
def orders():
    sub_id = request.args.get('sub_id')
    if not sub_id: return jsonify({'error': 'Missing sub_id'}), 400
    qs = urllib.parse.urlencode({
        'page_size': request.args.get('page_size', '20'),
        'page_num': request.args.get('page_num', '1'),
        'sub_id': sub_id,
        'purchase_time_s': request.args.get('start', ''),
        'purchase_time_e': request.args.get('end', ''),
        'version': '1'
    })
    cookie = clean_cookie(COOKIE)
    h = {
        "content-type": "application/json",
        "cookie": cookie,
        "referer": "https://affiliate.shopee.vn/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(f"https://affiliate.shopee.vn/api/v3/report/list?{qs}", headers=h, timeout=20)
        d = r.json()
        if d.get('code') != 0: return jsonify({'error': 'Shopee error', 'detail': d}), 500
        lst = (d.get('data') or {}).get('list') or []
        out = []
        for o in lst:
            c = str(o.get('commission', '0'))
            n = int(re.sub(r"[^\d]", "", c)) if c else 0
            out.append({
                'order_sn': o.get('order_sn'),
                'item_id': o.get('item_id'),
                'product_name': o.get('product_name', ''),
                'amount': o.get('amount'),
                'commission': o.get('commission'),
                'cashback': f"₫{n//2:,}".replace(',', '.'),
                'status': o.get('status'),
                'purchase_time': o.get('purchase_time'),
                'shop_name': o.get('shop_name', '')
            })
        return jsonify({
            'success': True,
            'sub_id': sub_id,
            'page_num': (d.get('data') or {}).get('page_num', 1),
            'page_size': (d.get('data') or {}).get('page_size', 20),
            'total_count': (d.get('data') or {}).get('total_count', 0),
            'orders': out
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route("/", methods=["GET"])
def health():
    return "OK", 200

if __name__ == "__main__":
    app.run(debug=True)
