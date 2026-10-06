<?php
declare(strict_types=1);

/**
 * migrate_workflow.php — copy a Dograh mock-interview workflow between servers.
 *
 * Reads the mock-interview workflow (its FLOW/definition, PROMPTS, and
 * turn-taking CONFIGURATIONS) from a SOURCE Dograh instance and re-creates it on
 * a TARGET Dograh instance — by default the server described in
 * `../deploy/server-info.txt` (the Lightsail box, Dograh API on :8000).
 *
 * Only control-plane REST calls are used (the same API the backend already
 * speaks). Nothing is hardcoded: every credential comes from CLI flags, the
 * environment, or `.env`. See --help for usage.
 *
 * Exit codes: 0 ok, 1 usage/arg error, 2 runtime/API error.
 */

require __DIR__ . '/src/Http.php';
require __DIR__ . '/src/Config.php';

// ---------------------------------------------------------------- CLI parsing
/**
 * Parse `--key=value`, `--key value`, and boolean `--flag` style arguments.
 *
 * @param string[] $argv
 * @return array<string,string|bool>
 */
function parse_args(array $argv): array
{
    $out = [];
    $n = count($argv);
    for ($i = 1; $i < $n; $i++) {
        $arg = $argv[$i];
        if ($arg === '-h' || $arg === '--help') {
            $out['help'] = true;
            continue;
        }
        if (strncmp($arg, '--', 2) !== 0) {
            fwrite(STDERR, "Ignoring unexpected argument: $arg\n");
            continue;
        }
        $arg = substr($arg, 2);
        $eq = strpos($arg, '=');
        if ($eq !== false) {
            $out[substr($arg, 0, $eq)] = substr($arg, $eq + 1);
            continue;
        }
        // No "=": treat the next token as the value unless it's another flag.
        if ($i + 1 < $n && strncmp($argv[$i + 1], '--', 2) !== 0) {
            $out[$arg] = $argv[++$i];
        } else {
            $out[$arg] = true; // boolean flag
        }
    }
    return $out;
}

/** @param array<string,string|bool> $opts */
function opt(array $opts, string $key, ?string $default = null): ?string
{
    if (!array_key_exists($key, $opts)) {
        return $default;
    }
    $v = $opts[$key];
    return is_bool($v) ? ($v ? '' : $default) : (string) $v;
}

/** @param array<string,string|bool> $opts */
function flag(array $opts, string $key): bool
{
    return array_key_exists($key, $opts) && $opts[$key] !== false && $opts[$key] !== '0';
}

function usage(): void
{
    $me = 'php migrate_workflow.php';
    echo <<<TXT
Copy a Dograh mock-interview workflow (flow + prompts + turn-taking config)
from a SOURCE Dograh server to a TARGET Dograh server.

USAGE
  $me [options]

SOURCE (where the workflow currently lives)
  --source-url=URL           Dograh base URL, e.g. http://127.0.0.1:8000
                             (default: DOGRAH_BASE_URL from .env)
  --source-api-key=KEY       X-API-Key for the source (default: DOGRAH_API_KEY)
  --source-email=EMAIL       Alt auth: login email (with --source-password)
  --source-password=PASS     Alt auth: login password
  --source-workflow-id=N     Workflow id to copy (default: DOGRAH_WORKFLOW_ID or 3)

TARGET (where the workflow is created)
  --target-url=URL           Dograh base URL
                             (default: derived from ../deploy/server-info.txt)
  --target-api-key=KEY       X-API-Key for the target
  --target-email=EMAIL       Alt auth: login email (with --target-password)
  --target-password=PASS     Alt auth: login password
  --target-signup            Create the target account first (fresh servers have
                             ENABLE_SIGNUP=true), then log in
  --target-create-api-key    After migrating, mint an X-API-Key on the target and
                             print it (handy for the php-backend .env)

BEHAVIOUR
  --name=NAME                Name for the workflow on the target (default: source name)
  --update                   If a workflow with the same name exists on the target,
                             overwrite it instead of creating a new one
  --no-publish               Create/update as a draft; skip publishing
  --out=FILE                 Also save the fetched source workflow JSON to FILE (backup)
  --dry-run                  Fetch + summarise the source only; write nothing
  -h, --help                 Show this help

EXAMPLES
  # Local source (run on the source host) -> Lightsail target, sign up on target:
  $me --source-url=http://127.0.0.1:8000 --source-api-key=sk_live_... \\
      --target-url=http://3.6.32.171:8000 \\
      --target-email=admin@example.com --target-password=... --target-signup \\
      --target-create-api-key

  # Preview what would be copied (no writes):
  $me --source-api-key=sk_live_... --dry-run

TXT;
}

