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
    """Chuyển '₫16.200' hoặc '16200' thành int 16200"""
    if not vnd_str:
        return 0
    try:
        return int(re.sub(r"[^\d]", "", str(vnd_str)))
    except:
        return 0

def format_vnd(num):
    """Chuyển 16200 thành '₫16.200'"""
    try:
        return f"₫{int(num):,}".replace(',', '.')
    except:
        return "₫0"

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
    h = {"content-type": "application/json", "cookie": cookie, "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"}
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
    if not item_id:
        return jsonify({'success': False, 'error': 'Missing item_id'}), 400

    cookie = clean_cookie(COOKIE)
    if not cookie:
        return jsonify({'success': False, 'error': 'No cookie'}), 500

    try:
        h = {
            "content-type": "application/json",
            "cookie": cookie,
            "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
        }
        # Gọi API affiliate lấy thông tin hoa hồng thực tế
        r = requests.get(
            f"https://affiliate.shopee.vn/api/v3/offer/product?item_id={item_id}",
            headers=h, timeout=20
        )
        d = r.json()

        if d.get('code') != 0:
            return jsonify({'success': False, 'error': 'Shopee API error', 'detail': d}), 500

        data = d.get('data', {})
        item = data.get('batch_item_for_item_card_full', {})

        # 1. Tên & ảnh sản phẩm
        product_name = item.get('name', 'Sản phẩm Shopee')
        image = item.get('image', '')

        # 2. Giá bán (đơn vị trong response là *100.000 VND)
        raw_price = item.get('price', '0')
        try:
            price_val = int(raw_price) / 100000
            price_str = f"₫{price_val:,.0f}".replace(',', '.')
        except Exception:
            price_str = ''
            price_val = 0

        # 3. Hoa hồng người bán thực tế
        comm_rate = data.get('commission_rate', {})
        # Ưu tiên seller_commission, fallback về data.commission
        seller_commission_str = comm_rate.get('seller_commission') or data.get('commission', '₫0')
        seller_commission_num = parse_vnd_number(seller_commission_str)

        # 4. Tỷ lệ % người bán trả
        seller_rate = comm_rate.get('seller_commission_rate', '~5%')

        # 5. Tính hoàn tiền cho user = 50% hoa hồng người bán
        cashback_num = seller_commission_num // 2
        cashback_str = format_vnd(cashback_num)

        return jsonify({
            'success': True,
            'item_id': item_id,
            'product_name': product_name,
            'image': image,
            'price': price_str,
            'seller_commission_rate': seller_rate,
            'estimated_commission': seller_commission_str,   # Hoa hồng gốc Shopee trả
            'estimated_cashback': cashback_str,              # User nhận 50%
            'cashback_percent': 50
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

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
    h = {"content-type": "application/json", "cookie": cookie, "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"}
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
