<?php
/**
 * Front controller for the PHP mock-interview backend.
 *
 * Mirrors the Python/FastAPI API:
 *   POST /api/interviews                       create a Dograh voice session
 *   GET  /api/interviews/{id}                  status + transcript
 *   GET  /api/interviews/{id}/messages         per-turn conversation
 *   POST /api/interviews/{id}/end              end + finalize
 *   GET  /api/interviews/{id}/report[?eval_model=]  full report (+persist)
 *   POST /api/webhooks/dograh                  optional end-of-call webhook
 *   GET  /api/vision/health                    CV service health
 *   POST /api/vision/analyze                   analyze one camera frame
 *   GET  /api/vision/session/{id}              aggregated CV analysis
 *   GET  /api/interviews/running               how many interviews are in progress
 *   GET  /config, /healthz                     runtime config / health
 *   GET  /                                     the demo page
 */

declare(strict_types=1);

$root = dirname(__DIR__);
require $root . '/src/Config.php';
require $root . '/src/Http.php';
require $root . '/src/DograhClient.php';
require $root . '/src/Store.php';
require $root . '/src/Transcript.php';
require $root . '/src/LlmClient.php';
require $root . '/src/Report.php';
require $root . '/src/Vision.php';

$cfg = new Config($root);

// ------------------------------------------------------------------ helpers
function json_response($data, int $status = 200): never
{
    http_response_code($status);
    header('Content-Type: application/json');
    echo json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    exit;
}

/** @return array<string,mixed> */
function read_json_body(): array
{
    $raw = file_get_contents('php://input');
    if (!$raw) {
        return [];
    }
    $j = json_decode($raw, true);
    return is_array($j) ? $j : [];
}

/**
 * @param array<string,mixed> $session
 * @return array<string,mixed>
 */
function refresh_results(array $session, DograhClient $dograh): array
{
    try {
        $savedWorkflow = (int) ($session['workflow_id'] ?? 0);
        $run = $dograh->getRun((int) $session['workflow_run_id'], $savedWorkflow > 0 ? $savedWorkflow : null);
    } catch (Throwable $e) {
        return $session;
    }

    $session['gathered_context'] = $run['gathered_context'] ?? null;
    $session['recording_url'] = $run['recording_url'] ?? ($run['recording_public_url'] ?? null);
    if (!empty($run['is_completed'])) {
        $session['status'] = 'completed';
        if (empty($session['ended_at'])) {
            $session['ended_at'] = Store::nowIso();
        }
    }
    $messages = Transcript::messagesFromRun($run);
    if ($messages) {
        $session['messages'] = $messages;
        $session['transcript'] = Transcript::transcriptText($messages);
    }
    return $session;
}

/**
 * Keep only the latest camera frame for an admin to watch.
 * This does not join the voice call.
 *
 * @param array<string,mixed> $analysis
 */
function remember_live_frame(string $root, string $sessionId, string $frameB64, array $analysis): void
{
    $id = preg_replace('/[^a-f0-9]/', '', strtolower($sessionId)) ?? '';
    if (strlen($id) < 16) {
        return;
    }
    $dir = $root . '/data/live';
    if (!is_dir($dir)) {
        @mkdir($dir, 0770, true);
    }
    $raw = base64_decode($frameB64, true);
    if (!is_string($raw) || $raw === '') {
        return;
    }
    file_put_contents($dir . '/' . $id . '.jpg', $raw);
    $body = is_array($analysis['analysis'] ?? null) ? $analysis['analysis'] : $analysis;
    $gaze = '';
    if (is_array($body['gaze_analysis_per_face'][0] ?? null)) {
        $gaze = (string) ($body['gaze_analysis_per_face'][0]['gaze_description'] ?? $body['gaze_analysis_per_face'][0]['gaze_direction'] ?? '');
    }
    file_put_contents($dir . '/' . $id . '.json', json_encode([
        'faces' => $body['faces_detected'] ?? null,
        'gaze'  => $gaze,
        'at'    => gmdate('c'),
    ]));
}

function count_live_interviews(Store $store, DograhClient $dograh): int
{
    $running = 0;
    foreach ($store->openSessions() as $session) {
        $fresh = refresh_results($session, $dograh);
        if (($fresh['status'] ?? '') === 'completed') {
            $store->update($fresh);
            continue;
        }
        $running++;
    }
    return $running;
}

