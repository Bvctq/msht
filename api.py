from flask import Flask, request, jsonify
import os
import json
import re
import urllib.parse
import requests

app = Flask(__name__)

INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "")
SHOPEE_COOKIE = os.environ.get("SHOPEE_COOKIE", "")

def require_auth():
    key = request.headers.get('x-api-key', '')
    if key != INTERNAL_API_KEY:
        return jsonify({'error': 'Unauthorized'}), 401
    return None

def clean_cookie(raw):
    return (raw or "").replace('"', "").replace("'", "").strip()

@app.route("/api/convert", methods=["POST"])
def api_convert():
    auth_error = require_auth()
    if auth_error:
        return auth_error

    data = request.get_json() or {}
    url = data.get('url', '').strip()
    sub_id = data.get('sub_id', '')

    if not url:
        return jsonify({'error': 'Missing url'}), 400

    cookie = clean_cookie(SHOPEE_COOKIE)
    if not cookie:
        return jsonify({'error': 'No Shopee cookie configured'}), 500

    api_url = url if url.startswith('http') else 'https://' + url
    link_params = [{"originalLink": api_url}]
    if sub_id:
        link_params[0]["advancedLinkParams"] = {"subId1": str(sub_id)}

    payload = {
        "operationName": "batchGetCustomLink",
        "query": (
            "query batchGetCustomLink($linkParams: [CustomLinkParam!], $sourceCaller: SourceCaller){"
            "batchCustomLink(linkParams: $linkParams, sourceCaller: $sourceCaller){shortLink longLink failCode}}"
        ),
        "variables": {
            "linkParams": link_params,
            "sourceCaller": "CUSTOM_LINK_CALLER",
        },
    }
    headers = {
        "content-type": "application/json",
        "cookie": cookie,
        "user-agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
        ),
    }
    
    try:
        resp = requests.post(
            "https://affiliate.shopee.vn/api/v3/gql?q=batchCustomLink",
            headers=headers,
            json=payload,
            timeout=20,
        )
        result_data = resp.json()
        batch = result_data.get("data", {}).get("batchCustomLink", [])
        if not batch:
            return jsonify({'error': 'Shopee returned empty batch', 'raw': result_data}), 500
        
        item = batch[0]
        if item.get("failCode") != 0:
            return jsonify({'error': f'Shopee failCode {item.get("failCode")}', 'raw': result_data}), 500
            
        short_link = item.get("shortLink")
        if not short_link:
            return jsonify({'error': 'No shortLink in response', 'raw': result_data}), 500
        
        return jsonify({
            'success': True,
            'original_url': url,
            'sub_id': sub_id or None,
            'affiliate_url': short_link,
            'short_link': short_link
        })
    except Exception as e:
        print(f"[Convert Error] {e}")
        return jsonify({'error': str(e)}), 500

