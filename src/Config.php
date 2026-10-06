<?php
declare(strict_types=1);

/**
 * Application configuration, loaded from the environment / .env file.
 *
 * Secrets (API keys, tokens) are read from the environment only and are never
 * written to source. See `.env.example` for the full list of settings.
 */
final class Config
{
    /** @var array<string,string> */
    private array $env = [];
    public string $root;

    public function __construct(string $root)
    {
        $this->root = $root;
        $envFile = $root . '/.env';
        // Docker injects the same values through env_file. The host file stays
        // mode 600, so the container user may not be able to open it.
        if (is_readable($envFile)) {
            foreach (file($envFile, FILE_IGNORE_NEW_LINES) ?: [] as $line) {
                $line = trim($line);
                if ($line === '' || $line[0] === '#') {
                    continue;
                }
                $pos = strpos($line, '=');
                if ($pos === false) {
                    continue;
                }
                $k = trim(substr($line, 0, $pos));
                $v = trim(substr($line, $pos + 1));
                // strip a single layer of surrounding quotes
                if (strlen($v) >= 2 && ($v[0] === '"' || $v[0] === "'") && substr($v, -1) === $v[0]) {
                    $v = substr($v, 1, -1);
                }
                $this->env[$k] = $v;
            }
        }
    }

    public function get(string $key, ?string $default = null): ?string
    {
        if (array_key_exists($key, $this->env)) {
            return $this->env[$key];
        }
        $v = getenv($key);
        return $v === false ? $default : $v;
    }

    /** @return string[] */
    private function csv(?string $value): array
    {
        if ($value === null || $value === '') {
            return [];
        }
        return array_values(array_filter(array_map('trim', explode(',', $value)), fn ($x) => $x !== ''));
    }

    // --- Dograh -----------------------------------------------------------
    public function dographBaseUrl(): string
    {
        // No default host: set DOGRAH_BASE_URL in .env per deployment.
        return rtrim((string) $this->get('DOGRAH_BASE_URL', ''), '/');
    }

    public function apiBase(): string
    {
        return $this->dographBaseUrl() . '/api/v1';
    }

    public function wsBase(): string
    {
        $override = $this->get('DOGRAH_WS_BASE_URL');
        if ($override) {
            return rtrim($override, '/');
        }
        $b = $this->dographBaseUrl();
        if (str_starts_with($b, 'https://')) {
            return 'wss://' . substr($b, strlen('https://'));
        }
        if (str_starts_with($b, 'http://')) {
            return 'ws://' . substr($b, strlen('http://'));
        }
        return $b;
    }

    public function signalingUrl(string $sessionToken): string
    {
        return $this->wsBase() . '/api/v1/ws/public/signaling/' . $sessionToken;
    }

    public function workflowId(): int
    {
        return (int) $this->get('DOGRAH_WORKFLOW_ID', '3');
    }

    public function workflowUuid(): ?string
    {
        return $this->get('DOGRAH_WORKFLOW_UUID');
    }

    public function apiKey(): ?string
    {
        $v = $this->get('DOGRAH_API_KEY');
        return $v !== null ? trim($v) : null;
    }

    public function embedToken(): ?string
    {
        $v = $this->get('DOGRAH_EMBED_TOKEN');
        return ($v !== null && $v !== '') ? trim($v) : null;
    }

    /** @return string[] */
    public function embedAllowedDomains(): array
    {
        return $this->csv($this->get('EMBED_ALLOWED_DOMAINS', ''));
    }

    // --- WebRTC ICE -------------------------------------------------------
    /** @return string[] */
    public function iceStunUrls(): array
    {
        $u = $this->csv($this->get('ICE_STUN_URLS', 'stun:stun.l.google.com:19302'));
        return $u ?: ['stun:stun.l.google.com:19302'];
    }

    public function meteredConfigured(): bool
    {
        return trim((string) $this->get('METERED_TURN_DOMAIN', '')) !== ''
            && trim((string) $this->get('METERED_TURN_API_KEY', '')) !== '';
    }