/** @param array<string,mixed> $session @return array<string,mixed> */
function admin_interview_view(array $session): array
{
    $transcript = trim((string) ($session['transcript'] ?? ''));
    if ($transcript === '' && is_array($session['messages'] ?? null)) {
        $lines = [];
        foreach ($session['messages'] as $message) {
            if (!is_array($message)) {
                continue;
            }
            $who = ($message['role'] ?? '') === 'user' ? 'Candidate' : 'Interviewer';
            $lines[] = $who . ': ' . (string) ($message['text'] ?? '');
        }
        $transcript = trim(implode("\n", $lines));
    }
    $live = ($session['status'] ?? '') !== 'completed' && empty($session['ended_at']);
    return [
        'interview_id'    => $session['id'] ?? '',
        'candidate_name'  => $session['candidate_name'] ?? '',
        'role'            => $session['role'] ?? '',
        'status'          => $live ? 'running' : (string) ($session['status'] ?? ''),
        'created_at'      => $session['created_at'] ?? null,
        'ended_at'        => $session['ended_at'] ?? null,
        'workflow_run_id' => $session['workflow_run_id'] ?? null,
        'transcript'      => $transcript,
        'live'            => $live,
        'warning_count'   => 0,
    ];
}

function iso_unix_ms(mixed $value): ?int
{
    if (!is_string($value) || trim($value) === '') {
        return null;
    }
    try {
        $dt = new DateTime($value);
    } catch (Throwable $e) {
        return null;
    }
    return ((int) $dt->format('U')) * 1000 + (int) $dt->format('v');
}

function recording_object_url(int $runId, string $which): ?string
{
    if ($runId <= 0) {
        return null;
    }
    $key = match ($which) {
        'user' => 'recordings/' . $runId . '/user.wav',
        'bot' => 'recordings/' . $runId . '/bot.wav',
        'mixed' => 'recordings/' . $runId . '.wav',
        default => null,
    };
    if ($key === null) {
        return null;
    }
    return 'http://minio:9000/voice-audio/' . $key;
}

function recording_duration_ms(int $runId): ?int
{
    $url = recording_object_url($runId, 'mixed');
    if ($url === null) {
        return null;
    }
    $ch = curl_init($url);
    if ($ch === false) {
        return null;
    }
    $fileSize = 0;
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 8,
        CURLOPT_CONNECTTIMEOUT => 3,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_HTTPHEADER => ['Range: bytes=0-127'],
        CURLOPT_HEADERFUNCTION => static function ($ch, string $header) use (&$fileSize): int {
            if (preg_match('#^Content-Range:\s*bytes\s+\d+-\d+/(\d+)#i', trim($header), $m)) {
                $fileSize = (int) $m[1];
            }
            return strlen($header);
        },
    ]);
    $header = curl_exec($ch);
    curl_close($ch);
    if (!is_string($header) || !str_starts_with($header, 'RIFF') || $fileSize < 44) {
        return null;
    }
    $fmt = strpos($header, 'fmt ');
    if ($fmt === false || $fmt + 20 > strlen($header)) {
        return null;
    }
    $byteRate = unpack('V', substr($header, $fmt + 16, 4));
    $rate = is_array($byteRate) ? (int) ($byteRate[1] ?? 0) : 0;
    if ($rate < 1000) {
        return null;
    }
    return (int) round(max(0, $fileSize - 44) / $rate * 1000);
}

/**
 * Map recording time 0 onto the interview clock.
 * The file usually ends just after the last spoken line.
 *
 * @param array<int,array<string,mixed>> $rows
 */
function audio_origin_ms(?int $durationMs, array $rows): int
{
    if ($durationMs === null || $durationMs < 500) {
        return 0;
    }
    $speech = [];
    foreach ($rows as $row) {
        if (!in_array($row['lane'] ?? '', ['candidate', 'interviewer'], true)) {
            continue;
        }
        if (($row['elapsed_ms'] ?? null) === null) {
            continue;
        }
        $speech[] = (int) $row['elapsed_ms'];
    }
    if ($speech === []) {
        return 0;
    }
    $first = min($speech);
    $last = max($speech);
    $start = $last + 2000 - $durationMs;
    if ($start < 0) {
        $start = 0;
    }
    if ($start > $first) {
        $start = max(0, $first - 800);
    }
    return (int) $start;
}

function recording_available(int $runId, string $which): bool
{
    $url = recording_object_url($runId, $which);
    if ($url === null) {
        return false;
    }
    $ch = curl_init($url);
    if ($ch === false) {
        return false;
    }
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 8,
        CURLOPT_CONNECTTIMEOUT => 3,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_HTTPHEADER => ['Range: bytes=0-3'],
    ]);
    $body = curl_exec($ch);
    $code = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    return is_string($body) && in_array($code, [200, 206], true) && str_starts_with($body, 'RIFF');
}

