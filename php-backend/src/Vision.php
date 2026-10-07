<?php
declare(strict_types=1);

/**
 * Proxy to the dlib/opencv computer-vision API.
 *
 * The browser samples camera frames and POSTs them to the backend (same origin
 * as the page); we forward each to the CV Flask service and return predictions.
 * Proxying keeps a single origin, avoiding CORS + mixed-content issues.
 */
final class Vision
{
    private Config $cfg;

    public function __construct(Config $cfg)
    {
        $this->cfg = $cfg;
    }

    /** @return array<string,mixed> */
    public function health(): array
    {
        try {
            [$st, $body] = Http::request('GET', $this->cfg->cvApiUrl() . '/health', ['timeout' => 5]);
            if ($st >= 400) {
                throw new RuntimeException("status $st");
            }
            return ['available' => true, 'cv' => json_decode($body, true)];
        } catch (Throwable $e) {
            return ['available' => false, 'error' => $e->getMessage()];
        }
    }

    /**
     * Forward one camera frame to the CV service. Throws on transport error.
     * @return array<string,mixed>
     */
    public function analyze(string $frameData, ?string $sessionId): array
    {
        [$st, $body] = Http::request('POST', $this->cfg->cvApiUrl() . '/analyze_frame', [
            'json'    => ['frame_data' => $frameData, 'session_id' => $sessionId],
            'timeout' => 10,
        ]);
        if ($st >= 400) {
            throw new RuntimeException('CV service error: ' . substr($body, 0, 300));
        }
        return json_decode($body, true);
    }

    /**
     * Aggregated per-session analysis. Never throws (degrades gracefully).
     * @return array<string,mixed>
     */
    public function sessionAnalysis(string $sessionId): array
    {
        try {
            [$st, $body] = Http::request('GET', $this->cfg->cvApiUrl() . '/session_analysis/' . rawurlencode($sessionId), [
                'timeout' => 10,
            ]);
            if ($st >= 400) {
                throw new RuntimeException("status $st");
            }
            $j = json_decode($body, true);
            $analysis = is_array($j) ? ($j['session_analysis'] ?? null) : null;
            if (!$analysis || (is_array($analysis) && isset($analysis['error']))) {
                return ['available' => false, 'reason' => $analysis['error'] ?? 'no session data'];
            }
            return ['available' => true, 'analysis' => $analysis];
        } catch (Throwable $e) {
            return ['available' => false, 'reason' => $e->getMessage()];
        }
    }
}
