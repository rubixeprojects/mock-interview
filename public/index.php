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
        $run = $dograh->getRun((int) $session['workflow_run_id']);
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

    $session = $store->create($name, $role, (string) $init['session_token'], (int) $init['workflow_run_id']);
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
if (preg_match('#^/api/interviews/([a-f0-9]+)(/messages|/end|/report)?$#', $path, $m)) {
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
            $run = $dograh->getRun((int) $session['workflow_run_id']);
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

    // POST /api/interviews/{id}/end
    if ($sub === '/end' && $method === 'POST') {
        if (($session['status'] ?? '') === 'created') {
            $session['status'] = 'in_progress';
        }
        $session = refresh_results($session, $dograh);
        $store->update($session);
        json_response($store->publicDict($session));
    }

    // GET /api/interviews/{id}/report
    if ($sub === '/report' && $method === 'GET') {
        try {
            $run = $dograh->getRun((int) $session['workflow_run_id']);
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
        json_response((new Vision($cfg))->analyze($frame, isset($b['session_id']) ? (string) $b['session_id'] : null));
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
