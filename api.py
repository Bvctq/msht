from flask import Flask, request, jsonify
import os
import json
import re
import time
import hmac
import hashlib
import urllib.parse
import urllib.request
import ssl
import requests

app = Flask(__name__)

# ========== CONFIG ==========
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "")

# --- Lazada ---
LAZADA_COOKIE = os.environ.get("LAZADA_COOKIE", "")
LAZADA_DASHBOARD_API = "https://adsense.lazada.vn/newOffer/link-convert-v2.json"
LAZADA_USER_TOKEN = os.environ.get("LAZADA_USER_TOKEN", "")
LAZADA_APP_KEY = os.environ.get("LAZADA_APP_KEY", "")
LAZADA_APP_SECRET = os.environ.get("LAZADA_APP_SECRET", "")
LAZADA_OPEN_API_URL = "https://api.lazada.sg/rest"

# --- Shopee ---
SHOPEE_COOKIE = os.environ.get("SHOPEE_COOKIE", "")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

LAZ_TRACKING_PARAMS = {
    'exlaz', 'laz_share_info', 'laz_token', 'trafficfrom',
    'laz_trackid', 'mkttid', 'spm', 'from_affiliate', 't',
    'dsource', 'data_prefetch', 'hybrid', 'at_iframe',
    'disable_bounces', 'lzd_navbar_hidden', 'pha',
    'disable_pull_refresh', 'prefetch_replace',
    'wx_navbar_transparent', 'c', 'clickid', 'sub_aff_id',
    'sub_id1', 'sub_id2', 'sub_id3', 'sub_id4', 'sub_id5', 'sub_id6',
    'epid', 'laz_prefetch_id', 'etype', 'eurl', 'eredirect',
    'zarsrc', 'utm_source', 'utm_medium', 'utm_campaign',
    'scm', 'lpid', 'nav_right_item_hidden', 'sbucket', 'k',
    '__wml_data_prefetch', 'web_view_type', 'e',
}

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
# HELPERS CHUNG
# ══════════════════════════════════════════════════════════════

def send_telegram_message(chat_id, text):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": False}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print(f"Telegram error: {e}")


def clean_link(link):
    return (link or "").strip().rstrip(TRAILING_PUNCT)


def normalize_link_for_api(link):
    link = clean_link(link)
    if link.startswith("//"):
        return "https:" + link
    if link.startswith("http://") or link.startswith("https://"):
        return link
    return "https://" + link


def is_lazada_host(url):
    try:
        parsed = urllib.parse.urlparse(url if url.startswith('http') else 'https://' + url)
        host = (parsed.hostname or '').lower()
        return 'lazada.' in host
    except:
        return False


def is_shopee_host(url):
    try:
        parsed = urllib.parse.urlparse(url if url.startswith('http') else 'https://' + url)
        host = (parsed.hostname or '').lower()
        return 'shopee.' in host or 'shp.ee' in host
    except:
        return False


# ══════════════════════════════════════════════════════════════
# SHOPEE CONVERT
# ══════════════════════════════════════════════════════════════

def clean_cookie(raw):
    return (raw or "").replace('"', "").replace("'", "").strip()


def get_shopee_cookies():
    cookie = clean_cookie(SHOPEE_COOKIE)
    if cookie:
        return [{"label": "default", "cookie": cookie}]
    return []


def convert_shopee_links_sync(links, cookies, sub_id=''):
    replace_map = {}
    if not links or not cookies:
        return replace_map

    api_links = [normalize_link_for_api(link) for link in links]

    for account in cookies:
        # Build linkParams with optional subId1
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
# LAZADA RESOLVE + CONVERT
# ══════════════════════════════════════════════════════════════

def find_js_redirect(body):
    body = body.decode('utf-8', errors='ignore') if isinstance(body, bytes) else body

    patterns = [
        r"window\.location\.href\s*=\s*['\"]([^'\"]+)['\"]",
        r"window\.location\.replace\(['\"]([^'\"]+)['\"]\)",
        r"window\.location\s*=\s*['\"]([^'\"]+)['\"]",
        r"location\.href\s*=\s*['\"]([^'\"]+)['\"]",
        r'<meta[^>]+refresh[^>]+url=([^"\s>]+)',
        r'data-redirect=[\'\"]([^\'\"]+)[\'\"]',
    ]
    for p in patterns:
        m = re.search(p, body, re.I)
        if m:
            return m.group(1).replace("&amp;", "&")
    return None