function proxy_recording(int $runId, string $which): never
{
    $url = recording_object_url($runId, $which);
    if ($url === null) {
        json_response(['detail' => 'No recording was saved for this call.'], 404);
    }
    $ch = curl_init($url);
    if ($ch === false) {
        json_response(['detail' => 'No recording was saved for this call.'], 404);
    }
    $status = 0;
    $pass = [];
    $sent = false;
    $headers = [];
    if (isset($_SERVER['HTTP_RANGE']) && is_string($_SERVER['HTTP_RANGE'])) {
        $headers[] = 'Range: ' . $_SERVER['HTTP_RANGE'];
    }
    curl_setopt_array($ch, [
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_RETURNTRANSFER => false,
        CURLOPT_TIMEOUT => 120,
        CURLOPT_CONNECTTIMEOUT => 5,
        CURLOPT_HTTPHEADER => $headers,
        CURLOPT_HEADERFUNCTION => static function ($ch, string $header) use (&$status, &$pass): int {
            if (preg_match('#^HTTP/\S+\s+(\d+)#', $header, $m)) {
                $status = (int) $m[1];
            }
            if (preg_match('#^(Content-Length|Content-Range|Accept-Ranges):\s*(.+)$#i', trim($header), $m)) {
                $pass[strtolower($m[1])] = trim($m[2]);
            }
            return strlen($header);
        },
        CURLOPT_WRITEFUNCTION => static function ($ch, string $data) use (&$status, &$pass, &$sent): int {
            if ($status !== 200 && $status !== 206) {
                return strlen($data);
            }
            if (!$sent) {
                http_response_code($status);
                header('Content-Type: audio/wav');
                header('Cache-Control: private, max-age=3600');
                header('Accept-Ranges: bytes');
                if (isset($pass['content-length'])) {
                    header('Content-Length: ' . $pass['content-length']);
                }
                if (isset($pass['content-range'])) {
                    header('Content-Range: ' . $pass['content-range']);
                }
                $sent = true;
            }
            echo $data;
            return strlen($data);
        },
    ]);
    curl_exec($ch);
    curl_close($ch);
    if (!$sent) {
        json_response(['detail' => 'No recording was saved for this call.'], 404);
    }
    exit;
}

/**
 * @param array<string,mixed> $session
 * @param array<int,array<string,mixed>> $events
 * @param array<int,array<string,mixed>> $messages
 * @return array<int,array<string,mixed>>
 */
function build_timeline(array $session, array $events, array $messages, string $replayDir = ''): array
{
    $createdMs = iso_unix_ms($session['created_at'] ?? null);
    $rows = [];
    $seq = 0;
    foreach ($messages as $message) {
        if (!is_array($message)) {
            continue;
        }
        $text = trim((string) ($message['text'] ?? ''));
        if ($text === '') {
            continue;
        }
        $atMs = iso_unix_ms($message['timestamp'] ?? null);
        $elapsed = ($createdMs !== null && $atMs !== null) ? max(0, $atMs - $createdMs) : null;
        $rows[] = [
            'seq' => $seq++,
            'elapsed_ms' => $elapsed,
            'at' => is_string($message['timestamp'] ?? null) ? $message['timestamp'] : null,
            'lane' => ($message['role'] ?? '') === 'user' ? 'candidate' : 'interviewer',
            'text' => $text,
        ];
    }
    foreach ($events as $eventIndex => $event) {
        if (!is_array($event)) {
            continue;
        }
        $kind = (string) ($event['kind'] ?? '');
        $lane = match ($kind) {
            'warning', 'leave' => 'warning',
            'camera' => 'camera',
            'agent' => 'agent',
            default => 'system',
        };
        $elapsed = isset($event['elapsed_ms']) ? (int) $event['elapsed_ms'] : null;
        $frame = ($replayDir !== '' && is_file($replayDir . '/' . $eventIndex . '.jpg')) ? $eventIndex : null;
        $rows[] = [
            'seq' => $seq++,
            'elapsed_ms' => $elapsed,
            'at' => $event['at'] ?? null,
            'lane' => $lane,
            'state' => $event['state'] ?? null,
            'text' => (string) ($event['message'] ?? ''),
            'frame' => $frame,
        ];
    }
    usort($rows, static function (array $a, array $b): int {
        $ae = $a['elapsed_ms'];
        $be = $b['elapsed_ms'];
        if ($ae !== null && $be !== null && $ae !== $be) {
            return $ae <=> $be;
        }
        if ($ae !== null && $be === null) {
            return -1;
        }
        if ($ae === null && $be !== null) {
            return 1;
        }
        return $a['seq'] <=> $b['seq'];
    });
    return $rows;
}

