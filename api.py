from flask import Flask, request, jsonify
import os, json, re, urllib.parse, requests

app = Flask(__name__)
COOKIE = os.environ.get("SHOPEE_COOKIE", "")

def clean_cookie(raw):
    return (raw or "").replace('"', "").replace("'", "").strip()

def resolve_url(url):
    """Resolve link rút gọn → link đích (follow HTTP redirect)"""
    try:
        if not url.startswith('http'):
            url = 'https://' + url
        r = requests.get(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15'},
            timeout=15,
            allow_redirects=True
        )
        return r.url
    except Exception:
        return url

def extract_ids(url):
    m = re.search(r'\/(\d+)\/(\d+)(?:\?|$|&)', url)
    if m: return m.group(1), m.group(2)
    m = re.search(r'[?&]item_id=(\d+)', url)
    if m: return None, m.group(1)
    m = re.search(r'-i\.(\d+)\.(\d+)', url)
    if m: return m.group(1), m.group(2)
    return None, None

def format_money(num):
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
    raw_url = request.args.get('url', '')
    item_id = request.args.get('item_id', '')
    
    # Nếu không có item_id, resolve URL rút gọn rồi extract
    if not item_id:
        is_short = any(x in raw_url for x in ['s.shopee.vn', 'shp.ee', 'vn.shp.ee'])
        resolved = resolve_url(raw_url) if is_short else raw_url
        shopid, itemid = extract_ids(resolved)
        item_id = itemid
    
    if not item_id:
        return jsonify({'success': False, 'debug': 'Cannot extract item_id from URL'}), 200
    
    try:
        # Dùng API addlivetag.com (không cần cookie Shopee)
        r = requests.get(
            f"https://data.addlivetag.com/product-data/product-data.php?item_id={item_id}",
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
            timeout=15
        )
        d = r.json()
        
        if d.get('status') != 'success':
            return jsonify({'success': False, 'debug': 'addlivetag API error', 'detail': d}), 200
        
        info = d.get('productInfo', {})
        if not info:
            return jsonify({'success': False, 'debug': 'Empty productInfo'}), 200
        
        product_name = info.get('productName', 'Sản phẩm Shopee')
        image = info.get('imageUrl', '')  # URL đầy đủ từ addlivetag
        price_val = info.get('price', 0)
        price_str = format_money(price_val) if price_val else ''
        
        # Hoa hồng người bán (Xtra) — không giới hạn trần
        seller_com = info.get('sellerComFinal', 0)
        if seller_com is None or not info.get('hasSellerCommission', False):
            seller_com = 0
        
        # Chia đôi 50/50
        user_cashback = seller_com // 2
        platform_fee = seller_com - user_cashback
        
        seller_rate = info.get('sellerRatePercent', 0)
        seller_rate_str = f"{seller_rate}%" if seller_rate else '~5%'
        
        return jsonify({
            'success': True,
            'item_id': item_id,
            'product_name': product_name,
            'image': image,
            'price': price_str,
            'seller_commission_rate': seller_rate_str,
            'seller_commission': format_money(seller_com),
            'estimated_commission': format_money(seller_com),
            'estimated_cashback': format_money(user_cashback),
            'user_cashback': format_money(user_cashback),
            'platform_fee': format_money(platform_fee),
            'cashback_percent': 50,
            'data_source': info.get('dataSource', 'unknown')
        })
        
    except Exception as e:
        return jsonify({'success': False, 'debug': str(e)}), 200

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
        "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
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
                'cashback': format_money(n // 2),
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
