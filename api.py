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

def get_product(shopid, itemid):
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
        return {'name': item.get('name', 'Sản phẩm'), 'image': item.get('image', ''), 'price': price, 'raw_price': int(p) if p else 0}
    except:
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
    raw_url = request.args.get('url', '')
    resolved = resolve_url(raw_url) if ('s.shopee.vn' in raw_url or 'shp.ee' in raw_url) else raw_url
    shopid, itemid = extract_ids(resolved)
    if not itemid:
        return jsonify({'success': False, 'debug': 'Cannot extract IDs'}), 200
    if not shopid:
        return jsonify({'success': False, 'debug': 'Missing shopid'}), 200
    
    info = get_product(shopid, itemid)
    if not info:
        return jsonify({'success': False, 'debug': 'Public API failed'}), 200
    
    # Tính ước tính: giả định hoa hồng người bán ~5% giá bán, user nhận 50% = 2.5%
    price = info['raw_price']
    comm = int(price * 0.05 / 100000)  # 5% hoa hồng
    cashback = comm // 2  # user 50%
    
    return jsonify({
        'success': True,
        'item_id': itemid,
        'product_name': info['name'],
        'image': info['image'],
        'price': info['price'],
        'seller_commission_rate': '~5%',
        'estimated_commission': f"₫{comm:,}".replace(',', '.'),
        'estimated_cashback': f"₫{cashback:,}".replace(',', '.'),
        'cashback_percent': 50
    })

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
            out.append({'order_sn': o.get('order_sn'), 'item_id': o.get('item_id'), 'product_name': o.get('product_name', ''), 'amount': o.get('amount'), 'commission': o.get('commission'), 'cashback': f"₫{n//2:,}".replace(',', '.'), 'status': o.get('status'), 'purchase_time': o.get('purchase_time'), 'shop_name': o.get('shop_name', '')})
        return jsonify({'success': True, 'sub_id': sub_id, 'page_num': (d.get('data') or {}).get('page_num', 1), 'page_size': (d.get('data') or {}).get('page_size', 20), 'total_count': (d.get('data') or {}).get('total_count', 0), 'orders': out})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route("/", methods=["GET"])
def health():
    return "OK", 200

if __name__ == "__main__":
    app.run(debug=True)