// --------------------------------------------------------------------- CORS
$origins = $cfg->corsOrigins();
$origin = $_SERVER['HTTP_ORIGIN'] ?? '';
if (in_array('*', $origins, true)) {
    header('Access-Control-Allow-Origin: *');
} elseif ($origin !== '' && in_array($origin, $origins, true)) {
    header('Access-Control-Allow-Origin: ' . $origin);
    header('Vary: Origin');
}
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type, X-API-Key');
if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'OPTIONS') {
    http_response_code(204);
    exit;
}

// ------------------------------------------------------------------ routing
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
$path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?? '/';
$path = rtrim($path, '/');
if ($path === '') {
    $path = '/';
}

// Static demo page.
if ($method === 'GET' && ($path === '/' || $path === '/index.html')) {
    header('Content-Type: text/html; charset=utf-8');
    readfile($root . '/public/demo.html');
    exit;
}

if ($method === 'GET' && $path === '/healthz') {
    json_response([
        'status'           => 'ok',
        'dograh_base_url'  => $cfg->dographBaseUrl(),
        'workflow_id'      => $cfg->workflowId(),
    ]);
}

if ($method === 'GET' && $path === '/config') {
    $ice = $cfg->iceServers();
    $turnOn = false;
    foreach ($ice as $server) {
        $urls = $server['urls'] ?? [];
        foreach (is_array($urls) ? $urls : [$urls] as $url) {
            if (str_starts_with((string) $url, 'turn')) {
                $turnOn = true;
            }
        }
    }
    json_response([
        'dograh_base_url'        => $cfg->dographBaseUrl(),
        'ws_base'                => $cfg->wsBase(),
        'workflow_id'            => $cfg->workflowId(),
        'workflow_uuid'          => $cfg->workflowUuid(),
        'ice_servers'            => array_map(
            static fn ($s) => ['urls' => $s['urls']],
            $ice
        ),
        'turn_configured'        => $turnOn,
        'eval_configured'        => $cfg->evalModel() !== '',
        'eval_model'             => $cfg->evalModel(),
        'eval_model_suggestions' => [
            'gemini/gemini-flash-latest',
            'gemini/gemini-2.5-flash',
            'gemini/gemini-3.6-flash',
            'gemini/gemini-2.5-pro',
        ],
    ]);
}

$store = new Store($cfg);
$dograh = new DograhClient($cfg);

