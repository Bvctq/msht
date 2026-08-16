from flask import Flask, request, jsonify
import os
import json
import re
import urllib.parse
import requests


# Cập nhật hàm giải mã link ngắn Shopee ở đầu file hoặc trong phần HELPERS
def unshorten_shopee_url(url):
    if not url:
        return url
    if 's.shopee.vn' in url or 'shp.ee' in url or 'shopee.ee' in url:
        try:
            resp = requests.head(url, allow_redirects=True, timeout=10, headers={'User-Agent': USER_AGENT})
            return resp.url
        except Exception as e:
            print(f"Unshorten Shopee error: {e}")
    return url


@app.route("/api/commission", methods=["GET"])
def api_commission():
    auth_error = require_auth()
    if auth_error:
        return auth_error

    item_id = request.args.get('item_id')
    purl = request.args.get('url')

    # 1. Giải mã link ngắn & Bóc tách item_id nếu chưa có
    if not item_id and purl:
        resolved_url = unshorten_shopee_url(purl)
        
        m = re.search(r'product\/(\d+)\/(\d+)', resolved_url) or \
            re.search(r'-i\.(\d+)\.(\d+)', resolved_url) or \
            re.search(r'i\.(\d+)\.(\d+)', resolved_url) or \
            re.search(r'[?&]item_id=(\d+)', resolved_url)
            
        if m:
            item_id = m.group(m.lastindex)

    if not item_id:
        return jsonify({'error': 'Không trích xuất được item_id từ URL'}), 400

    cookie_val = clean_cookie(SHOPEE_COOKIE)
    if not cookie_val:
        return jsonify({'error': 'Chưa cấu hình SHOPEE_COOKIE trên Vercel'}), 500

    headers = {
        "content-type": "application/json",
        "cookie": cookie_val,
        "user-agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
        ),
    }

    try:
        resp = requests.get(
            f"https://affiliate.shopee.vn/api/v3/offer/product?item_id={item_id}",
            headers=headers,
            timeout=20,
        )
        data = resp.json()
        if data.get("code") != 0 or not data.get("data"):
            return jsonify({'error': 'Lỗi Shopee API hoặc Cookie hết hạn', 'detail': data}), 500

        d = data["data"]
        comm_rate_info = d.get("commission_rate", {})
        
        # ===== LOGIC TÍNH HOA HỒNG NGƯỜI BÁN & CHIA 50/50 =====
        # Lấy hoa hồng người bán trả (seller_commission)
        seller_comm_str = comm_rate_info.get("seller_commission") or d.get("commission", "0")
        rate_str = comm_rate_info.get("seller_commission_rate", "0%")
        
        # Bóc tách số tiền nguyên (VND)
        comm_num = int(re.sub(r"[^\d]", "", seller_comm_str)) if seller_comm_str else 0
        
        # Chia 50% cho người dùng, 50% giữ lại
        user_cashback_num = comm_num // 2  

        product_info = d.get("batch_item_for_item_card_full", {}) or {}
        
        # Giá Shopee trả về dạng số nhân 100.000 (vd: 18000000000 = 180.000đ)
        price_raw = product_info.get("price", "0")
        try:
            price_num = int(price_raw) / 100000
            price_str = f"₫{int(price_num):,}".replace(",", ".")
        except:
            price_str = ""

        return jsonify({
            'success': True,
            'item_id': item_id,
            'product_name': product_info.get("name", "Sản phẩm Shopee"),
            'image': product_info.get("image", ""),
            'price': price_str,
            'seller_commission_rate': rate_str,
            'total_seller_commission': f"₫{comm_num:,}".replace(",", "."),
            'estimated_cashback': f"₫{user_cashback_num:,}".replace(",", "."),
            'cashback_percent': 50,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
        
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
        "query": "query batchGetCustomLink($linkParams: [CustomLinkParam!], $sourceCaller: SourceCaller){batchCustomLink(linkParams: $linkParams, sourceCaller: $sourceCaller){shortLink longLink failCode}}",
        "variables": {"linkParams": link_params, "sourceCaller": "CUSTOM_LINK_CALLER"},
    }
    headers = {
        "content-type": "application/json",
        "cookie": cookie,
        "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
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
        return jsonify({'error': str(e)}), 500

@app.route("/api/commission", methods=["GET"])
def api_commission():
    auth_error = require_auth()
    if auth_error:
        return auth_error

    item_id = request.args.get('item_id', '').strip()
    purl = request.args.get('url', '')

    # Nếu không có item_id, parse từ url
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
        return jsonify({
            'success': False,
            'debug': 'Cannot extract item_id from url. Please use full shopee.vn product link.'
        }), 200

    cookie = clean_cookie(SHOPEE_COOKIE)
    headers = {
        "content-type": "application/json",
        "cookie": cookie,
        "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
    }

    try:
        url = f"https://affiliate.shopee.vn/api/v3/offer/product?item_id={item_id}"
        resp = requests.get(url, headers=headers, timeout=20)
        raw_text = resp.text
        
        try:
            data = resp.json()
        except Exception:
            return jsonify({
                'success': False,
                'debug': 'Shopee returned non-JSON (cookie expired?)',
                'http_status': resp.status_code,
                'content_type': resp.headers.get('content-type'),
                'raw_preview': raw_text[:500]
            }), 200
        
        code = data.get("code")
        if code != 0:
            return jsonify({
                'success': False,
                'debug': 'Shopee API error code',
                'shopee_code': code,
                'shopee_msg': data.get('msg'),
                'full_response': data
            }), 200
        
        d = data.get("data")
        if not d:
            return jsonify({
                'success': False,
                'debug': 'Shopee data is empty',
                'full_response': data
            }), 200

        comm_rate = d.get("commission_rate") or {}
        seller_comm_raw = comm_rate.get("seller_commission")
        if seller_comm_raw is None:
            seller_comm_raw = d.get("commission", "0")
        
        seller_comm_str = str(seller_comm_raw or "0")
        rate_str = str(comm_rate.get("seller_commission_rate", "0%") if comm_rate else "0%")
        
        comm_clean = re.sub(r"[^\d]", "", seller_comm_str)
        comm_num = int(comm_clean) if comm_clean else 0
        cashback = comm_num // 2

        product_info = d.get("batch_item_for_item_card_full") or {}
        
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
        return jsonify({
            'success': False,
            'debug': 'Python exception',
            'error_type': type(e).__name__,
            'error_msg': str(e)
        }), 200

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
        "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
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
        return jsonify({'error': str(e)}), 500

@app.route("/", methods=["GET"])
def health():
    return "Shopee Affiliate API is running!", 200

if __name__ == "__main__":
    app.run(debug=True)