@app.route("/api/commission", methods=["GET"])
def api_commission():
    auth_error = require_auth()
    if auth_error:
        return auth_error

    item_id = request.args.get('item_id')
    purl = request.args.get('url')

    if not item_id and purl:
        m = re.search(r'product\/(\d+)\/(\d+)', purl)
        if m:
            item_id = m.group(2)
        else:
            m = re.search(r'[?&]item_id=(\d+)', purl)
            if m:
                item_id = m.group(1)
            else:
                m = re.search(r'-i\.(\d+)\.(\d+)', purl)
                if m:
                    item_id = m.group(2)

    if not item_id:
        return jsonify({'error': 'Missing item_id or url'}), 400

    cookie = clean_cookie(SHOPEE_COOKIE)
    headers = {
        "content-type": "application/json",
        "cookie": cookie,
        "user-agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
        ),
    }

    try:
        url = f"https://affiliate.shopee.vn/api/v3/offer/product?item_id={item_id}"
        print(f"[Commission] Requesting {url}")
        
        resp = requests.get(url, headers=headers, timeout=20)
        print(f"[Commission] Status: {resp.status_code}")
        print(f"[Commission] Content-Type: {resp.headers.get('content-type', 'unknown')}")
        
        # Nếu Shopee trả HTML (cookie hết hạn), báo lỗi rõ
        if 'text/html' in resp.headers.get('content-type', ''):
            print(f"[Commission] HTML response (cookie expired?)")
            return jsonify({'error': 'Shopee returned HTML - cookie may be expired'}), 500
        
        # Thử parse JSON
        try:
            data = resp.json()
        except Exception as je:
            print(f"[Commission] JSON parse error: {je}")
            print(f"[Commission] Raw text: {resp.text[:500]}")
            return jsonify({'error': 'Invalid JSON from Shopee', 'raw_preview': resp.text[:200]}), 500
        
        print(f"[Commission] Response code: {data.get('code')}")
        
        code = data.get("code")
        if code != 0:
            return jsonify({
                'error': 'Shopee API error', 
                'shopee_code': code,
                'shopee_msg': data.get('msg', 'unknown'),
                'detail': data
            }), 500
        
        d = data.get("data")
        if not d:
            return jsonify({'error': 'Empty data from Shopee'}), 500

        # Lấy hoa hồng người bán
        comm_rate = d.get("commission_rate") or {}
        
        # Ưu tiên seller_commission, fallback về commission
        seller_comm_raw = comm_rate.get("seller_commission")
        if seller_comm_raw is None:
            seller_comm_raw = d.get("commission", "0")
        
        seller_comm_str = str(seller_comm_raw or "0")
        rate_str = str(comm_rate.get("seller_commission_rate", "0%") if comm_rate else "0%")
        
        # Parse số tiền
        comm_clean = re.sub(r"[^\d]", "", seller_comm_str)
        comm_num = int(comm_clean) if comm_clean else 0
        cashback = comm_num // 2

        product_info = d.get("batch_item_for_item_card_full") or {}
        
        # Giá Shopee trả về *100000
        price_raw = product_info.get("price", "0")
        try:
            price_num = int(price_raw) / 100000
            price_str = f"₫{price_num:,.0f}".replace(",", ".")
        except Exception:
            price_str = ""

        return jsonify({
            'success': True,
            'item_id': item_id,
            'product_name': product_info.get("name", "Sản phẩm Shopee"),
            'image': product_info.get("image", ""),
            'price': price_str,
            'seller_commission_rate': rate_str,
            'estimated_commission': seller_comm_str,
            'estimated_cashback': f"₫{cashback:,}".replace(",", "."),
            'cashback_percent': 50,
        })
    except Exception as e:
        print(f"[Commission Exception] {type(e).__name__}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route("/api/orders", methods=["GET"])
def api_orders():
    auth_error = require_auth()
    if auth_error:
        return auth_error

    sub_id = request.args.get('sub_id')
    if not sub_id:
        return jsonify({'error': 'Missing sub_id'}), 400

    start = request.args.get('start', '')
    end = request.args.get('end', '')
    page_num = request.args.get('page_num', '1')
    page_size = request.args.get('page_size', '20')

    qs = urllib.parse.urlencode({
        'page_size': page_size,
        'page_num': page_num,
        'sub_id': sub_id,
        'purchase_time_s': start,
        'purchase_time_e': end,
        'version': '1'
    })

    cookie = clean_cookie(SHOPEE_COOKIE)
    headers = {
        "content-type": "application/json",
        "cookie": cookie,
        "user-agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
        ),
    }

    try:
        resp = requests.get(
            f"https://affiliate.shopee.vn/api/v3/report/list?{qs}",
            headers=headers,
            timeout=20,
        )
        data = resp.json()
        if data.get("code") != 0:
            return jsonify({'error': 'Shopee API error', 'detail': data}), 500

        order_list = (data.get("data") or {}).get("list") or []
        orders = []
        for o in order_list:
            comm_str = str(o.get("commission", "0"))
            comm_clean = re.sub(r"[^\d]", "", comm_str)
            comm_num = int(comm_clean) if comm_clean else 0
            orders.append({
                'order_sn': o.get("order_sn"),
                'item_id': o.get("item_id"),
                'product_name': o.get("product_name", ""),
                'amount': o.get("amount"),
                'commission': o.get("commission"),
                'cashback': f"₫{comm_num // 2:,}".replace(",", "."),
                'status': o.get("status"),
                'purchase_time': o.get("purchase_time"),
                'shop_name': o.get("shop_name", ""),
            })

        return jsonify({
            'success': True,
            'sub_id': sub_id,
            'page_num': (data.get("data") or {}).get("page_num", 1),
            'page_size': (data.get("data") or {}).get("page_size", 20),
            'total_count': (data.get("data") or {}).get("total_count", 0),
            'orders': orders
        })
    except Exception as e:
        print(f"[Orders Exception] {e}")
        return jsonify({'error': str(e)}), 500

@app.route("/", methods=["GET"])
def health():
    return "Shopee Affiliate API is running!", 200

if __name__ == "__main__":
    app.run(debug=True)