// ------------------------------------------------------------- admin panel
if ($path === '/my-admin-page' || str_starts_with($path, '/my-admin-page/')) {
    $adminPassword = trim((string) $cfg->get('ADMIN_PASSWORD', ''));
    $adminCookie = static function (string $password): string {
        return hash_hmac('sha256', 'mi-admin-v1', $password);
    };
    $adminAuthed = static function () use ($adminPassword, $adminCookie): bool {
        if ($adminPassword === '') {
            return false;
        }
        $got = $_COOKIE['mi_admin'] ?? '';
        return is_string($got) && hash_equals($adminCookie($adminPassword), $got);
    };
    $setAdminCookie = static function (string $value, int $expires): void {
        setcookie('mi_admin', $value, [
            'expires'  => $expires,
            'path'     => '/my-admin-page',
            'secure'   => true,
            'httponly' => true,
            'samesite' => 'Lax',
        ]);
    };

    if ($method === 'GET' && $path === '/my-admin-page') {
        header('Content-Type: text/html; charset=utf-8');
        header('Cache-Control: no-store');
        readfile($root . '/admin/page.html');
        exit;
    }

    if ($method === 'POST' && $path === '/my-admin-page/login') {
        if ($adminPassword === '') {
            json_response(['detail' => 'Admin password is not configured on the server.'], 503);
        }
        $given = (string) (read_json_body()['password'] ?? '');
        if (!hash_equals($adminPassword, $given)) {
            json_response(['detail' => 'Incorrect password.'], 401);
        }
        $setAdminCookie($adminCookie($adminPassword), time() + 12 * 3600);
        json_response(['ok' => true]);
    }

    if ($method === 'POST' && $path === '/my-admin-page/logout') {
        $setAdminCookie('', time() - 3600);
        json_response(['ok' => true]);
    }

    if (!$adminAuthed()) {
        json_response(['detail' => 'Sign in required.'], 401);
    }

    if ($method === 'GET' && $path === '/my-admin-page/api') {
        $workflows = [];
        $workflowsError = null;
        try {
            $workflows = $dograh->listWorkflows();
        } catch (Throwable $e) {
            $workflowsError = $e->getMessage();
        }
        json_response([
            'workflow_id'      => $cfg->workflowId(),
            'workflow_name'    => $cfg->workflowName(),
            'workflow_uuid'    => $cfg->workflowUuid(),
            'running'          => count_live_interviews($store, $dograh),
            'workflows'        => $workflows,
            'workflows_error'  => $workflowsError,
            'metered'          => [
                'domain'      => $cfg->meteredDomain(),
                'configured'  => $cfg->meteredConfigured(),
                'key_hint'    => $cfg->meteredKeyHint(),
            ],
        ]);
    }

    if ($method === 'POST' && $path === '/my-admin-page/api/workflow') {
        $body = read_json_body();
        $id = (int) ($body['id'] ?? 0);
        if ($id <= 0) {
            json_response(['detail' => 'Enter a workflow id.'], 422);
        }
        try {
            $match = $dograh->describeWorkflow($id);
        } catch (Throwable $e) {
            json_response(['detail' => $e->getMessage()], 404);
        }
        $cfg->saveWorkflow($id, (string) $match['name'], $match['uuid']);
        json_response([
            'ok'            => true,
            'workflow_id'   => $cfg->workflowId(),
            'workflow_name' => $cfg->workflowName(),
        ]);
    }

    if ($method === 'POST' && $path === '/my-admin-page/api/metered') {
        $body = read_json_body();
        $domain = trim((string) ($body['domain'] ?? ''));
        $incoming = trim((string) ($body['api_key'] ?? ''));
        $key = $incoming !== '' ? $incoming : $cfg->meteredApiKey();
        if ($domain === '' || $key === '') {
            json_response(['detail' => 'Domain and API key are both required.'], 422);
        }
        $checkDomain = strtolower(preg_replace('#^https?://#', '', $domain) ?? $domain);
        $url = 'https://' . rtrim($checkDomain, '/') . '/api/v1/turn/credentials?apiKey=' . rawurlencode($key);
        $ch = curl_init($url);
        $accepted = false;
        if ($ch !== false) {
            curl_setopt_array($ch, [
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_TIMEOUT => 8,
                CURLOPT_CONNECTTIMEOUT => 5,
            ]);
            $probe = curl_exec($ch);
            $code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
            curl_close($ch);
            $accepted = is_string($probe) && $code === 200;
        }
        if (!$accepted) {
            json_response(['detail' => 'Metered did not accept that domain and API key. Nothing was saved.'], 424);
        }
        try {
            $cfg->saveMetered($domain, $incoming !== '' ? $incoming : null);
        } catch (InvalidArgumentException $e) {
            json_response(['detail' => $e->getMessage()], 422);
        }
        json_response([
            'ok'       => true,
            'domain'   => $cfg->meteredDomain(),
            'key_hint' => $cfg->meteredKeyHint(),
        ]);
    }

    if ($method === 'GET' && $path === '/my-admin-page/api/interviews') {
        $rows = [];
        foreach ($store->recentSessions(40) as $session) {
            $live = ($session['status'] ?? '') !== 'completed' && empty($session['ended_at']);
            if ($live) {
                $fresh = refresh_results($session, $dograh);
                $store->update($fresh);
                $session = $fresh;
            }
            $view = admin_interview_view($session);
            $view['warning_count'] = $store->countEvents((string) ($session['id'] ?? ''), 'warning');
            $rows[] = $view;
        }
        json_response(['interviews' => $rows]);
    }

    if ($method === 'GET' && preg_match('#^/my-admin-page/api/interviews/([a-f0-9]+)/trace$#', $path, $traceMatch)) {
        $session = $store->get($traceMatch[1]);
        if (!$session) {
            json_response(['detail' => 'Interview not found'], 404);
        }
        $fresh = refresh_results($session, $dograh);
        $store->update($fresh);
        $events = $store->readEvents($traceMatch[1]);
        $messages = is_array($fresh['messages'] ?? null) ? $fresh['messages'] : [];
        $runId = (int) ($fresh['workflow_run_id'] ?? 0);
        $audio = [
            'mixed' => recording_available($runId, 'mixed'),
            'user' => recording_available($runId, 'user'),
            'bot' => recording_available($runId, 'bot'),
        ];
        $warnings = 0;
        $endedHow = null;
        foreach ($events as $event) {
            if (($event['kind'] ?? '') === 'warning') {
                $warnings++;
            }
            if (in_array(($event['kind'] ?? ''), ['leave', 'end'], true)) {
                $endedHow = (string) ($event['message'] ?? '');
            }
        }
        $view = admin_interview_view($fresh);
        $view['warning_count'] = $warnings;
        $view['ended_how'] = $endedHow;
        $view['flags_saved'] = $events !== [];
        $view['audio'] = $audio;
        $replayDir = $root . '/data/replays/' . $traceMatch[1];
        $timeline = build_timeline($fresh, $events, $messages, is_dir($replayDir) ? $replayDir : '');
        $origin = audio_origin_ms($audio['mixed'] ? recording_duration_ms($runId) : null, $timeline);
        $view['audio_origin_ms'] = $origin;
        $view['timeline'] = $timeline;
        json_response($view);
    }

    if ($method === 'GET' && preg_match('#^/my-admin-page/api/interviews/([a-f0-9]+)/audio/(mixed|user|bot)$#', $path, $audioMatch)) {
        $session = $store->get($audioMatch[1]);
        if (!$session) {
            json_response(['detail' => 'Interview not found'], 404);
        }
        proxy_recording((int) ($session['workflow_run_id'] ?? 0), $audioMatch[2]);
    }

    if ($method === 'GET' && preg_match('#^/my-admin-page/api/interviews/([a-f0-9]+)/replay/(\d+)\.jpg$#', $path, $replayMatch)) {
        $shot = $root . '/data/replays/' . $replayMatch[1] . '/' . $replayMatch[2] . '.jpg';
        if (!is_file($shot)) {
            json_response(['detail' => 'No camera still for this moment.'], 404);
        }
        header('Content-Type: image/jpeg');
        header('Cache-Control: private, max-age=3600');
        readfile($shot);
        exit;
    }

    if ($method === 'GET' && preg_match('#^/my-admin-page/api/interviews/([a-f0-9]+)/frame$#', $path, $frameMatch)) {
        $frameFile = $root . '/data/live/' . $frameMatch[1] . '.jpg';
        if (!is_file($frameFile)) {
            json_response(['detail' => 'No camera frame yet.'], 404);
        }
        header('Content-Type: image/jpeg');
        header('Cache-Control: no-store');
        readfile($frameFile);
        exit;
    }

    if ($method === 'GET' && preg_match('#^/my-admin-page/api/interviews/([a-f0-9]+)$#', $path, $oneMatch)) {
        $session = $store->get($oneMatch[1]);
        if (!$session) {
            json_response(['detail' => 'Interview not found'], 404);
        }
        $fresh = refresh_results($session, $dograh);
        $store->update($fresh);
        $view = admin_interview_view($fresh);
        $snapFile = $root . '/data/live/' . $oneMatch[1] . '.json';
        $snap = is_file($snapFile) ? json_decode((string) file_get_contents($snapFile), true) : null;
        $view['camera'] = is_array($snap) ? $snap : null;
        $view['has_frame'] = is_file($root . '/data/live/' . $oneMatch[1] . '.jpg');
        json_response($view);
    }

    json_response(['detail' => 'Not found'], 404);
}

