<?php
/**
 * Cheklar relay: ichki server (bot) tayyor cheklarni shu yerga yuboradi, 1C shu yerdan oladi.
 *
 * Kalitlar (X-API-Key sarlavhasi):
 *   server_key - faqat ichki server: /api/relay/push, /api/relay/events
 *   onec_key   - faqat 1C: /api/receipts/claim, /ack, /fail, /reject, ro'yxat
 */
declare(strict_types=1);

// PHP 7.4+ bilan mos (xostingdagi PHP 7.4 uchun).

function relay_now(): string
{
    return date('Y-m-d H:i:s');
}

function relay_connect(array $cfg): PDO
{
    $db = new PDO($cfg['dsn'], $cfg['db_user'] ?? null, $cfg['db_pass'] ?? null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);
    relay_ensure_schema($db);
    return $db;
}

function relay_ensure_schema(PDO $db): void
{
    $mysql = $db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql';
    $pk = $mysql ? 'INT AUTO_INCREMENT PRIMARY KEY' : 'INTEGER PRIMARY KEY AUTOINCREMENT';
    $text = $mysql ? 'MEDIUMTEXT' : 'TEXT';
    $tail = $mysql ? ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4' : '';
    $db->exec("CREATE TABLE IF NOT EXISTS receipts (
        id {$pk},
        source_id INT NOT NULL,
        dedup_key VARCHAR(100) NULL UNIQUE,
        status VARCHAR(20) NOT NULL,
        check_number VARCHAR(50) NOT NULL,
        fm VARCHAR(50) NOT NULL,
        fp VARCHAR(50) NOT NULL,
        check_date VARCHAR(25) NOT NULL,
        url TEXT NOT NULL,
        seller_name VARCHAR(255) NULL,
        seller_inn VARCHAR(20) NULL,
        address VARCHAR(255) NULL,
        total DECIMAL(15,2) NULL,
        vat_total DECIMAL(15,2) NULL,
        items_json {$text} NOT NULL,
        last_error TEXT NULL,
        onec_document VARCHAR(255) NULL,
        claimed_at VARCHAR(25) NULL,
        created_at VARCHAR(25) NOT NULL,
        updated_at VARCHAR(25) NOT NULL
    ){$tail}");
    $db->exec("CREATE TABLE IF NOT EXISTS events (
        id {$pk},
        receipt_id INT NOT NULL,
        source_id INT NOT NULL,
        type VARCHAR(20) NOT NULL,
        document VARCHAR(255) NULL,
        error TEXT NULL,
        created_at VARCHAR(25) NOT NULL
    ){$tail}");
}

function relay_public(array $row): array
{
    return [
        'id' => (int)$row['id'],
        'status' => $row['status'],
        'url' => $row['url'],
        'fm' => $row['fm'],
        'check_number' => $row['check_number'],
        'check_date' => $row['check_date'],
        'fp' => $row['fp'],
        'seller_name' => $row['seller_name'],
        'seller_inn' => $row['seller_inn'],
        'address' => $row['address'],
        'total' => $row['total'] === null ? null : (float)$row['total'],
        'vat_total' => $row['vat_total'] === null ? null : (float)$row['vat_total'],
        'last_error' => $row['last_error'],
        'onec_document' => $row['onec_document'],
        'created_at' => $row['created_at'],
        'updated_at' => $row['updated_at'],
        'items' => json_decode($row['items_json'], true) ?: [],
    ];
}

function relay_get(PDO $db, int $id): ?array
{
    $st = $db->prepare('SELECT * FROM receipts WHERE id = ?');
    $st->execute([$id]);
    $row = $st->fetch();
    return $row ?: null;
}

function relay_event(PDO $db, array $row, string $type, ?string $document = null, ?string $error = null): void
{
    $db->prepare('INSERT INTO events(receipt_id, source_id, type, document, error, created_at) VALUES(?, ?, ?, ?, ?, ?)')
        ->execute([$row['id'], $row['source_id'], $type, $document, $error, relay_now()]);
}

function relay_update(PDO $db, int $id, array $fields): void
{
    $fields['updated_at'] = relay_now();
    $cols = implode(', ', array_map(fn($k) => "$k = ?", array_keys($fields)));
    $db->prepare("UPDATE receipts SET $cols WHERE id = ?")->execute([...array_values($fields), $id]);
}

/**
 * So'rovni qayta ishlaydi va [HTTP kod, javob massivi] qaytaradi.
 */
