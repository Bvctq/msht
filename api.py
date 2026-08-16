# api.py
from flask import Flask, request, jsonify
import os
import re
import urllib.parse
import requests

app = Flask(__name__)

# ============================================================
# CONFIG
# ============================================================
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "")
SHOPEE_COOKIE = os.environ.get("SHOPEE_COOKIE", "")

SHOPEE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
)

TRAILING_PUNCT = '.,;:!?)]}\'"'


# ============================================================
# AUTH
# ============================================================
def require_auth():
    """
    Kiểm tra x-api-key từ PHP gửi sang.
    """
    key = request.headers.get('x-api-key', '')

    if not INTERNAL_API_KEY or key != INTERNAL_API_KEY:
        return jsonify({'error': 'Unauthorized'}), 401

    return None


# ============================================================
# HELPERS
# ============================================================
def clean_link(link):
    """
    Loại bỏ dấu thừa ở cuối link.
    """
    return (link or "").strip().rstrip(TRAILING_PUNCT)


def normalize_link_for_api(link):
    """
    Chuẩn hóa link trước khi gửi sang Shopee API.
    """
    link = clean_link(link)

    if link.startswith("//"):
        return "https:" + link

    if link.startswith("http://") or link.startswith("https://"):
        return link

    return "https://" + link


def clean_cookie(raw):
    """
    Làm sạch cookie Shopee.
    """
    if not raw:
        return ""

    return (
        raw.replace('"', "")
           .replace("'", "")
           .replace("\n", "")
           .replace("\r", "")
           .strip()
    )


def get_shopee_cookies():
    """
    Lấy danh sách cookie Shopee.
    Hiện tại chỉ dùng 1 cookie mặc định.
    """
    cookie = clean_cookie(SHOPEE_COOKIE)

    if cookie:
        return [
            {
                "label": "default",
                "cookie": cookie
            }
        ]

    return []


def parse_money_vnd(value):
    """
    Parse các dạng tiền như:
    "₫16.200", "16200", 16200, "16,200", None
    Trả về int VND.
    """
    try:
        if value is None:
            return 0

        if isinstance(value, (int, float)):
            return int(value)

        digits = re.sub(r'[^0-9]', '', str(value))
        return int(digits) if digits else 0

    except Exception:
        return 0


def parse_rate_fraction(value):
    """
    Parse tỷ lệ hoa hồng.
    Ví dụ:
    "9%" -> 0.09
    "2,5%" -> 0.025
    9000 -> 0.09
    2500 -> 0.025
    """
    try:
        if value is None:
            return 0.0

        if isinstance(value, (int, float)):
            v = float(value)

            if v == 0:
                return 0.0

            # Nếu Shopee trả dạng fraction sẵn, ví dụ 0.09
            if 0 < v < 1:
                return v

            # Shopee rate_detail thường là 9000 = 9%, 2500 = 2.5%
            if v > 100:
                return v / 100000.0

            return v / 100.0

        s = str(value).strip().replace(',', '.')
        m = re.search(r'[0-9]*\.?[0-9]+', s)

        if not m:
            return 0.0

        v = float(m.group())

        if v == 0:
            return 0.0

        if '%' in s:
            return v / 100.0

        if 0 < v < 1:
            return v

        if v > 100:
            return v / 100000.0

        return v / 100.0

    except Exception:
        return 0.0


def format_vnd(amount):
    """
    Format tiền VND kiểu Việt Nam:
    8100 -> ₫8.100
    """
    try:
        return '₫{:,.0f}'.format(float(amount)).replace(',', '.')
    except Exception:
        return '₫0'