// GET /api/interviews/running — live calls only, no candidate details.
if ($method === 'GET' && $path === '/api/interviews/running') {
    json_response([
        'running' => count_live_interviews($store, $dograh),
        'as_of'   => Store::nowIso(),
    ]);
}

// POST /api/interviews
if ($method === 'POST' && $path === '/api/interviews') {
    $b = read_json_body();
    $name = trim((string) ($b['candidate_name'] ?? ''));
    if ($name === '') {
        json_response(['detail' => 'candidate_name is required'], 422);
    }
    $course = trim((string) ($b['course'] ?? ''));
    $role = trim((string) ($b['role'] ?? ''));
    if ($role === '') {
        $role = $course !== '' ? $course : 'Software Engineer';
    }
    $extra = [];
    if (is_array($b['extra_context'] ?? null)) {
        foreach ($b['extra_context'] as $key => $value) {
            if (!is_string($key) || !preg_match('/^[A-Za-z][A-Za-z0-9_]{0,40}$/', $key)) {
                continue;
            }
            if (preg_match('/token|secret|password|api_?key|^key$/i', $key)) {
                continue;
            }
            if (is_array($value) || is_object($value)) {
                continue;
            }
            $text = trim((string) $value);
            if ($text === '') {
                continue;
            }
            $extra[$key] = substr($text, 0, 200);
        }
    }
    if ($course !== '') {
        $extra['course'] = substr($course, 0, 200);
    }
    $context = array_merge(['candidate_name' => $name, 'role' => $role], $extra);

    try {
        $init = $dograh->initEmbedSession($context);
    } catch (Throwable $e) {
        json_response(['detail' => $e->getMessage()], 424);
    }

    $session = $store->create($name, $role, (string) $init['session_token'], (int) $init['workflow_run_id'], $cfg->workflowId());
    json_response([
        'interview_id'    => $session['id'],
        'workflow_run_id' => $session['workflow_run_id'],
        'session_token'   => $init['session_token'],
        'signaling_url'   => $cfg->signalingUrl((string) $init['session_token']),
        'ice_servers'     => $cfg->iceServers(),
        'config'          => $init['config'] ?? [],
    ]);
}

