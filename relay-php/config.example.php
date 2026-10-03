<?php
// Nusxa oling: config.php (public_html bilan bir papkada, uning ICHIDA EMAS).
return [
    // DirectAdmin → Ma'lumotlar bazalari bo'limida yaratilgan baza
    'dsn' => 'mysql:host=localhost;dbname=USER_cheklar;charset=utf8mb4',
    'db_user' => 'USER_cheklar',
    'db_pass' => 'PAROL',

    // Ichki server (bot) kaliti - serverdagi .env dagi RELAY_KEY bilan bir xil
    'server_key' => 'UZUN-TASODIFIY-KALIT-1',
    // 1C kaliti - 1C qayta ishlashdagi «Ключ API» maydoniga yoziladi
    'onec_key' => 'UZUN-TASODIFIY-KALIT-2',

    // 1C olgan, lekin tasdiqlamagan cheklar necha daqiqadan keyin qayta beriladi
    'lease_minutes' => 15,
    'timezone' => 'Asia/Tashkent',
];
