from flask import Flask, request, jsonify
import os, json, re, urllib.parse, requests

app = Flask(__name__)
COOKIE = os.environ.get("SHOPEE_COOKIE", "")

def clean_cookie(raw):
    return (raw or "").replace('"', "").replace("'", "").strip()

# ========== CONVERT ==========
@app.route("/api/convert", methods=["POST"])
def convert():
    data = request.get_json() or {}
    url = data.get('url', '').strip()
    sub = data.get('sub_id', '')
    if not url:
        return jsonify({'error': 'Missing url'}), 400

    cookie = clean_cookie(COOKIE)
    if not cookie:
        return jsonify({'error': 'No Shopee cookie configured'}), 500

    api_url = url if url.startswith('http') else 'https://' + url
    lp = [{"originalLink": api_url}]
    if sub:
        lp[0]["advancedLinkParams"] = {"subId1": str(sub)}

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
        if not batch:
            return jsonify({'error': 'empty batch', 'raw': d}), 500
        item = batch[0]
        if item.get("failCode") != 0:
            return jsonify({'error': f'failCode {item.get("failCode")}', 'raw': d}), 500
        sl = item.get("shortLink")
        if not sl:
            return jsonify({'error': 'no shortLink', 'raw': d}), 500
        return jsonify({'success': True, 'affiliate_url': sl, 'short_link': sl, 'sub_id': sub or None})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========== COMMISSION ==========
@app.route("/api/commission", methods=["GET"])
def commission():
    item_id = request.args.get('item_id', '').strip()
    purl = request.args.get('url', '')

    if not item_id and purl:
        for pat in [r'product\/(\d+)\/(\d+)', r'[?&]item_id=(\d+)', r'-i\.(\d+)\.(\d+)']:
            m = re.search(pat, purl)
            if m:
                item_id = m.group(2) if len(m.groups()) > 1 else m.group(1)
                break

    if not item_id:
        return jsonify({'success': False, 'debug': 'Missing item_id'}), 200

    cookie = clean_cookie(COOKIE)
    h = {
        "content-type": "application/json",
        "cookie": cookie,
        "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
        "referer": "https://affiliate.shopee.vn/",
        "accept": "application/json"
    }
    try:
        r = requests.get(f"https://affiliate.shopee.vn/api/v3/offer/product?item_id={item_id}", headers=h, timeout=20)
        try:
            d = r.json()
        except Exception:
            return jsonify({'success': False, 'debug': 'Shopee returned HTML', 'http': r.status_code}), 200

        if d.get('code') != 0:
            return jsonify({'success': False, 'debug': 'Shopee error', 'code': d.get('code'), 'msg': d.get('msg'), 'raw': d}), 200

        data = d.get('data', {})
        cr = data.get('commission_rate') or {}
        seller_comm = str(cr.get('seller_commission') or data.get('commission') or '0')
        rate = str(cr.get('seller_commission_rate', '0%'))
        comm_num = int(re.sub(r"[^\d]", "", seller_comm)) if seller_comm else 0
        cashback = comm_num // 2
        prod = data.get('batch_item_for_item_card_full') or {}
        price_raw = prod.get('price', '0')
        try:
            price = f"₫{int(price_raw)/100000:,.0f}".replace(',', '.')
        except:
            price = ''

        return jsonify({
            'success': True,
            'item_id': item_id,
            'product_name': prod.get('name', 'Sản phẩm Shopee'),
            'image': prod.get('image', ''),
            'price': price,
            'seller_commission_rate': rate,
            'estimated_commission': seller_comm,
            'estimated_cashback': f"₫{cashback:,}".replace(',', '.'),
            'cashback_percent': 50
        })
    except Exception as e:
        return jsonify({'success': False, 'debug': str(e)}), 200

# ========== ORDERS ==========
@app.route("/api/orders", methods=["GET"])
def orders():
    sub_id = request.args.get('sub_id')
    if not sub_id:
        return jsonify({'error': 'Missing sub_id'}), 400

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
        "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    }
    try:
        r = requests.get(f"https://affiliate.shopee.vn/api/v3/report/list?{qs}", headers=h, timeout=20)
        d = r.json()
        if d.get('code') != 0:
            return jsonify({'error': 'Shopee error', 'detail': d}), 500
        lst = (d.get('data') or {}).get('list') or []
        out = []
        for o in lst:
            c = str(o.get('commission', '0'))
            n = int(re.sub(r"[^\d]", "", c)) if c else 0
            out.append({
                'order_sn': o.get('order_sn'), 'item_id': o.get('item_id'),
                'product_name': o.get('product_name', ''), 'amount': o.get('amount'),
                'commission': o.get('commission'), 'cashback': f"₫{n//2:,}".replace(',', '.'),
                'status': o.get('status'), 'purchase_time': o.get('purchase_time'),
                'shop_name': o.get('shop_name', '')
            })
        return jsonify({
            'success': True, 'sub_id': sub_id,
            'page_num': (d.get('data') or {}).get('page_num', 1),
            'page_size': (d.get('data') or {}).get('page_size', 20),
            'total_count': (d.get('data') or {}).get('total_count', 0),
            'orders': out
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========== TEST COOKIE ==========
@app.route("/api/test", methods=["POST"])
def test():
    data = request.get_json() or {}
    cookie = clean_cookie(data.get('cookie', COOKIE))
    item_id = data.get('item_id', '23881637574')
    if not cookie:
        return jsonify({'error': 'No cookie'}), 400
    h = {
        "content-type": "application/json",
        "cookie": cookie,
        "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
        "referer": "https://affiliate.shopee.vn/",
        "accept": "application/json"
    }
    r = requests.get(f"https://affiliate.shopee.vn/api/v3/offer/product?item_id={item_id}", headers=h, timeout=20)
    try:
        d = r.json()
    except:
        return jsonify({'http': r.status_code, 'json': False, 'preview': r.text[:500]}), 200
    return jsonify({
        'http': r.status_code, 'json': True,
        'code': d.get('code'), 'msg': d.get('msg'),
        'has_commission_rate': bool((d.get('data') or {}).get('commission_rate')),
        'raw': d
    }), 200

@app.route("/", methods=["GET"])
def health():
    return "OK", 200

if __name__ == "__main__":
    app.run(debug=True)
