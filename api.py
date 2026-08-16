import os
import json
import re
import urllib.parse
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ========== CONFIG ==========
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "")
SHOPEE_COOKIE = os.environ.get("SHOPEE_COOKIE", "")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

TRAILING_PUNCT = '.,;:!?)]}\'"'


# ══════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════

def require_auth():
    key = request.headers.get('x-api-key', '')
    if key != INTERNAL_API_KEY:
        return jsonify({'error': 'Unauthorized'}), 401
    return None


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def clean_link(link):
    return (link or "").strip().rstrip(TRAILING_PUNCT)


def normalize_link_for_api(link):
    link = clean_link(link)
    if link.startswith("//"):
        return "https:" + link
    if link.startswith("http://") or link.startswith("https://"):
        return link
    return "https://" + link


def clean_cookie(raw):
    return (raw or "").replace('"', "").replace("'", "").strip()


def get_shopee_cookies():
    cookie = clean_cookie(SHOPEE_COOKIE)
    if cookie:
        return [{"label": "default", "cookie": cookie}]
    return []


def unshorten_shopee_url(url):
    if not url:
        return url
    if "s.shopee.vn" in url or "shp.ee" in url or "shopee.ee" in url:
        try:
            resp = requests.head(
                url,
                allow_redirects=True,
                timeout=10,
                headers={"User-Agent": USER_AGENT},
            )
            return resp.url
        except Exception as e:
            print(f"Unshorten Shopee error: {e}")
    return url


# ══════════════════════════════════════════════════════════════
# SHOPEE CONVERT LOGIC
# ══════════════════════════════════════════════════════════════

def convert_shopee_links_sync(links, cookies, sub_id=''):
    replace_map = {}
    if not links or not cookies:
        return replace_map

    api_links = [normalize_link_for_api(link) for link in links]

    for account in cookies:
        link_params = [{"originalLink": link} for link in api_links]
        if sub_id:
            for lp in link_params:
                lp["advancedLinkParams"] = {"subId1": str(sub_id)}

        payload = {
            "operationName": "batchGetCustomLink",
            "query": (
                "query batchGetCustomLink($linkParams: [CustomLinkParam!], "
                "$sourceCaller: SourceCaller) { "
                "batchCustomLink(linkParams: $linkParams, sourceCaller: $sourceCaller) "
                "{ shortLink longLink failCode } }"
            ),
            "variables": {
                "linkParams": link_params,
                "sourceCaller": "CUSTOM_LINK_CALLER",
            },
        }
        headers = {
            "content-type": "application/json",
            "cookie": account["cookie"],
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
            data = resp.json()
            batch = data.get("data", {}).get("batchCustomLink", [])
            for idx, item in enumerate(batch):
                if idx >= len(links):
                    continue
                short_link = item.get("shortLink")
                long_link = item.get("longLink")
                if short_link:
                    replace_map[links[idx]] = {
                        "short_link": short_link,
                        "long_link": long_link or short_link
                    }

        except Exception as e:
            print(f"Convert Shopee error: {e}")

    return replace_map


# ══════════════════════════════════════════════════════════════
# API ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def health():
    return "Shopee Affiliate API is running!", 200


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

    cookies = get_shopee_cookies()
    if not cookies:
        return jsonify({'error': 'No Shopee cookie configured'}), 500

    result = convert_shopee_links_sync([url], cookies, sub_id)
    mapped = result.get(url)

    if not mapped:
        return jsonify({'error': 'Shopee failed to create link'}), 500

    short_link = mapped["short_link"]

    return jsonify({
        'success': True,
        'original_url': url,
        'sub_id': sub_id or None,
        'affiliate_url': short_link,
        'short_link': short_link
    })


@app.route("/api/commission", methods=["GET"])
def api_commission():
    auth_error = require_auth()
    if auth_error:
        return auth_error

    item_id = request.args.get('item_id')
    purl = request.args.get('url')

    if not item_id and purl:
        purl = unshorten_shopee_url(purl)
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

    headers = {
        "content-type": "application/json",
        "cookie": clean_cookie(SHOPEE_COOKIE),
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
            return jsonify({'error': 'Shopee API error', 'detail': data}), 500

        d = data["data"]

        seller_comm_str = d.get("commission_rate", {}).get("seller_commission", d.get("commission", "0"))
        rate_str = d.get("commission_rate", {}).get("seller_commission_rate", "0%")

        comm_num = int(re.sub(r"[^\d]", "", seller_comm_str)) if seller_comm_str else 0
        cashback = comm_num // 2

        product_info = d.get("batch_item_for_item_card_full", {})

        price_raw = product_info.get("price", "0")
        try:
            price_num = int(price_raw) / 100000
            price_str = f"₫{price_num:,.0f}".replace(",", ".")
        except:
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

    headers = {
        "content-type": "application/json",
        "cookie": clean_cookie(SHOPEE_COOKIE),
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

        order_list = data.get("data", {}).get("list") or []
        orders = []
        for o in order_list:
            comm_str = o.get("commission", "0")
            comm_num = int(re.sub(r"[^\d]", "", comm_str)) if comm_str else 0
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
            'page_num': data.get("data", {}).get("page_num", 1),
            'page_size': data.get("data", {}).get("page_size", 20),
            'total_count': data.get("data", {}).get("total_count", 0),
            'orders': orders
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
