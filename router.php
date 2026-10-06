<?php
/**
 * Router for the PHP built-in web server:
 *
 *     php -S 0.0.0.0:8080 router.php
 *
 * Serves existing files under public/ directly (e.g. demo.html); everything
 * else is dispatched to the front controller.
 */

declare(strict_types=1);

$uri = urldecode(parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH) ?? '/');
$file = __DIR__ . '/public' . $uri;

// Let the built-in server serve real static files (but never directory listings).
if ($uri !== '/' && is_file($file)) {
    return false;
}

require __DIR__ . '/public/index.php';