// ------------------------------------------------------------- HTTP + helpers
/**
 * Perform a JSON request and decode the body.
 *
 * @param array<string,string> $headers
 * @param mixed $json  Request body (omitted when null)
 * @return array{0:int,1:mixed,2:string} [status, decoded, raw]
 */
function api(string $method, string $url, array $headers, $json = null): array
{
    $opts = ['headers' => $headers];
    if ($json !== null) {
        $opts['json'] = $json;
    }
    [$status, $raw] = Http::request($method, $url, $opts);
    $decoded = ($raw === '') ? null : json_decode($raw, true);
    return [$status, $decoded, $raw];
}

function fail(string $msg): void
{
    throw new RuntimeException($msg);
}

/** Derive the default target base URL from ../deploy/server-info.txt. */
function default_target_url(): ?string
{
    $file = __DIR__ . '/../deploy/server-info.txt';
    if (!is_file($file)) {
        return null;
    }
    $txt = (string) file_get_contents($file);
    if (preg_match('/lightsail\s*ip\s*:\s*([0-9]{1,3}(?:\.[0-9]{1,3}){3})/i', $txt, $m)) {
        return 'http://' . $m[1] . ':8000';
    }
    return null;
}

/**
 * Authenticate against a Dograh instance and return request headers.
 * Prefers an API key; otherwise logs in (optionally signing up first) for a JWT.
 *
 * @return array<string,string>
 */
function authenticate(
    string $label,
    string $baseUrl,
    ?string $apiKey,
    ?string $email,
    ?string $password,
    bool $signup
): array {
    $apiKey = $apiKey !== null ? trim($apiKey) : null;
    if ($apiKey !== null && $apiKey !== '') {
        echo "[$label] auth: X-API-Key\n";
        return ['X-API-Key' => $apiKey];
    }

    if ($email === null || $email === '' || $password === null || $password === '') {
        fail("[$label] no credentials. Provide --{$label}-api-key, or --{$label}-email + --{$label}-password.");
    }

    $apiBase = rtrim($baseUrl, '/') . '/api/v1';

    if ($signup) {
        echo "[$label] signup: $email\n";
        [$st, , $raw] = api('POST', "$apiBase/auth/signup", [], [
            'email' => $email,
            'password' => $password,
            'name' => 'Workflow Migrator',
        ]);
        // 2xx = created; 400/409 = already exists -> fine, we'll just log in.
        if ($st >= 200 && $st < 300) {
            echo "[$label] signup ok\n";
        } elseif ($st === 400 || $st === 409) {
            echo "[$label] account already exists; continuing to login\n";
        } else {
            fail("[$label] signup failed: HTTP $st " . substr($raw, 0, 400));
        }
    }

    echo "[$label] login: $email\n";
    [$st, $body, $raw] = api('POST', "$apiBase/auth/login", [], [
        'email' => $email,
        'password' => $password,
    ]);
    if ($st !== 200 || !is_array($body) || empty($body['token'])) {
        fail("[$label] login failed: HTTP $st " . substr($raw, 0, 400));
    }
    return ['Authorization' => 'Bearer ' . $body['token']];
}

/** Summarise a workflow definition for human-friendly logging. */
function summarise_definition($definition): string
{
    if (!is_array($definition)) {
        return '(unparseable definition)';
    }
    $nodes = $definition['nodes'] ?? [];
    $edges = $definition['edges'] ?? [];
    $promptNodes = 0;
    $chars = 0;
    foreach ($nodes as $node) {
        $prompt = $node['data']['prompt'] ?? '';
        if (is_string($prompt) && $prompt !== '') {
            $promptNodes++;
            $chars += strlen($prompt);
        }
    }
    return sprintf(
        '%d node(s), %d edge(s), %d prompt-bearing node(s), %d prompt chars',
        count($nodes),
        count($edges),
        $promptNodes,
        $chars
    );
}

