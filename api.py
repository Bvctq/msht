<?php
require_once 'config.php';
requireLogin();

/**
 * Resolve link rút gọn s.shopee.vn / shp.ee → link đích
 */
function resolveShopeeUrl($url) {
    if (strpos($url, 's.shopee.vn') === false && strpos($url, 'shp.ee') === false) {
        return $url;
    }
    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_FOLLOWLOCATION, true);
    curl_setopt($ch, CURLOPT_TIMEOUT, 15);
    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
    curl_setopt($ch, CURLOPT_SSL_VERIFYHOST, false);
    curl_setopt($ch, CURLOPT_NOBODY, true); // chỉ cần header
    curl_setopt($ch, CURLOPT_USERAGENT, 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15');
    curl_exec($ch);
    $resolved = curl_getinfo($ch, CURLINFO_EFFECTIVE_URL);
    curl_close($ch);
    
    if ($resolved && filter_var($resolved, FILTER_VALIDATE_URL) && $resolved !== $url) {
        return $resolved;
    }
    return $url;
}

$result = null;
$commission = null;
$error = '';
$debug_error = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $url = trim($_POST['url'] ?? '');

    if (empty($url) || !filter_var($url, FILTER_VALIDATE_URL)) {
        $error = 'Vui lòng nhập link Shopee hợp lệ';
    } else {
        // 1. Tạo link affiliate (dùng URL gốc, Shopee affiliate tự xử lý)
        $apiResult = callVercelAPI('/api/convert', 'POST', [
            'url' => $url,
            'sub_id' => $_SESSION['username']
        ]);

        if (isset($apiResult['error'])) {
            $error = 'Lỗi API: ' . $apiResult['error'];
            error_log('[SaleVN] Convert error: ' . json_encode($apiResult));
        } elseif (!empty($apiResult['success'])) {
            $result = $apiResult;

            // 2. Resolve link rút gọn → link đích để lấy item_id
            $resolvedUrl = resolveShopeeUrl($url);
            error_log('[SaleVN] Resolved: ' . $resolvedUrl);

            // 3. Extract item_id từ URL đÃ resolve
            $itemId = null;
            if (preg_match('/[?&]item_id=(\d+)/', $resolvedUrl, $m)) {
                $itemId = $m[1];
            } elseif (preg_match('/product\/(\d+)\/(\d+)/', $resolvedUrl, $m)) {
                $itemId = $m[2];
            } elseif (preg_match('/-i\.(\d+)\.(\d+)/', $resolvedUrl, $m)) {
                $itemId = $m[2];
            }

            // 4. Gọi API lấy thông tin hoa hồng (qua addlivetag.com)
            if ($itemId) {
                $commResult = callVercelAPI(
                    '/api/commission?item_id=' . urlencode($itemId) . '&url=' . urlencode($resolvedUrl),
                    'GET'
                );

                if (isset($commResult['error'])) {
                    $debug_error = 'Commission: ' . $commResult['error'];
                    error_log('[SaleVN] ' . $debug_error . ' | ItemID=' . $itemId);
                } elseif (!empty($commResult['success'])) {
                    $commission = $commResult;
                }
            } else {
                $debug_error = 'Không trích xuất được item_id từ link rút gọn';
                error_log('[SaleVN] ' . $debug_error . ' | Resolved=' . $resolvedUrl);
            }

            // 5. Lưu DB
            if ($conn) {
                $stmt = $conn->prepare("INSERT INTO converted_links 
                    (user_id, original_url, affiliate_url, short_link, sub_id, item_id) 
                    VALUES (?, ?, ?, ?, ?, ?)");
                $stmt->bind_param('isssss', 
                    $_SESSION['user_id'], 
                    $url, 
                    $result['affiliate_url'], 
                    $result['short_link'], 
                    $_SESSION['username'],
                    $itemId
                );
                $stmt->execute();
            }
        } else {
            $error = 'Không thể tạo link affiliate';
            error_log('[SaleVN] Convert failed: ' . json_encode($apiResult));
        }
    }
}
?>
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Chuyển đổi link - SaleVN</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',Tahoma,sans-serif}
body{background:#f5f5f5;min-height:100vh;padding:20px}
.container{max-width:680px;margin:0 auto}
.header{background:#ee4d2d;color:#fff;padding:20px;border-radius:12px 12px 0 0;text-align:center}
.header h1{font-size:22px}
.header a{color:#fff;text-decoration:none;font-size:14px;opacity:.9;margin:0 8px}
.box{background:#fff;padding:30px;border-radius:0 0 12px 12px;box-shadow:0 4px 20px rgba(0,0,0,.08);margin-bottom:20px}
.form-group{margin-bottom:20px}
label{display:block;margin-bottom:8px;font-weight:600;color:#333;font-size:14px}
input[type="url"]{width:100%;padding:14px;border:2px solid #ddd;border-radius:10px;font-size:15px}
input:focus{outline:none;border-color:#ee4d2d}
button[type="submit"]{padding:14px 28px;background:#ee4d2d;color:#fff;border:none;border-radius:10px;font-size:16px;font-weight:600;cursor:pointer}
button[type="submit"]:hover{background:#d73211}
.msg{padding:14px;border-radius:10px;margin-bottom:20px;font-size:14px}
.error{background:#ffe5e5;color:#c00}
.product-card{background:#f8f9fa;border-radius:12px;overflow:hidden;margin-top:20px;border:1px solid #e9ecef}
.product-top{display:flex;gap:16px;padding:20px;align-items:flex-start;background:#fff}
.product-top img{width:110px;height:110px;object-fit:cover;border-radius:10px;border:1px solid #eee;flex-shrink:0}
.product-meta{flex:1}
.product-name{font-size:17px;font-weight:700;color:#333;line-height:1.4;margin-bottom:6px}
.product-price{font-size:20px;font-weight:800;color:#ee4d2d}
.comm-rate{font-size:13px;color:#28a745;font-weight:600;margin-top:6px}
.comm-rate span{background:#e5f5e5;padding:3px 10px;border-radius:4px}
.cashback-bar{background:linear-gradient(90deg,#28a745,#34ce57);color:#fff;padding:16px 20px;display:flex;align-items:center;justify-content:space-between}
.cashback-bar .left{display:flex;align-items:center;gap:10px;font-size:15px;font-weight:600}
.cashback-bar .left svg{width:22px;height:22px}
.cashback-bar .right{font-size:24px;font-weight:800}
.comm-breakdown{padding:14px 20px;background:#fff;border-top:1px dashed #e9ecef;font-size:13px;color:#555}
.comm-breakdown .row{display:flex;justify-content:space-between;margin-bottom:6px}
.comm-breakdown .row:last-child{margin-bottom:0;padding-top:6px;border-top:1px solid #f0f0f0}
.comm-breakdown .val{color:#333;font-weight:600}
.link-section{padding:20px;background:#fff}
.link-section label{font-size:13px;color:#666;font-weight:600;margin-bottom:6px;display:block}
.link-box{background:#f8f9fa;padding:12px 14px;border-radius:8px;border:1px solid #e9ecef;word-break:break-all;font-size:14px;color:#333;margin-bottom:10px}
.copy-btn{background:#333;color:#fff;padding:10px 18px;border:none;border-radius:8px;cursor:pointer;font-size:13px;font-weight:600}
.copy-btn:hover{background:#555}
.tips{margin-top:24px;padding:16px;background:#f0f8ff;border-radius:10px;font-size:14px;color:#444}
.tips strong{color:#ee4d2d}
.debug-info{color:#999;font-size:12px;margin-top:8px;text-align:center}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>🛒 Chuyển đổi link Shopee</h1>
<div style="margin-top:8px">
<a href="index.php">← Về trang chủ</a>
<a href="dashboard.php">📊 Dashboard</a>
<a href="logout.php">Đăng xuất</a>
</div>
</div>

<div class="box">
<?php if ($error): ?><div class="msg error"><?php echo htmlspecialchars($error); ?></div><?php endif; ?>

<form method="POST" action="">
<div class="form-group">
<label>🔗 Dán link Shopee vào đây</label>
<input type="url" name="url" placeholder="https://shopee.vn/product/... hoặc https://s.shopee.vn/..." required>
</div>
<button type="submit">⚡ Tạo link hoàn tiền</button>
</form>

<?php if ($result): ?>

<?php if ($commission): ?>
<div class="product-card">
    <div class="product-top">
        <?php if (!empty($commission['image'])): ?>
        <img src="<?php echo htmlspecialchars($commission['image']); ?>" alt="" onerror="this.src='https://placehold.co/110x110?text=Shopee'">
        <?php else: ?>
        <img src="https://placehold.co/110x110?text=Shopee" alt="">
        <?php endif; ?>
        <div class="product-meta">
            <div class="product-name"><?php echo htmlspecialchars($commission['product_name'] ?? 'Sản phẩm Shopee'); ?></div>
            <?php if (!empty($commission['price']) && $commission['price'] !== '₫0'): ?>
            <div class="product-price"><?php echo htmlspecialchars($commission['price']); ?></div>
            <?php endif; ?>
            <?php if (!empty($commission['seller_commission_rate'])): ?>
            <div class="comm-rate">
                <span>💰 Hoa hồng người bán: <?php echo htmlspecialchars($commission['seller_commission_rate']); ?></span>
            </div>
            <?php endif; ?>
        </div>
    </div>
    
    <div class="cashback-bar">
        <div class="left">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
            Bạn nhận lại (50%):
        </div>
        <div class="right"><?php echo htmlspecialchars($commission['estimated_cashback'] ?? '₫0'); ?></div>
    </div>
    
    <div class="comm-breakdown">
        <div class="row">
            <span>Tổng hoa hồng người bán:</span>
            <span class="val"><?php echo htmlspecialchars($commission['seller_commission'] ?? '—'); ?></span>
        </div>
        <div class="row">
            <span>Hệ thống giữ (50%):</span>
            <span class="val"><?php echo htmlspecialchars($commission['platform_fee'] ?? '—'); ?></span>
        </div>
        <div class="row">
            <span style="color:#28a745;font-weight:700">🎁 Bạn nhận (50%):</span>
            <span class="val" style="color:#28a745;font-weight:700"><?php echo htmlspecialchars($commission['user_cashback'] ?? '—'); ?></span>
        </div>
    </div>
</div>
<?php elseif ($debug_error): ?>
<div class="debug-info">⚠️ <?php echo htmlspecialchars($debug_error); ?></div>
<?php endif; ?>

<div class="link-section" style="margin-top:20px">
    <label>📋 Link chia sẻ (rút gọn)</label>
    <div class="link-box" id="shortLink"><?php echo htmlspecialchars($result['short_link']); ?></div>
    <button class="copy-btn" onclick="copyText('shortLink')">📋 Copy link</button>
</div>

<?php endif; ?>

<div class="tips">
<strong>💡 Hướng dẫn:</strong><br>
1. Dán link sản phẩm Shopee (link đầy đủ hoặc link rút gọn)<br>
2. Nhấn "Tạo link hoàn tiền"<br>
3. Copy link và chia sẻ cho bạn bè<br>
4. Khi có người mua qua link, bạn nhận <strong>50% hoa hồng người bán</strong>!
</div>
</div>
</div>

<script>
function copyText(id) {
    const text = document.getElementById(id).innerText;
    navigator.clipboard.writeText(text).then(() => { alert('Đã copy link!'); });
}
</script>
</body>
</html>
