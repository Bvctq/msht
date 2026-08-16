import os
import re
from flask import Flask, request, jsonify
from curl_cffi import requests

# KHỞI TẠO TOP-LEVEL APP (Bắt buộc để Vercel nhận diện)
app = Flask(__name__)

# Lấy cookie từ biến môi trường của Vercel
SHOPEE_COOKIE = os.environ.get("SHOPEE_COOKIE", "")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def clean_cookie(cookie_str):
    if not cookie_str:
        return ""
    return cookie_str.strip().replace("\n", "").replace("\r", "")

def require_auth():
    # Đã tắt kiểm tra api-key theo yêu cầu
    return None

def unshorten_shopee_url(url):
    return url

@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "running", "service": "Shopee Affiliate API Active"})

@app.route("/api/convert", methods=["POST", "GET"])
def api_convert():
    auth_error = require_auth()
    if auth_error:
        return auth_error
    
    # Logic xử lý tạo link rút gọn của bạn ở đây
    return jsonify({
        "success": True,
        "message": "Convert endpoint is working"
    })

@app.route("/api/commission", methods=["GET"])
def api_commission():
    auth_error = require_auth()
    if auth_error:
        return auth_error

    item_id = (request.args.get('item_id') or '').strip()
    purl = (request.args.get('url') or '').strip()

    if not item_id and purl:
        purl = unshorten_shopee_url(purl)
        m = (
            re.search(r'-i\.\d+\.(\d+)', purl)
            or re.search(r'product\/\d+\/(\d+)', purl)
            or re.search(r'[?&](?:item_id|itemid|itemId)=(\d+)', purl)
        )
        if m:
            item_id = m.group(1)

    if not item_id:
        return jsonify({'error': 'Missing item_id or url', 'resolved_url': purl}), 400

    shopee_cookie = clean_cookie(SHOPEE_COOKIE)
    if not shopee_cookie:
        return jsonify({'error': 'Biến SHOPEE_COOKIE trên Vercel đang bị rỗng'}), 500

    # Tự động trích xuất csrftoken từ cookie để vượt WAF của Shopee
    csrftoken = ""
    csrf_match = re.search(r'csrftoken=([^;]+)', shopee_cookie)
    if csrf_match:
        csrftoken = csrf_match.group(1)

    headers = {
        "accept": "application/json, text/plain, * / *",
        "accept-language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        "content-type": "application/json",
        "cookie": shopee_cookie,
        "origin": "https://affiliate.shopee.vn",
        "referer": "https://affiliate.shopee.vn/offer/product_offer",
        "user-agent": USER_AGENT,
        "x-shopee-language": "vi",
        "x-csrftoken": csrftoken,
    }

    try:
        # Sử dụng curl_cffi với impersonate="chrome110" để qua mặt hệ thống check vân tay của Shopee
        resp = requests.get(
            f"https://affiliate.shopee.vn/api/v3/offer/product?item_id={item_id}",
            headers=headers,
            impersonate="chrome110",
            timeout=20,
        )

        try:
            data = resp.json()
        except Exception:
            return jsonify({
                'error': 'Shopee trả về trang HTML thay vì JSON (bị chặn IP)',
                'status_code': resp.status_code,
                'preview': resp.text[:300]
            }), 400

        if data.get("code") != 0 or not data.get("data"):
            return jsonify({
                'error': 'Shopee từ chối request',
                'shopee_raw_response': data
            }), 400

        d = data.get("data") or {}
        comm_rate_dict = d.get("commission_rate") or {}
        seller_comm_str = comm_rate_dict.get("seller_commission") or d.get("commission") or "0"
        rate_str = comm_rate_dict.get("seller_commission_rate") or "0%"

        comm_num = int(re.sub(r"[^\d]", "", str(seller_comm_str))) if seller_comm_str else 0
        cashback = comm_num // 2

        product_info = d.get("batch_item_for_item_card_full") or {}

        price_raw = product_info.get("price") or "0"
        try:
            price_num = int(price_raw) / 100000
            price_str = f"₫{price_num:,.0f}".replace(",", ".")
        except Exception:
            price_str = ""

        return jsonify({
            'success': True,
            'item_id': item_id,
            'product_name': product_info.get("name") or "Sản phẩm Shopee",
            'image': product_info.get("image") or "",
            'price': price_str,
            'seller_commission_rate': rate_str,
            'estimated_commission': seller_comm_str,
            'estimated_cashback': f"₫{cashback:,}".replace(",", "."),
            'cashback_percent': 50,
        })

    except Exception as e:
        return jsonify({'error': f'Python Exception: {str(e)}'}), 500

if __name__ == "__main__":
    app.run(debug=True)
