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

    public function settingsFile(): string
    {
        return $this->root . '/data/interview-settings.json';
    }

    /** @return array<string,mixed> */
    public function interviewSettings(): array
    {
        $file = $this->settingsFile();
        if (!is_readable($file)) {
            return [];
        }
        $decoded = json_decode((string) file_get_contents($file), true);
        return is_array($decoded) ? $decoded : [];
    }

    public function workflowOverridden(): bool
    {
        return (int) ($this->interviewSettings()['workflow_id'] ?? 0) > 0;
    }

    public function workflowId(): int
    {
        $chosen = (int) ($this->interviewSettings()['workflow_id'] ?? 0);
        if ($chosen > 0) {
            return $chosen;
        }
        return (int) $this->get('DOGRAH_WORKFLOW_ID', '3');
    }

    public function workflowUuid(): ?string
    {
        $settings = $this->interviewSettings();
        if ($this->workflowOverridden()) {
            $uuid = trim((string) ($settings['workflow_uuid'] ?? ''));
            return $uuid !== '' ? $uuid : null;
        }
        return $this->get('DOGRAH_WORKFLOW_UUID');
    }

    public function workflowName(): string
    {
        return trim((string) ($this->interviewSettings()['workflow_name'] ?? ''));
    }

    /**
     * Remember which Dograh workflow new interviews should start.
     * The Dograh API key stays in the environment and is never written here.
     */
    public function saveWorkflow(int $id, string $name, ?string $uuid): void
    {
        $this->writeSettings([
            'workflow_id'   => $id,
            'workflow_name' => $name,
            'workflow_uuid' => $uuid ?: null,
        ]);
    }

    public function meteredDomain(): string
    {
        $chosen = trim((string) ($this->interviewSettings()['metered_domain'] ?? ''));
        if ($chosen !== '') {
            return $chosen;
        }
        return trim((string) $this->get('METERED_TURN_DOMAIN', ''));
    }

    public function meteredApiKey(): string
    {
        $chosen = trim((string) ($this->interviewSettings()['metered_api_key'] ?? ''));
        if ($chosen !== '') {
            return $chosen;
        }
        return trim((string) $this->get('METERED_TURN_API_KEY', ''));
    }

    public function meteredKeyHint(): string
    {
        $key = $this->meteredApiKey();
        if ($key === '') {
            return '';
        }
        return '••••' . substr($key, -4);
    }

    /**
     * Store Metered TURN settings. A blank API key keeps the key already on the server.
     * The full key is never returned to the browser.
     */
    public function saveMetered(string $domain, ?string $apiKey): void
    {
        $domain = strtolower(trim($domain));
        $domain = preg_replace('#^https?://#', '', $domain) ?? $domain;
        $domain = rtrim($domain, '/');
        if (!preg_match('/^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$/', $domain)) {
            throw new InvalidArgumentException('Enter the Metered domain, such as yourapp.metered.live.');
        }
        $patch = ['metered_domain' => $domain];
        if ($apiKey !== null && trim($apiKey) !== '') {
            $patch['metered_api_key'] = trim($apiKey);
        }
        $this->writeSettings($patch);
        $cache = $this->root . '/data/metered-ice.json';
        if (is_file($cache)) {
            @unlink($cache);
        }
    }

    /** @param array<string,mixed> $patch */
    private function writeSettings(array $patch): void
    {
        $dir = $this->root . '/data';
        if (!is_dir($dir)) {
            @mkdir($dir, 0770, true);
        }
        $payload = array_merge($this->interviewSettings(), $patch);
        $payload['updated_at'] = (new DateTime('now', new DateTimeZone('UTC')))->format('Y-m-d\TH:i:s\Z');
        $tmp = $dir . '/.interview-settings.tmp';
        file_put_contents($tmp, json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES));
        rename($tmp, $this->settingsFile());
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
        return $this->meteredDomain() !== '' && $this->meteredApiKey() !== '';
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
        $domain = $this->meteredDomain();
        $key = $this->meteredApiKey();
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
