<?php
declare(strict_types=1);

/**
 * Tiny cURL wrapper. Synchronous (PHP is request-scoped), which is fine for the
 * handful of control-plane calls this backend makes per request.
 */
final class Http
{
    /**
     * @param array{headers?:array<string,string>,json?:mixed,timeout?:int,connect_timeout?:int} $opts
     * @return array{0:int,1:string} [status, body]
     */
    public static function request(string $method, string $url, array $opts = []): array
    {
        $ch = curl_init();
        $headerLines = [];
        foreach ($opts['headers'] ?? [] as $k => $v) {
            $headerLines[] = $k . ': ' . $v;
        }

        $payload = null;
        if (array_key_exists('json', $opts)) {
            $payload = json_encode($opts['json'], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
            $headerLines[] = 'Content-Type: application/json';
        }

        curl_setopt_array($ch, [
            CURLOPT_URL            => $url,
            CURLOPT_CUSTOMREQUEST  => strtoupper($method),
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_HTTPHEADER     => $headerLines,
            CURLOPT_TIMEOUT        => $opts['timeout'] ?? 30,
            CURLOPT_CONNECTTIMEOUT => $opts['connect_timeout'] ?? 10,
        ]);
        if ($payload !== null) {
            curl_setopt($ch, CURLOPT_POSTFIELDS, $payload);
        }

        $body = curl_exec($ch);
        if ($body === false) {
            $err = curl_error($ch);
            curl_close($ch);
            throw new RuntimeException('HTTP request failed: ' . $err);
        }
        $status = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
        curl_close($ch);

        return [$status, (string) $body];
    }
}