# ============================================================
# SHOPEE CONVERT
# ============================================================
def convert_shopee_links_sync(links, cookies, sub_id=''):
    """
    Tạo link affiliate Shopee.
    Có gắn subId1 = username của người dùng.
    """
    replace_map = {}

    if not links or not cookies:
        return replace_map

    api_links = [normalize_link_for_api(link) for link in links]

    for account in cookies:
        link_params = []

        for link in api_links:
            lp = {
                "originalLink": link
            }

            if sub_id:
                lp["advancedLinkParams"] = {
                    "subId1": str(sub_id)
                }

            link_params.append(lp)

        payload = {
            "operationName": "batchGetCustomLink",
            "query": """
                query batchGetCustomLink($linkParams: [CustomLinkParam!], $sourceCaller: SourceCaller) {
                    batchCustomLink(linkParams: $linkParams, sourceCaller: $sourceCaller) {
                        shortLink
                        longLink
                        failCode
                    }
                }
            """,
            "variables": {
                "linkParams": link_params,
                "sourceCaller": "CUSTOM_LINK_CALLER"
            }
        }

        headers = {
            "content-type": "application/json",
            "cookie": account["cookie"],
            "user-agent": SHOPEE_UA,
            "referer": "https://affiliate.shopee.vn/",
            "origin": "https://affiliate.shopee.vn"
        }

        try:
            resp = requests.post(
                "https://affiliate.shopee.vn/api/v3/gql?q=batchCustomLink",
                headers=headers,
                json=payload,
                timeout=20
            )

            if resp.status_code != 200:
                print(f"[CONVERT] Shopee HTTP {resp.status_code}")
                continue

            data = resp.json()

            if not isinstance(data, dict):
                print("[CONVERT] Shopee response not JSON")
                continue

            batch = (data.get("data") or {}).get("batchCustomLink") or []

            for idx, item in enumerate(batch):
                if idx >= len(links):
                    continue

                fail_code = item.get("failCode")

                if fail_code not in (0, None):
                    print(f"[CONVERT] failCode={fail_code} link={links[idx]}")
                    continue

                short_link = item.get("shortLink")
                long_link = item.get("longLink")

                if short_link:
                    replace_map[links[idx]] = {
                        "short_link": short_link,
                        "long_link": long_link or short_link
                    }

            if replace_map:
                break

        except Exception as e:
            print(f"[CONVERT] Shopee error: {e}")

    return replace_map


# ============================================================
# HEALTH CHECK
# ============================================================
@app.route("/", methods=["GET"])
def health():
    return "Shopee Affiliate API is running!", 200


# ============================================================
# API TẠO LINK AFFILIATE
# ============================================================
@app.route("/api/convert", methods=["POST"])
def api_convert():
    auth_error = require_auth()
    if auth_error:
        return auth_error

    data = request.get_json(silent=True) or {}

    url = (data.get("url") or "").strip()
    sub_id = (data.get("sub_id") or "").strip()

    if not url:
        return jsonify({
            'success': False,
            'error': 'Missing url'
        }), 200

    cookies = get_shopee_cookies()

    if not cookies:
        return jsonify({
            'success': False,
            'error': 'No Shopee cookie configured'
        }), 200

    result = convert_shopee_links_sync([url], cookies, sub_id)
    mapped = result.get(url)

    if not mapped:
        return jsonify({
            'success': False,
            'error': 'Shopee failed to create link'
        }), 200

    short_link = mapped["short_link"]

    return jsonify({
        'success': True,
        'original_url': url,
        'sub_id': sub_id or None,
        'affiliate_url': short_link,
        'short_link': short_link
    })