// /api/interviews/{id}[ /messages | /end | /report ]
if (preg_match('#^/api/interviews/([a-f0-9]+)(/messages|/end|/report|/flags)?$#', $path, $m)) {
    $id = $m[1];
    $sub = $m[2] ?? '';
    $session = $store->get($id);
    if (!$session) {
        json_response(['detail' => 'Interview not found'], 404);
    }

    // GET /api/interviews/{id}
    if ($sub === '' && $method === 'GET') {
        if (($session['status'] ?? '') !== 'completed' || empty($session['transcript'])) {
            $session = refresh_results($session, $dograh);
            $store->update($session);
        }
        json_response($store->publicDict($session));
    }

    // GET /api/interviews/{id}/messages
    if ($sub === '/messages' && $method === 'GET') {
        try {
            $savedWorkflow = (int) ($session['workflow_id'] ?? 0);
        $run = $dograh->getRun((int) $session['workflow_run_id'], $savedWorkflow > 0 ? $savedWorkflow : null);
        } catch (Throwable $e) {
            json_response(['detail' => $e->getMessage()], 424);
        }
        $messages = Transcript::messagesFromRun($run);
        if ($messages) {
            $session['messages'] = $messages;
            $session['transcript'] = Transcript::transcriptText($messages);
            $store->update($session);
        }
        json_response([
            'interview_id'    => $session['id'],
            'workflow_run_id' => $session['workflow_run_id'],
            'is_completed'    => (bool) ($run['is_completed'] ?? false),
            'messages'        => $messages,
        ]);
    }

    // POST /api/interviews/{id}/flags — camera warnings and leave reasons from the exam page.
    if ($sub === '/flags' && $method === 'POST') {
        if (!empty($session['ended_at'])) {
            $ended = strtotime((string) $session['ended_at']);
            if ($ended !== false && (time() - $ended) > 180) {
                json_response(['ok' => false], 409);
            }
        }
        $b = read_json_body();
        $kind = (string) ($b['kind'] ?? '');
        $type = (string) ($b['type'] ?? '');
        $state = (string) ($b['state'] ?? '');
        $allowedKind = ['camera', 'warning', 'leave', 'end', 'start', 'agent'];
        $allowedType = ['faces', 'noface', 'gaze', 'motion', 'tab', 'blur', 'fullscreen', 'start', 'violation', 'candidate', 'call-ended', 'closed', 'agent'];
        if (!in_array($kind, $allowedKind, true) || !in_array($type, $allowedType, true)) {
            json_response(['detail' => 'Unknown flag'], 422);
        }
        if ($state !== '' && !in_array($state, ['on', 'off'], true)) {
            $state = '';
        }
        $message = trim((string) ($b['message'] ?? ''));
        $message = preg_replace('/\s+/', ' ', $message) ?? '';
        $message = substr($message, 0, 180);
        if ($message === '') {
            json_response(['detail' => 'Message is required'], 422);
        }
        $elapsed = isset($b['elapsed_ms']) ? (int) $b['elapsed_ms'] : null;
        if ($elapsed !== null && ($elapsed < 0 || $elapsed > 6 * 3600 * 1000)) {
            $elapsed = null;
        }
        $warningNumber = (int) ($b['warning_number'] ?? 0);
        if ($warningNumber < 0 || $warningNumber > 3) {
            $warningNumber = 0;
        }
        $eventIndex = $store->appendEvent((string) $session['id'], [
            'at' => Store::nowIso(),
            'elapsed_ms' => $elapsed,
            'kind' => $kind,
            'type' => $type,
            'state' => $state !== '' ? $state : null,
            'message' => $message,
            'warning_number' => $kind === 'warning' ? $warningNumber : null,
        ]);
        $safeId = preg_replace('/[^a-f0-9]/', '', (string) $session['id']) ?? '';
        if ($eventIndex !== null && $safeId !== '' && in_array($kind, ['camera', 'warning'], true) && $state !== 'off') {
            $liveShot = $root . '/data/live/' . $safeId . '.jpg';
            if (is_file($liveShot)) {
                $replayDir = $root . '/data/replays/' . $safeId;
                if (!is_dir($replayDir)) {
                    @mkdir($replayDir, 0770, true);
                }
                @copy($liveShot, $replayDir . '/' . $eventIndex . '.jpg');
            }
        }
        json_response(['ok' => true]);
    }

    // POST /api/interviews/{id}/end
    if ($sub === '/end' && $method === 'POST') {
        $session = refresh_results($session, $dograh);
        $session['status'] = 'completed';
        if (empty($session['ended_at'])) {
            $session['ended_at'] = Store::nowIso();
        }
        $store->update($session);
        json_response($store->publicDict($session));
    }

    // GET /api/interviews/{id}/report
    if ($sub === '/report' && $method === 'GET') {
        try {
            $savedWorkflow = (int) ($session['workflow_id'] ?? 0);
        $run = $dograh->getRun((int) $session['workflow_run_id'], $savedWorkflow > 0 ? $savedWorkflow : null);
        } catch (Throwable $e) {
            json_response(['detail' => $e->getMessage()], 424);
        }
        $messages = Transcript::messagesFromRun($run);
        if ($messages) {
            $session['messages'] = $messages;
            $session['transcript'] = Transcript::transcriptText($messages);
        }
        if (!empty($run['is_completed']) && ($session['status'] ?? '') !== 'completed') {
            $session['status'] = 'completed';
            if (empty($session['ended_at'])) {
                $session['ended_at'] = Store::nowIso();
            }
        }
        $store->update($session);

        $behavioral = (new Vision($cfg))->sessionAnalysis($id);

        $report = new Report($cfg, new LlmClient($cfg));
        $evalModel = isset($_GET['eval_model']) ? trim((string) $_GET['eval_model']) : null;
        $ai = $report->generateAiEvaluation(
            (string) $session['candidate_name'],
            (string) $session['role'],
            $messages,
            $evalModel !== '' ? $evalModel : null
        );

        $rep = [
            'interview_id'       => $session['id'],
            'generated_at'       => Store::nowIso(),
            'candidate_name'     => $session['candidate_name'],
            'role'               => $session['role'],
            'workflow_run_id'    => $session['workflow_run_id'],
            'status'             => $session['status'],
            'is_completed'       => (bool) ($run['is_completed'] ?? false),
            'recording_url'      => $session['recording_url'] ?? null,
            'stats'              => $report->interviewStats($session, $messages),
            'transcript'         => $messages,
            'behavioral_analysis' => $behavioral,
            'ai_evaluation'      => $ai,
        ];
        $rep['saved_path'] = $report->saveReport($rep);
        json_response($rep);
    }

    // Path matched an interview id but not a supported method/sub.
    json_response(['detail' => 'Method not allowed'], 405);
}