def fetch_with_requests(url, max_redirects=5):
    headers = {
        'User-Agent': USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,vi;q=0.8',
    }
    visited = set()
    current = url

    for _ in range(max_redirects):
        if current in visited:
            break
        visited.add(current)

        try:
            resp = requests.get(current, headers=headers, timeout=15, allow_redirects=False)
        except Exception as e:
            print(f"requests error: {e}")
            return None

        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get('Location', '')
            if loc:
                current = urllib.parse.urljoin(current, loc)
                continue

        js_url = find_js_redirect(resp.text)
        if js_url:
            current = urllib.parse.urljoin(current, js_url)
            continue

        return current

    return current


def extract_url_param(url, param='url'):
    try:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        if param in qs:
            return urllib.parse.unquote(qs[param][0])
    except Exception as e:
        print(f"Extract param error: {e}")
    return None


def resolve_to_destination(raw_url):
    url = raw_url.strip()
    if not url.startswith('http'):
        url = 'https://' + url

    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or '').lower()

    if not re.match(r'^(s\.|c\.)?lazada\.', host):
        return url

    if host.startswith('c.lazada.'):
        for param in ['url', 't']:
            dest = extract_url_param(url, param)
            if dest:
                if not dest.startswith('http'):
                    dest = urllib.parse.unquote(dest)
                if dest.startswith('http'):
                    return dest
        final = fetch_with_requests(url)
        if final:
            for param in ['url', 't']:
                dest = extract_url_param(final, param)
                if dest and dest.startswith('http'):
                    return urllib.parse.unquote(dest)
            return final
        return url

    if host.startswith('s.lazada.'):
        final = fetch_with_requests(url)
        if not final:
            return None
        final_host = (urllib.parse.urlparse(final).hostname or '').lower()
        if final_host.startswith('c.lazada.'):
            for param in ['url', 't']:
                dest = extract_url_param(final, param)
                if dest and dest.startswith('http'):
                    return urllib.parse.unquote(dest)
        if re.match(r'^(www\.)?lazada\.', final_host):
            return final
        return final

    return url


def clean_tracking_params(url):
    try:
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        clean_query = {k: v for k, v in query.items() if k.lower() not in LAZ_TRACKING_PARAMS}
        if not clean_query:
            return urllib.parse.urlunparse(parsed._replace(query=''))
        new_query = urllib.parse.urlencode(clean_query, doseq=True)
        return urllib.parse.urlunparse(parsed._replace(query=new_query))
    except Exception as e:
        print(f"Clean error: {e}")
        return url


def simplify_lazada_url(url):
    try:
        parsed = urllib.parse.urlparse(url)
        path = re.sub(r'/products/pdp-i', '/i', parsed.path)
        path = re.sub(r'/products/i', '/i', path)
        return urllib.parse.urlunparse(parsed._replace(path=path))
    except:
        return url


def clean_output_url(url):
    if not url:
        return url
    return url.split('?')[0]


def sign_lazada_request(params, app_secret):
    sorted_params = sorted(params.items())
    concatenated = "".join([f"{k}{v}" for k, v in sorted_params])
    string_to_sign = app_secret + concatenated + app_secret
    return hmac.new(
        app_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256
    ).hexdigest().upper()