// --------------------------------------------------------------------- main
function run(array $argv): int
{
    $opts = parse_args($argv);
    if (flag($opts, 'help')) {
        usage();
        return 0;
    }

    // Source defaults come from php-backend/.env (via the existing Config loader).
    $cfg = new Config(__DIR__);

    $sourceUrl = rtrim((string) (opt($opts, 'source-url', $cfg->dographBaseUrl()) ?? ''), '/');
    $sourceApiKey = opt($opts, 'source-api-key', $cfg->apiKey());
    $sourceEmail = opt($opts, 'source-email');
    $sourcePassword = opt($opts, 'source-password');
    $sourceWorkflowId = (int) (opt($opts, 'source-workflow-id', (string) $cfg->workflowId()) ?? '0');

    $targetUrl = rtrim((string) (opt($opts, 'target-url', default_target_url()) ?? ''), '/');
    $targetApiKey = opt($opts, 'target-api-key');
    $targetEmail = opt($opts, 'target-email');
    $targetPassword = opt($opts, 'target-password');
    $targetSignup = flag($opts, 'target-signup');

    $overrideName = opt($opts, 'name');
    $doUpdate = flag($opts, 'update');
    $publish = !flag($opts, 'no-publish');
    $outFile = opt($opts, 'out');
    $dryRun = flag($opts, 'dry-run');

    // ---- validate source ----
    if ($sourceUrl === '') {
        fail('No source URL. Set --source-url or DOGRAH_BASE_URL in .env.');
    }
    if ($sourceWorkflowId <= 0) {
        fail('Invalid --source-workflow-id (must be a positive integer).');
    }

    echo "Source : $sourceUrl (workflow #$sourceWorkflowId)\n";
    echo "Target : " . ($targetUrl !== '' ? $targetUrl : '(unset)') . "\n";
    echo str_repeat('-', 60) . "\n";

    // ---- fetch from source ----
    $srcHeaders = authenticate('source', $sourceUrl, $sourceApiKey, $sourceEmail, $sourcePassword, false);
    $srcApiBase = "$sourceUrl/api/v1";

    echo "[source] fetching workflow #$sourceWorkflowId ...\n";
    [$st, $wf, $raw] = api('GET', "$srcApiBase/workflow/fetch/$sourceWorkflowId", $srcHeaders);
    if ($st !== 200 || !is_array($wf)) {
        fail("[source] fetch failed: HTTP $st " . substr($raw, 0, 400));
    }

    $name = $overrideName ?? (string) ($wf['name'] ?? 'Mock Interview');

    $definition = $wf['workflow_definition'] ?? null;
    if (is_string($definition)) {
        $definition = json_decode($definition, true);
    }
    if (!is_array($definition)) {
        fail('[source] workflow_definition is missing or not decodable.');
    }

    $configurations = $wf['workflow_configurations'] ?? null;
    if (is_string($configurations)) {
        $configurations = json_decode($configurations, true);
    }
    $templateVars = $wf['template_context_variables'] ?? null;
    if (is_string($templateVars)) {
        $templateVars = json_decode($templateVars, true);
    }

    echo "[source] name: \"$name\"\n";
    echo "[source] flow: " . summarise_definition($definition) . "\n";
    echo "[source] turn-taking config: " . (is_array($configurations) && $configurations !== []
        ? json_encode($configurations, JSON_UNESCAPED_SLASHES)
        : '(none)') . "\n";

    if ($outFile !== null && $outFile !== '') {
        file_put_contents($outFile, json_encode([
            'name' => $name,
            'workflow_definition' => $definition,
            'workflow_configurations' => $configurations,
            'template_context_variables' => $templateVars,
        ], JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE));
        echo "[source] saved backup -> $outFile\n";
    }

    if ($dryRun) {
        echo "\nDry run: nothing written to the target.\n";
        return 0;
    }

    // ---- validate target ----
    if ($targetUrl === '') {
        fail('No target URL. Set --target-url (or ensure ../deploy/server-info.txt has the lightsail ip).');
    }
    $tgtHeaders = authenticate('target', $targetUrl, $targetApiKey, $targetEmail, $targetPassword, $targetSignup);
    $tgtApiBase = "$targetUrl/api/v1";

    // ---- decide create vs update ----
    $targetId = null;
    if ($doUpdate) {
        echo "[target] looking for an existing workflow named \"$name\" ...\n";
        [$st, $list] = api('GET', "$tgtApiBase/workflow/fetch", $tgtHeaders);
        if ($st === 200 && is_array($list)) {
            foreach ($list as $w) {
                if (isset($w['name']) && $w['name'] === $name) {
                    $targetId = (int) $w['id'];
                    echo "[target] found existing workflow #$targetId\n";
                    break;
                }
            }
        }
    }

    $targetUuid = null;
    if ($targetId === null) {
        echo "[target] creating workflow \"$name\" ...\n";
        [$st, $created, $raw] = api('POST', "$tgtApiBase/workflow/create/definition", $tgtHeaders, [
            'name' => $name,
            'workflow_definition' => $definition,
        ]);
        if (($st < 200 || $st >= 300) || !is_array($created) || empty($created['id'])) {
            fail("[target] create failed: HTTP $st " . substr($raw, 0, 500));
        }
        $targetId = (int) $created['id'];
        $targetUuid = $created['workflow_uuid'] ?? null;
        echo "[target] created workflow #$targetId\n";
    }

    // ---- push definition + prompts + turn-taking config (PUT) ----
    // create/definition doesn't accept configurations, so always PUT to ensure
    // the flow, prompts AND turn-taking configuration all land together.
    $putBody = [
        'name' => $name,
        'workflow_definition' => $definition,
    ];
    if (is_array($configurations) && $configurations !== []) {
        $putBody['workflow_configurations'] = $configurations;
    }
    if (is_array($templateVars) && $templateVars !== []) {
        $putBody['template_context_variables'] = $templateVars;
    }
    echo "[target] updating workflow #$targetId (flow + prompts + config) ...\n";
    [$st, , $raw] = api('PUT', "$tgtApiBase/workflow/$targetId", $tgtHeaders, $putBody);
    if ($st < 200 || $st >= 300) {
        fail("[target] update failed: HTTP $st " . substr($raw, 0, 500));
    }

    // ---- publish ----
    if ($publish) {
        echo "[target] publishing workflow #$targetId ...\n";
        [$st, , $raw] = api('POST', "$tgtApiBase/workflow/$targetId/publish", $tgtHeaders);
        if ($st < 200 || $st >= 300) {
            fail("[target] publish failed: HTTP $st " . substr($raw, 0, 500));
        }
        echo "[target] published\n";
    } else {
        echo "[target] left as draft (--no-publish)\n";
    }

    // ---- verify + report ----
    [$st, $verify] = api('GET', "$tgtApiBase/workflow/fetch/$targetId", $tgtHeaders);
    if ($st === 200 && is_array($verify)) {
        $targetUuid = $verify['workflow_uuid'] ?? $targetUuid;
        $vdef = $verify['workflow_definition'] ?? null;
        if (is_string($vdef)) {
            $vdef = json_decode($vdef, true);
        }
        echo "[target] verify: status=" . ($verify['status'] ?? '?')
            . ", version=" . ($verify['version_number'] ?? '?')
            . ", " . summarise_definition($vdef) . "\n";
    }

    // ---- optional: mint an API key for the php-backend .env ----
    $mintedKey = null;
    if (flag($opts, 'target-create-api-key')) {
        echo "[target] creating an API key ...\n";
        [$st, $keyResp, $raw] = api('POST', "$tgtApiBase/user/api-keys", $tgtHeaders, [
            'name' => 'mock-interview-backend',
        ]);
        if ($st >= 200 && $st < 300 && is_array($keyResp) && !empty($keyResp['api_key'])) {
            $mintedKey = (string) $keyResp['api_key'];
            echo "[target] api key created (prefix: " . ($keyResp['key_prefix'] ?? '?') . ")\n";
        } else {
            // Non-fatal: the migration itself already succeeded.
            fwrite(STDERR, "[target] WARN: could not create API key: HTTP $st " . substr($raw, 0, 300) . "\n");
        }
    }

    // ---- summary ----
    echo "\n" . str_repeat('=', 60) . "\n";
    echo "Migration complete.\n";
    echo "  Target workflow id   : $targetId\n";
    echo "  Target workflow uuid : " . ($targetUuid ?? '(unknown)') . "\n";
    echo "\nPoint the mock-interview backend at the target by setting these in\n";
    echo "php-backend/.env (or backend/.env):\n\n";
    echo "  DOGRAH_BASE_URL=$targetUrl\n";
    echo "  DOGRAH_WORKFLOW_ID=$targetId\n";
    if ($targetUuid) {
        echo "  DOGRAH_WORKFLOW_UUID=$targetUuid\n";
    }
    if ($mintedKey) {
        echo "  DOGRAH_API_KEY=$mintedKey\n";
    }
    echo "\n";

    return 0;
}

try {
    exit(run($argv));
} catch (Throwable $e) {
    fwrite(STDERR, 'ERROR: ' . $e->getMessage() . "\n");
    exit(2);
}