# ============================================================
# API LẤY HOA HỒNG SẢN PHẨM
# ============================================================
@app.route("/api/commission", methods=["GET"])
def api_commission():
    auth_error = require_auth()
    if auth_error:
        return auth_error

    item_id = request.args.get('item_id')
    purl = request.args.get('url')

    # Nếu không có item_id thì thử trích từ URL
    if not item_id and purl:
        m = re.search(r'[?&]item_id=(\d+)', purl)
        if m:
            item_id = m.group(1)
        else:
            m = re.search(r'product\/(\d+)\/(\d+)', purl)
            if m:
                item_id = m.group(2)
            else:
                m = re.search(r'-i\.(\d+)\.(\d+)', purl)
                if m:
                    item_id = m.group(2)

    if not item_id:
        return jsonify({
            'success': False,
            'error': 'Missing item_id or url'
        }), 200

    cookie = clean_cookie(SHOPEE_COOKIE)

    if not cookie:
        return jsonify({
            'success': False,
            'error': 'No Shopee cookie configured'
        }), 200

    headers = {
        'accept': 'application/json, text/plain, */*',
        'content-type': 'application/json',
        'cookie': cookie,
        'user-agent': SHOPEE_UA,
        'referer': 'https://affiliate.shopee.vn/',
        'origin': 'https://affiliate.shopee.vn'
    }

    api_url = (
        "https://affiliate.shopee.vn/api/v3/offer/product"
        f"?item_id={urllib.parse.quote(str(item_id))}"
    )

    print(f"[COMMISSION] item_id={item_id} url={api_url}")

    try:
        resp = requests.get(api_url, headers=headers, timeout=20)
        print(f"[COMMISSION] Shopee HTTP {resp.status_code}")

        if resp.status_code != 200:
            return jsonify({
                'success': False,
                'error': f'Shopee HTTP {resp.status_code}',
                'raw': resp.text[:1000]
            }), 200

        try:
            data = resp.json()
        except Exception:
            return jsonify({
                'success': False,
                'error': 'Shopee response is not JSON',
                'raw': resp.text[:1000]
            }), 200

        if not isinstance(data, dict):
            return jsonify({
                'success': False,
                'error': 'Shopee response invalid format',
                'raw': resp.text[:1000]
            }), 200

        if data.get("code") != 0 or not data.get("data"):
            return jsonify({
                'success': False,
                'error': data.get('msg') or 'Shopee API error',
                'shopee_code': data.get('code')
            }), 200

        d = data.get("data") or {}
        cr = d.get("commission_rate") or {}
        crd = d.get("commission_rate_detail") or {}
        item_card = d.get("batch_item_for_item_card_full") or {}

        # ==========================================================
        # 1. Ưu tiên hoa hồng người bán
        # ==========================================================
        if cr.get("seller_commission") is not None:
            commission_raw = cr.get("seller_commission")
        elif cr.get("default_commission") is not None:
            commission_raw = cr.get("default_commission")
        else:
            commission_raw = d.get("commission") or 0

        commission_amount = parse_money_vnd(commission_raw)

        # ==========================================================
        # 2. Lấy giá sản phẩm
        # Shopee thường trả giá dạng micro: 18000000000 = 180.000 VND
        # ==========================================================
        price_raw = item_card.get("price") or item_card.get("price_min") or 0
        price_micro = parse_money_vnd(price_raw)
        price_num = price_micro / 100000.0 if price_micro else 0.0

        # ==========================================================
        # 3. Nếu thiếu số tiền hoa hồng thì tự tính từ rate + giá
        # ==========================================================
        if commission_amount <= 0 and price_num > 0:
            rate_raw = None

            if crd.get("seller_commission_rate") is not None:
                rate_raw = crd.get("seller_commission_rate")
            elif cr.get("seller_commission_rate") is not None:
                rate_raw = cr.get("seller_commission_rate")
            elif crd.get("default_commission_rate") is not None:
                rate_raw = crd.get("default_commission_rate")
            elif cr.get("default_commission_rate") is not None:
                rate_raw = cr.get("default_commission_rate")

            rate_fraction = parse_rate_fraction(rate_raw)

            cap_num = 0.0

            # commission_cap trong commission_rate_detail thường là micro VND
            if crd.get("commission_cap") is not None:
                cap_micro = parse_money_vnd(crd.get("commission_cap"))
                cap_num = cap_micro / 100000.0 if cap_micro else 0.0

            # commission_cap trong commission_rate có thể là chuỗi format sẵn: ₫40.000
            elif cr.get("commission_cap") is not None:
                cap_num = float(parse_money_vnd(cr.get("commission_cap")))

            estimated = price_num * rate_fraction

            if cap_num > 0:
                estimated = min(estimated, cap_num)

            commission_amount = int(round(estimated))

        # ==========================================================
        # 4. Chia 50% cho người dùng
        # ==========================================================
        cashback = commission_amount // 2

        # Tỷ lệ hoa hồng hiển thị
        rate_display = None

        if cr.get("seller_commission_rate") is not None:
            rate_display = cr.get("seller_commission_rate")
        elif crd.get("seller_commission_rate") is not None:
            rate_display = crd.get("seller_commission_rate")
        elif cr.get("default_commission_rate") is not None:
            rate_display = cr.get("default_commission_rate")
        elif crd.get("default_commission_rate") is not None:
            rate_display = crd.get("default_commission_rate")
        else:
            rate_display = "0%"

        # Nếu rate_display là số hoặc chuỗi số không có %, format lại thành %
        if not (isinstance(rate_display, str) and '%' in rate_display):
            percent = parse_rate_fraction(rate_display) * 100
            rate_display = '{:.2f}'.format(percent).replace('.', ',') + '%'

        return jsonify({
            'success': True,
            'item_id': str(item_id),
            'product_name': item_card.get("name") or "Sản phẩm Shopee",
            'image': item_card.get("image") or "",
            'price': format_vnd(price_num) if price_num > 0 else "",
            'seller_commission_rate': rate_display,
            'estimated_commission': format_vnd(commission_amount),
            'estimated_cashback': format_vnd(cashback),
            'cashback_percent': 50,
            'commission_amount': commission_amount,
            'cashback_amount': cashback
        })

    except Exception as e:
        print(f"[COMMISSION] Exception: {e}")

        return jsonify({
            'success': False,
            'error': str(e)
        }), 200


