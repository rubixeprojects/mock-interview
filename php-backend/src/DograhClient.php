<?php
declare(strict_types=1);

/**
 * Thin client for the Dograh FastAPI backend.
 *
 *   * authenticate (X-API-Key)
 *   * ensure an embed token exists for the interview workflow
 *   * initialise a public embed session (creates a SMALLWEBRTC workflow run)
 *   * read a workflow run after the call
 *
 * The realtime voice media is a direct browser <-> Dograh WebRTC connection and
 * never passes through this backend.
 */
final class DograhClient
{
    private Config $cfg;
    private ?string $embedToken = null;

    public function __construct(Config $cfg)
    {
        $this->cfg = $cfg;
        $this->embedToken = $cfg->embedToken();
    }

    /** @return array<string,string> */
    private function authHeaders(): array
    {
        $key = $this->cfg->apiKey();
        if ($key) {
            return ['X-API-Key' => $key];
        }
        throw new RuntimeException('No Dograh auth configured. Set DOGRAH_API_KEY (or DOGRAH_EMBED_TOKEN for starting sessions).');
    }

    /**
     * Workflows on this Dograh account, without their prompts or definitions.
     *
     * @return array<int,array<string,mixed>>
     */
    public function listWorkflows(): array
    {
        [$st, $body] = Http::request('GET', $this->cfg->apiBase() . '/workflow/fetch', [
            'headers' => $this->authHeaders(),
        ]);
        if ($st !== 200) {
            throw new RuntimeException("Could not list workflows: $st");
        }
        $list = json_decode($body, true);
        if (!is_array($list)) {
            return [];
        }
        $out = [];
        foreach ($list as $item) {
            if (!is_array($item) || empty($item['id'])) {
                continue;
            }
            $out[] = [
                'id'     => (int) $item['id'],
                'name'   => (string) ($item['name'] ?? ('Workflow ' . $item['id'])),
                'uuid'   => isset($item['workflow_uuid']) ? (string) $item['workflow_uuid'] : null,
                'status' => (string) ($item['status'] ?? ($item['state'] ?? '')),
            ];
        }
        return $out;
    }

    /** @return array{id:int,name:string,uuid:?string} */
    public function describeWorkflow(int $id): array
    {
        [$st, $body] = Http::request('GET', $this->cfg->apiBase() . '/workflow/fetch/' . $id, [
            'headers' => $this->authHeaders(),
        ]);
        if ($st !== 200) {
            throw new RuntimeException('That workflow id was not found on Dograh.');
        }
        $item = json_decode($body, true);
        if (!is_array($item) || empty($item['id'])) {
            throw new RuntimeException('That workflow id was not found on Dograh.');
        }
        return [
            'id'   => (int) $item['id'],
            'name' => (string) ($item['name'] ?? ('Workflow ' . $id)),
            'uuid' => isset($item['workflow_uuid']) ? (string) $item['workflow_uuid'] : null,
        ];
    }

    public function ensureEmbedToken(): string
    {
        if ($this->embedToken && !$this->cfg->workflowOverridden()) {
            return $this->embedToken;
        }

        $wid = $this->cfg->workflowId();

        // Try an existing active token first.
        [$st, $body] = Http::request('GET', $this->cfg->apiBase() . "/workflow/$wid/embed-token", [
            'headers' => $this->authHeaders(),
        ]);
        if ($st === 200) {
            $j = json_decode($body, true);
            if (is_array($j) && !empty($j['token'])) {
                $this->embedToken = (string) $j['token'];
                return $this->embedToken;
            }
        }

        // Otherwise create one. Empty allowed_domains => any origin allowed.
        $domains = $this->cfg->embedAllowedDomains();
        $payload = [
            'allowed_domains' => $domains ?: null,
            'settings'        => ['widgetType' => 'voice'],
            'expires_in_days' => 30,
        ];
        [$st, $body] = Http::request('POST', $this->cfg->apiBase() . "/workflow/$wid/embed-token", [
            'headers' => $this->authHeaders(),
            'json'    => $payload,
        ]);
        if ($st !== 200) {
            throw new RuntimeException("Failed to create embed token: $st $body");
        }
        $j = json_decode($body, true);
        $this->embedToken = (string) $j['token'];
        return $this->embedToken;
    }

    /**
     * @param array<string,mixed> $contextVariables
     * @return array<string,mixed> {session_token, workflow_run_id, config}
     */
    public function initEmbedSession(array $contextVariables): array
    {
        $token = $this->ensureEmbedToken();
        [$st, $body] = Http::request('POST', $this->cfg->apiBase() . '/public/embed/init', [
            'json' => ['token' => $token, 'context_variables' => $contextVariables],
        ]);
        if ($st !== 200) {
            throw new RuntimeException("Embed init failed: $st $body");
        }
        return json_decode($body, true);
    }

    /** @return array<string,mixed> */
    public function getRun(int $runId, ?int $workflowId = null): array
    {
        $wid = ($workflowId !== null && $workflowId > 0) ? $workflowId : $this->cfg->workflowId();
        [$st, $body] = Http::request('GET', $this->cfg->apiBase() . "/workflow/$wid/runs/$runId", [
            'headers' => $this->authHeaders(),
        ]);
        if ($st !== 200) {
            throw new RuntimeException("Get run failed: $st $body");
        }
        return json_decode($body, true);
    }
}