function relay_handle(PDO $db, array $cfg, string $method, string $path, array $query, string $body, string $apiKey): array
{
    $path = '/' . trim($path, '/');
    $input = $body !== '' ? json_decode($body, true) : [];
    if (!is_array($input)) {
        return [400, ['detail' => "JSON noto'g'ri"]];
    }

    if ($path === '/health') {
        return [200, ['ok' => true]];
    }

    $isServer = $apiKey !== '' && hash_equals((string)$cfg['server_key'], $apiKey);
    $isOnec = $apiKey !== '' && hash_equals((string)$cfg['onec_key'], $apiKey);

    // ---------------------------------------------------------------- ichki server
    if (strpos($path, '/api/relay/') === 0) {
        if (!$isServer) {
            return [401, ['detail' => "API kalit noto'g'ri"]];
        }
        if ($method === 'POST' && $path === '/api/relay/push') {
            return relay_push($db, $input);
        }
        if ($method === 'GET' && $path === '/api/relay/events') {
            $after = (int)($query['after'] ?? 0);
            $limit = max(1, min(500, (int)($query['limit'] ?? 100)));
            $st = $db->prepare("SELECT e.*, r.check_number FROM events e JOIN receipts r ON r.id = e.receipt_id
                WHERE e.id > ? ORDER BY e.id LIMIT $limit");
            $st->execute([$after]);
            $events = array_map(fn($e) => [
                'id' => (int)$e['id'], 'receipt_id' => (int)$e['receipt_id'],
                'source_id' => (int)$e['source_id'], 'check_number' => $e['check_number'],
                'type' => $e['type'],
                'document' => $e['document'], 'error' => $e['error'], 'created_at' => $e['created_at'],
            ], $st->fetchAll());
            return [200, ['events' => $events]];
        }
        return [404, ['detail' => 'Topilmadi']];
    }

    // ---------------------------------------------------------------- 1C
    if (strpos($path, '/api/receipts') !== 0) {
        return [404, ['detail' => 'Topilmadi']];
    }
    if (!$isOnec) {
        return [401, ['detail' => "API kalit noto'g'ri"]];
    }

    if ($method === 'POST' && $path === '/api/receipts/claim') {
        $limit = max(1, min(500, (int)($query['limit'] ?? 50)));
        return [200, ['receipts' => relay_claim($db, $limit, (int)($cfg['lease_minutes'] ?? 15))]];
    }
    if ($method === 'GET' && $path === '/api/receipts') {
        $limit = max(1, min(1000, (int)($query['limit'] ?? 100)));
        if (!empty($query['status'])) {
            $st = $db->prepare("SELECT * FROM receipts WHERE status = ? ORDER BY id LIMIT $limit");
            $st->execute([$query['status']]);
        } else {
            $st = $db->query("SELECT * FROM receipts ORDER BY id DESC LIMIT $limit");
        }
        $rows = array_map(function ($r) {
            $p = relay_public($r);
            unset($p['items']);
            return $p;
        }, $st->fetchAll());
        return [200, ['receipts' => $rows]];
    }

    if (!preg_match('#^/api/receipts/(\d+)(?:/(ack|fail|reject))?$#', $path, $m)) {
        return [404, ['detail' => 'Topilmadi']];
    }
    $row = relay_get($db, (int)$m[1]);
    if (!$row) {
        return [404, ['detail' => 'Chek topilmadi']];
    }
    $action = $m[2] ?? '';

    if ($method === 'GET' && $action === '') {
        return [200, relay_public($row)];
    }
    if ($method !== 'POST' || $action === '') {
        return [405, ['detail' => 'Metod ruxsat etilmagan']];
    }

    if ($action === 'ack') {
        $document = trim((string)($input['document'] ?? ''));
        if ($document === '') {
            return [422, ['detail' => 'document maydoni kerak']];
        }
        if ($row['status'] === 'imported') {
            return [200, ['ok' => true, 'already' => true]];
        }
        relay_update($db, (int)$row['id'], ['status' => 'imported', 'onec_document' => $document, 'last_error' => null]);
        relay_event($db, $row, 'imported', $document);
        return [200, ['ok' => true]];
    }

    $error = trim((string)($input['error'] ?? ''));
    if ($error === '') {
        return [422, ['detail' => 'error maydoni kerak']];
    }
    if ($action === 'fail') {
        // 1C hujjat yarata olmadi - chek navbatga qaytadi.
        relay_update($db, (int)$row['id'], ['status' => 'ready', 'last_error' => $error, 'claimed_at' => null]);
        relay_event($db, $row, 'failed', null, $error);
        return [200, ['ok' => true]];
    }
    // reject: dublikat kaliti bo'shatiladi - chekni qayta yuborish mumkin.
    relay_update($db, (int)$row['id'], ['status' => 'rejected', 'dedup_key' => null, 'last_error' => $error]);
    relay_event($db, $row, 'rejected', null, $error);
    return [200, ['ok' => true]];
}

function relay_push(PDO $db, array $r): array
{
    foreach (['source_id', 'dedup_key', 'check_number', 'fm', 'fp', 'check_date', 'url'] as $key) {
        if (!isset($r[$key]) || $r[$key] === '') {
            return [422, ['detail' => "Maydon yo'q: $key"]];
        }
    }
    // Idempotentlik chek raqami (dedup_key) bo'yicha: shu chekni shu server qayta yuborsa - mavjudini
    // qaytaramiz; boshqa manbadan (yoki ichki baza qayta yaratilgandan keyin) kelsa - dublikat.
    $st = $db->prepare('SELECT * FROM receipts WHERE dedup_key = ?');
    $st->execute([(string)$r['dedup_key']]);
    if ($existing = $st->fetch()) {
        if ((int)$existing['source_id'] === (int)$r['source_id']
            && $existing['check_number'] === (string)$r['check_number']) {
            return [200, ['id' => (int)$existing['id'], 'existing' => true]];
        }
        return [409, ['detail' => 'duplicate', 'existing_id' => (int)$existing['id'],
            'existing_status' => $existing['status']]];
    }

    $now = relay_now();
    try {
        $db->prepare('INSERT INTO receipts(source_id, dedup_key, status, check_number, fm, fp, check_date, url,
                seller_name, seller_inn, address, total, vat_total, items_json, created_at, updated_at)
                VALUES(?, ?, \'ready\', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)')
            ->execute([
                (int)$r['source_id'], (string)$r['dedup_key'], (string)$r['check_number'], (string)$r['fm'],
                (string)$r['fp'], (string)$r['check_date'], (string)$r['url'],
                $r['seller_name'] ?? null, $r['seller_inn'] ?? null, $r['address'] ?? null,
                $r['total'] ?? null, $r['vat_total'] ?? null,
                json_encode($r['items'] ?? [], JSON_UNESCAPED_UNICODE), $now, $now,
            ]);
    } catch (PDOException $e) {
        if ($e->getCode() === '23000') {
            $st = $db->prepare('SELECT id, status FROM receipts WHERE dedup_key = ?');
            $st->execute([(string)$r['dedup_key']]);
            $dup = $st->fetch() ?: [];
            return [409, ['detail' => 'duplicate', 'existing_id' => (int)($dup['id'] ?? 0),
                'existing_status' => $dup['status'] ?? null]];
        }
        throw $e;
    }
    return [200, ['id' => (int)$db->lastInsertId(), 'existing' => false]];
}

/**
 * Tayyor cheklarni 1C ga beradi va 'processing' qiladi. Bir vaqtda ikki joydan so'ralsa ham
 * bitta chek faqat bittasiga tushadi. Tasdiqlanmaganlari lease_minutes dan keyin qaytadi.
 */
function relay_claim(PDO $db, int $limit, int $leaseMinutes): array
{
    $mysql = $db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql';
    $now = relay_now();
    $expired = date('Y-m-d H:i:s', time() - $leaseMinutes * 60);

    $db->beginTransaction();
    try {
        $db->prepare("UPDATE receipts SET status = 'ready', updated_at = ? WHERE status = 'processing' AND claimed_at < ?")
            ->execute([$now, $expired]);
        $lock = $mysql ? ' FOR UPDATE' : '';
        $rows = $db->query("SELECT * FROM receipts WHERE status = 'ready' ORDER BY id LIMIT $limit$lock")->fetchAll();
        $upd = $db->prepare("UPDATE receipts SET status = 'processing', claimed_at = ?, updated_at = ? WHERE id = ?");
        foreach ($rows as &$row) {
            $upd->execute([$now, $now, $row['id']]);
            $row['status'] = 'processing';
        }
        unset($row);
        $db->commit();
    } catch (Throwable $e) {
        $db->rollBack();
        throw $e;
    }
    return array_map('relay_public', $rows);
}