// --------------------------------------------------------------- vision API
if ($method === 'GET' && $path === '/api/vision/health') {
    json_response((new Vision($cfg))->health());
}

if ($method === 'POST' && $path === '/api/vision/analyze') {
    $b = read_json_body();
    $frame = $b['frame_data'] ?? null;
    if (!is_string($frame) || $frame === '') {
        json_response(['detail' => 'frame_data is required'], 422);
    }
    try {
        $analysis = (new Vision($cfg))->analyze($frame, isset($b['session_id']) ? (string) $b['session_id'] : null);
        remember_live_frame($root, (string) ($b['session_id'] ?? ''), $frame, is_array($analysis) ? $analysis : []);
        json_response($analysis);
    } catch (Throwable $e) {
        json_response(['detail' => $e->getMessage()], 424);
    }
}

if ($method === 'GET' && preg_match('#^/api/vision/session/([^/]+)$#', $path, $mv)) {
    json_response((new Vision($cfg))->sessionAnalysis($mv[1]));
}

// ------------------------------------------------------------- dograh webhook
if ($method === 'POST' && $path === '/api/webhooks/dograh') {
    $b = read_json_body();
    $runId = $b['workflow_run_id'] ?? ($b['run_id'] ?? null);
    if ($runId !== null) {
        $s = $store->getByRun((int) $runId);
        if ($s) {
            $s['status'] = 'completed';
            if (!empty($b['transcript'])) {
                $s['transcript'] = $b['transcript'];
            }
            if (!empty($b['recording_url'])) {
                $s['recording_url'] = $b['recording_url'];
            }
            if (!empty($b['gathered_context'])) {
                $s['gathered_context'] = $b['gathered_context'];
            }
            $store->update($s);
        }
    }
    json_response(['ok' => true]);
}

// ------------------------------------------------------------------- 404
json_response(['detail' => 'Not found', 'path' => $path], 404);
