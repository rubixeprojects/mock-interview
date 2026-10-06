<?php
declare(strict_types=1);

/**
 * File-based interview session store.
 *
 * The Python backend keeps sessions in process memory (uvicorn is long-lived).
 * PHP is request-scoped, so sessions are persisted as one JSON file per
 * interview under data/sessions/. Same public shape as the Python version.
 */
final class Store
{
    private string $dir;

    public function __construct(Config $cfg)
    {
        $this->dir = $cfg->root . '/data/sessions';
        if (!is_dir($this->dir)) {
            @mkdir($this->dir, 0770, true);
        }
    }

    public static function nowIso(): string
    {
        return (new DateTime('now', new DateTimeZone('UTC')))->format('Y-m-d\TH:i:s.uP');
    }

    /** @return array<string,mixed> */
    public function create(string $candidateName, string $role, string $sessionToken, int $workflowRunId): array
    {
        $session = [
            'id'               => bin2hex(random_bytes(16)),
            'candidate_name'   => $candidateName,
            'role'             => $role,
            'session_token'    => $sessionToken,
            'workflow_run_id'  => $workflowRunId,
            'status'           => 'created',
            'created_at'       => self::nowIso(),
            'ended_at'         => null,
            'transcript'       => null,
            'recording_url'    => null,
            'gathered_context' => null,
            'messages'         => [],
        ];
        $this->write($session);
        return $session;
    }

    /** @return array<string,mixed>|null */
    public function get(string $id): ?array
    {
        $f = $this->path($id);
        if (!is_file($f)) {
            return null;
        }
        $j = json_decode((string) file_get_contents($f), true);
        return is_array($j) ? $j : null;
    }

    /** @param array<string,mixed> $session */
    public function update(array $session): void
    {
        $this->write($session);
    }

    /** @return array<string,mixed>|null */
    public function getByRun(int $workflowRunId): ?array
    {
        foreach (glob($this->dir . '/*.json') ?: [] as $f) {
            $s = json_decode((string) file_get_contents($f), true);
            if (is_array($s) && (int) ($s['workflow_run_id'] ?? -1) === $workflowRunId) {
                return $s;
            }
        }
        return null;
    }

    /**
     * @param array<string,mixed> $s
     * @return array<string,mixed>
     */
    public function publicDict(array $s): array
    {
        return [
            'interview_id'     => $s['id'],
            'candidate_name'   => $s['candidate_name'],
            'role'             => $s['role'],
            'workflow_run_id'  => $s['workflow_run_id'],
            'status'           => $s['status'],
            'created_at'       => $s['created_at'],
            'ended_at'         => $s['ended_at'] ?? null,
            'transcript'       => $s['transcript'] ?? null,
            'recording_url'    => $s['recording_url'] ?? null,
            'gathered_context' => $s['gathered_context'] ?? null,
            'messages'         => $s['messages'] ?? [],
        ];
    }

    private function path(string $id): string
    {
        $safe = preg_replace('/[^a-f0-9]/', '', $id);
        return $this->dir . '/' . $safe . '.json';
    }

    /** @param array<string,mixed> $s */
    private function write(array $s): void
    {
        file_put_contents(
            $this->path((string) $s['id']),
            json_encode($s, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)
        );
    }
}