# ============================================================
# API ĐỒNG BỘ ĐƠN HÀNG
# ============================================================
@app.route("/api/orders", methods=["GET"])
def api_orders():
    auth_error = require_auth()
    if auth_error:
        return auth_error

    sub_id = request.args.get('sub_id')

    if not sub_id:
        return jsonify({
            'success': False,
            'error': 'Missing sub_id'
        }), 200

    start = request.args.get('start', '')
    end = request.args.get('end', '')
    page_num = request.args.get('page_num', '1')
    page_size = request.args.get('page_size', '20')

    params = {
        'page_size': page_size,
        'page_num': page_num,
        'sub_id': sub_id,
        'version': '1'
    }

    if start:
        params['purchase_time_s'] = start

    if end:
        params['purchase_time_e'] = end

    qs = urllib.parse.urlencode(params)

    headers = {
        'accept': 'application/json, text/plain, */*',
        'content-type': 'application/json',
        'cookie': clean_cookie(SHOPEE_COOKIE),
        'user-agent': SHOPEE_UA,
        'referer': 'https://affiliate.shopee.vn/',
        'origin': 'https://affiliate.shopee.vn'
    }

    api_url = f"https://affiliate.shopee.vn/api/v3/report/list?{qs}"

    print(f"[ORDERS] sub_id={sub_id} url={api_url}")

    try:
        resp = requests.get(api_url, headers=headers, timeout=20)
        print(f"[ORDERS] Shopee HTTP {resp.status_code}")

        if resp.status_code != 200:
            return jsonify({
                'success': False,
                'error': f'Shopee HTTP {resp.status_code}',
                'raw': resp.text[:1000]
            }), 200

        try:
            data = resp.json()
        except Exception:
            return jsonify({
                'success': False,
                'error': 'Shopee response is not JSON',
                'raw': resp.text[:1000]
            }), 200

        if not isinstance(data, dict):
            return jsonify({
                'success': False,
                'error': 'Shopee response invalid format',
                'raw': resp.text[:1000]
            }), 200

        if data.get("code") != 0:
            return jsonify({
                'success': False,
                'error': data.get('msg') or 'Shopee API error',
                'shopee_code': data.get('code')
            }), 200

        order_list = (data.get("data") or {}).get("list") or []
        orders = []

        for o in order_list:
            if not isinstance(o, dict):
                continue

            # Ưu tiên hoa hồng người bán nếu API trả về
            seller_commission = o.get("seller_commission")

            if seller_commission is not None:
                commission_raw = seller_commission
            else:
                commission_raw = o.get("commission") or "0"

            commission_num = parse_money_vnd(commission_raw)

            # User nhận 50%
            cashback = commission_num // 2

            orders.append({
                'order_sn': o.get("order_sn") or "",
                'item_id': str(o.get("item_id") or ""),
                'product_name': o.get("product_name") or "",
                'amount': o.get("amount") or "",
                'commission': seller_commission if seller_commission is not None else (o.get("commission") or "0"),
                'cashback': format_vnd(cashback),
                'status': o.get("status") or "pending",
                'purchase_time': str(o.get("purchase_time") or ""),
                'shop_name': o.get("shop_name") or ""
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
        print(f"[ORDERS] Exception: {e}")

        return jsonify({
            'success': False,
            'error': str(e)
        }), 200


# ============================================================
# RUN
# ============================================================
if __name__ == "__main__":
    app.run(debug=True)