def convert_via_dashboard(jump_url):
    if not LAZADA_COOKIE:
        return None, "Thiếu LAZADA_COOKIE"

    payload = json.dumps({
        "jumpUrl": jump_url,
        "subIdTemplateKey": ""
    }).encode("utf-8")

    req = urllib.request.Request(
        LAZADA_DASHBOARD_API,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Cookie": LAZADA_COOKIE,
            "Referer": "https://adsense.lazada.vn/",
            "Origin": "https://adsense.lazada.vn",
            "User-Agent": USER_AGENT
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("success") and data.get("resultCode") == 1:
                return data.get("data"), None
            return None, data.get("message", "Dashboard API error")
    except Exception as e:
        return None, str(e)


def get_lazada_affiliate_link(input_value, input_type="url"):
    if not LAZADA_USER_TOKEN or not LAZADA_APP_KEY or not LAZADA_APP_SECRET:
        return None, "Thiếu Open API config"

    params = {
        "app_key": LAZADA_APP_KEY,
        "timestamp": str(int(time.time() * 1000)),
        "sign_method": "sha256",
        "userToken": LAZADA_USER_TOKEN,
        "inputType": input_type,
        "inputValue": input_value,
    }
    params["sign"] = sign_lazada_request(params, LAZADA_APP_SECRET)
    query = urllib.parse.urlencode(params)
    url = f"{LAZADA_OPEN_API_URL}/marketing/getlink?{query}"

    try:
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except Exception as e:
        return None, str(e)


def convert_single_lazada(target_url):
    if LAZADA_COOKIE:
        data, error = convert_via_dashboard(target_url)
        if data and data.get("shortLink"):
            return clean_output_url(data["shortLink"])

    data, error = get_lazada_affiliate_link(target_url, "url")
    if not error and data:
        result = data.get("data", {})
        items = result.get("urlBatchGetLinkInfoList", [])
        if items:
            link = items[0].get("regularPromotionLink", "")
            if link:
                return clean_output_url(link)
    return None


# ══════════════════════════════════════════════════════════════
# API ENDPOINTS FOR PHP
# ══════════════════════════════════════════════════════════════

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

    # Truyền sub_id để Shopee tự gắn vào utm_content qua advancedLinkParams.subId1
    result = convert_shopee_links_sync([url], cookies, sub_id)
    mapped = result.get(url)

    if not mapped:
        return jsonify({'error': 'Shopee failed to create link'}), 500

    short_link = mapped["short_link"]
    # Không gắn ?sub_id= thủ công nữa — Shopee đã nhúng qua subId1

    return jsonify({
        'success': True,
        'original_url': url,
        'sub_id': sub_id or None,
        'affiliate_url': short_link,   # Trả về short link làm chính
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
        
        # ===== LOGIC 50/50: LẤY HOA HỒNG NGƯỜI BÁN =====
        seller_comm_str = d.get("commission_rate", {}).get("seller_commission", d.get("commission", "0"))
        rate_str = d.get("commission_rate", {}).get("seller_commission_rate", "0%")
        
        comm_num = int(re.sub(r"[^\d]", "", seller_comm_str)) if seller_comm_str else 0
        cashback = comm_num // 2  # User nhận 50%

        product_info = d.get("batch_item_for_item_card_full", {})
        
        # Giá Shopee trả về dạng *100000
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


# ══════════════════════════════════════════════════════════════
# TELEGRAM WEBHOOK
# ══════════════════════════════════════════════════════════════

def extract_all_urls_with_positions(text):
    results = []
    occupied = set()

    pattern = re.compile(
        r'https?://[^\s<>"{}|\\^`\[\]]+',
        re.IGNORECASE
    )

    for m in pattern.finditer(text):
        url = m.group()
        s, e = m.start(), m.end()
        if s in occupied:
            continue
        if is_lazada_host(url):
            results.append((s, e, url, 'lazada'))
            for i in range(s, e):
                occupied.add(i)
        elif is_shopee_host(url):
            results.append((s, e, url, 'shopee'))
            for i in range(s, e):
                occupied.add(i)

    pattern_laz = re.compile(
        r'(?<!\w)(?:s\.|c\.|www\.)?lazada\.(?:vn|sg|co\.th|co\.id|com\.ph|com\.my)(?:[/\w\-\.?=&%@:;+]*)?',
        re.IGNORECASE
    )
    for m in pattern_laz.finditer(text):
        s, e = m.start(), m.end()
        overlap = any(i in occupied for i in range(s, e))
        if not overlap:
            url = 'https://' + m.group()
            results.append((s, e, url, 'lazada'))
            for i in range(s, e):
                occupied.add(i)

    pattern_shp = re.compile(
        r'(?<!\w)(?:s\.|vn\.)?sh(?:opee\.vn|p\.ee|opee\.ee)/[^\s<>"{}|\\^`\[\]]*',
        re.IGNORECASE
    )
    for m in pattern_shp.finditer(text):
        s, e = m.start(), m.end()
        overlap = any(i in occupied for i in range(s, e))
        if not overlap:
            url = 'https://' + m.group()
            results.append((s, e, url, 'shopee'))
            for i in range(s, e):
                occupied.add(i)

    return sorted(results, key=lambda x: x[0])


def process_text_with_links(text, chat_id):
    urls_info = extract_all_urls_with_positions(text)
    if not urls_info:
        send_telegram_message(chat_id, "❌ Không tìm thấy link Lazada hoặc Shopee hợp lệ.")
        return

    shopee_links = []
    lazada_links = []
    for s, e, url, typ in urls_info:
        if typ == 'shopee':
            shopee_links.append(url)
        else:
            lazada_links.append(url)

    shopee_map = {}
    if shopee_links:
        cookies = get_shopee_cookies()
        if cookies:
            shopee_map = convert_shopee_links_sync(shopee_links, cookies)
        else:
            print("Shopee links found but no cookie configured")

    lazada_map = {}
    for url in lazada_links:
        resolved = resolve_to_destination(url)
        if resolved:
            prepared = simplify_lazada_url(clean_tracking_params(resolved))
            result = convert_single_lazada(prepared)
            if result:
                lazada_map[url] = result

    if len(urls_info) == 1:
        raw_url = urls_info[0][2]
        typ = urls_info[0][3]
        clean_input = text.strip()
        raw_in_text = text[urls_info[0][0]:urls_info[0][1]]

        if clean_input == raw_in_text or clean_input == raw_url or clean_input == raw_url.replace('https://', ''):
            if typ == 'shopee' and raw_url in shopee_map:
                send_telegram_message(chat_id, shopee_map[raw_url]["short_link"])
                return
            elif typ == 'lazada' and raw_url in lazada_map:
                send_telegram_message(chat_id, lazada_map[raw_url])
                return
            else:
                send_telegram_message(chat_id, "❌ Không thể chuyển đổi link.")
                return

    send_telegram_message(chat_id, "⏳ Đang chuyển đổi các link...")
    parts = []
    last_end = 0

    for start, end, raw_url, typ in urls_info:
        parts.append(text[last_end:start])

        if typ == 'shopee':
            if raw_url in shopee_map:
                parts.append(shopee_map[raw_url]["short_link"])
            else:
                parts.append("❌ lỗi")
        else:
            if raw_url in lazada_map:
                parts.append(lazada_map[raw_url])
            else:
                parts.append("❌ lỗi")

        last_end = end

    parts.append(text[last_end:])
    send_telegram_message(chat_id, ''.join(parts))


def process_message(update):
    message = update.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if not chat_id or not text:
        return

    if text.startswith("/start"):
        welcome = (
            "👋 Chào mừng!\n\n"
            "Gửi link Shopee hoặc Lazada, bot sẽ chuyển thành link affiliate! 🚀\n\n"
            "Hỗ trợ:\n"
            "• Shopee: shopee.vn, s.shopee.vn, shp.ee\n"
            "• Lazada: link dài, s.lazada.vn, c.lazada.vn\n"
            "• Text kèm nhiều link\n"
            "• Cả khi thiếu https://"
        )
        send_telegram_message(chat_id, welcome)
        return

    if text.startswith("/help"):
        help_text = (
            "📖 Hướng dẫn:\n\n"
            "Gửi link Shopee/Lazada → Bot tự động:\n"
            "1️⃣ Nhận diện loại link\n"
            "2️⃣ Mở link rút gọn (cả JS redirect)\n"
            "3️⃣ Xóa tracking params\n"
            "4️⃣ Chuyển đổi sang affiliate\n\n"
            "Ví dụ:\n"
            "• https://shopee.vn/...\n"
            "• https://s.lazada.vn/s.nCcIw\n"
            "• s.lazada.vn/l.ZCTQZ\n"
            "• ---> Tên SP | https://s.shopee.vn/..."
        )
        send_telegram_message(chat_id, help_text)
        return

    process_text_with_links(text, chat_id)


@app.route("/", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        process_message(update)
    except Exception as e:
        print(f"Webhook error: {e}")
    return "OK", 200


@app.route("/", methods=["GET"])
def health():
    return "Lazada + Shopee Affiliate Bot is running!", 200


if __name__ == "__main__":
    app.run(debug=True)
