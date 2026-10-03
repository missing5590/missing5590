<?php
declare(strict_types=1);

// Kod va sozlamalar public_html dan tashqarida turadi - brauzer orqali ochilmaydi.
require __DIR__ . '/../app.php';
$cfg = require getenv('RELAY_CONFIG') ?: __DIR__ . '/../config.php';
date_default_timezone_set($cfg['timezone'] ?? 'Asia/Tashkent');

header('Content-Type: application/json; charset=utf-8');

$path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';
$apiKey = $_SERVER['HTTP_X_API_KEY'] ?? '';

try {
    $db = relay_connect($cfg);
    [$status, $payload] = relay_handle(
        $db, $cfg, $_SERVER['REQUEST_METHOD'] ?? 'GET', $path, $_GET,
        (string)file_get_contents('php://input'), $apiKey
    );
} catch (Throwable $e) {
    error_log('relay: ' . $e);
    [$status, $payload] = [500, ['detail' => 'Server xatosi']];
}

http_response_code($status);
echo json_encode($payload, JSON_UNESCAPED_UNICODE);