    /**
     * ICE servers for the browser peer connection.
     *
     * Metered is preferred: the API key stays on the server and the browser
     * receives only the short-lived username and credential Metered returns.
     * The optional local coturn path uses TURN REST HMAC-SHA1 (draft-uberti).
     * SHA-1 is required by that protocol and is not used for anything else.
     *
     * @return array<int,array<string,mixed>>
     */
    public function iceServers(): array
    {
        if ($this->meteredConfigured()) {
            return $this->meteredIceServers();
        }
        $servers = array_map(fn ($u) => ['urls' => $u], $this->iceStunUrls());
        $host = trim((string) $this->get('TURN_HOST', ''));
        $secret = (string) $this->get('TURN_SECRET', '');
        if ($host === '' || $secret === '') {
            return $servers;
        }
        $port = (int) ($this->get('TURN_PORT', '3478') ?: '3478');
        $username = (string) (time() + 86400) . ':interview';
        $password = base64_encode(hash_hmac('sha1', $username, $secret, true));
        $servers[] = [
            'urls' => [
                "turn:{$host}:{$port}",
                "turn:{$host}:{$port}?transport=tcp",
            ],
            'username' => $username,
            'credential' => $password,
        ];
        return $servers;
    }

    /** @return array<int,array<string,mixed>> */
    private function meteredIceServers(): array
    {
        $fallback = array_map(fn ($u) => ['urls' => $u], $this->iceStunUrls());
        $domain = trim((string) $this->get('METERED_TURN_DOMAIN', ''));
        $key = trim((string) $this->get('METERED_TURN_API_KEY', ''));
        $cacheFile = $this->root . '/data/metered-ice.json';
        if (is_readable($cacheFile)) {
            $cached = json_decode((string) file_get_contents($cacheFile), true);
            if (is_array($cached) && (int) ($cached['exp'] ?? 0) > time() && is_array($cached['servers'] ?? null)) {
                return $cached['servers'];
            }
        }
        $url = 'https://' . $domain . '/api/v1/turn/credentials?apiKey=' . rawurlencode($key);
        $ch = curl_init($url);
        if ($ch === false) {
            return $fallback;
        }
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => 8,
            CURLOPT_CONNECTTIMEOUT => 5,
        ]);
        $body = curl_exec($ch);
        $code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);
        if (!is_string($body) || $code !== 200) {
            error_log('Metered TURN credential request failed');
            return $fallback;
        }
        $decoded = json_decode($body, true);
        if (!is_array($decoded)) {
            return $fallback;
        }
        $servers = [];
        foreach ($decoded as $item) {
            if (!is_array($item) || !isset($item['urls'])) {
                continue;
            }
            $entry = ['urls' => $item['urls']];
            if (isset($item['username']) && $item['username'] !== '') {
                $entry['username'] = (string) $item['username'];
            }
            if (isset($item['credential']) && $item['credential'] !== '') {
                $entry['credential'] = (string) $item['credential'];
            }
            $servers[] = $entry;
        }
        if ($servers === []) {
            return $fallback;
        }
        $dir = dirname($cacheFile);
        if (is_dir($dir) && is_writable($dir)) {
            file_put_contents($cacheFile, json_encode(['exp' => time() + 300, 'servers' => $servers]));
        }
        return $servers;
    }

    // --- This backend -----------------------------------------------------
    /** @return string[] */
    public function corsOrigins(): array
    {
        $c = $this->csv($this->get('CORS_ORIGINS', '*'));
        return $c ?: ['*'];
    }

    // --- Computer vision --------------------------------------------------
    public function cvApiUrl(): string
    {
        return rtrim($this->get('CV_API_URL', 'http://127.0.0.1:5001'), '/');
    }

    // --- AI evaluation ----------------------------------------------------
    public function evalModel(): string
    {
        return trim((string) $this->get('EVAL_LLM_MODEL', ''));
    }

    public function evalApiKey(): ?string
    {
        $v = $this->get('EVAL_LLM_API_KEY');
        return ($v !== null && $v !== '') ? $v : null;
    }

    public function evalApiBase(): ?string
    {
        $v = $this->get('EVAL_LLM_API_BASE');
        return ($v !== null && $v !== '') ? rtrim($v, '/') : null;
    }

    // --- Report persistence ----------------------------------------------
    public function reportsDir(): string
    {
        $d = (string) $this->get('REPORTS_DIR', 'data/reports');
        // Resolve relative paths against the project root.
        if (!preg_match('#^(/|[A-Za-z]:[\\\\/])#', $d)) {
            $d = $this->root . '/' . $d;
        }
        return $d;
    }
}
