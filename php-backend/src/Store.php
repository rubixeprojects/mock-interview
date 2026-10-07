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
    public function create(string $candidateName, string $role, string $sessionToken, int $workflowRunId, int $workflowId): array
    {
        $session = [
            'id'               => bin2hex(random_bytes(16)),
            'candidate_name'   => $candidateName,
            'role'             => $role,
            'session_token'    => $sessionToken,
            'workflow_id'      => $workflowId,
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

    /**
     * Interviews that have not ended and were created within $maxAgeSeconds.
     * Older unfinished files are abandoned sessions, not live calls.
     *
     * @return array<int,array<string,mixed>>
     */
    public function openSessions(int $maxAgeSeconds = 1800): array
    {
        $now = time();
        $open = [];
        foreach (glob($this->dir . '/*.json') ?: [] as $file) {
            $session = json_decode((string) file_get_contents($file), true);
            if (!is_array($session)) {
                continue;
            }
            if (($session['status'] ?? '') === 'completed' || !empty($session['ended_at'])) {
                continue;
            }
            $created = strtotime((string) ($session['created_at'] ?? ''));
            if ($created === false || ($now - $created) > $maxAgeSeconds) {
                continue;
            }
            $open[] = $session;
        }
        return $open;
    }

    /**
     * Newest interviews first. Session tokens are not included.
     *
     * @return array<int,array<string,mixed>>
     */
    public function recentSessions(int $limit = 40): array
    {
        $rows = [];
        foreach (glob($this->dir . '/*.json') ?: [] as $file) {
            $session = json_decode((string) file_get_contents($file), true);
            if (is_array($session)) {
                $rows[] = $session;
            }
        }
        usort($rows, static function (array $a, array $b): int {
            return strcmp((string) ($b['created_at'] ?? ''), (string) ($a['created_at'] ?? ''));
        });
        return array_slice($rows, 0, $limit);
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

    /**
     * One JSON object per line. Kept beside the session file so a session
     * update cannot wipe the flag history.
     *
     * @param array<string,mixed> $event
     * @return int|null line index of the stored event
     */
    public function appendEvent(string $id, array $event): ?int
    {
        $path = $this->eventsPath($id);
        $fh = fopen($path, 'c+');
        if ($fh === false) {
            return null;
        }
        if (!flock($fh, LOCK_EX)) {
            fclose($fh);
            return null;
        }
        $size = filesize($path);
        if ($size !== false && $size > 250000) {
            flock($fh, LOCK_UN);
            fclose($fh);
            return null;
        }
        $index = 0;
        $last = '';
        if ($size) {
            rewind($fh);
            while (($line = fgets($fh)) !== false) {
                if (trim($line) === '') {
                    continue;
                }
                $last = $line;
                $index++;
            }
        }
        $prev = $last !== '' ? json_decode($last, true) : null;
        if (is_array($prev)) {
            $same = ($prev['kind'] ?? '') === ($event['kind'] ?? '')
                && ($prev['type'] ?? '') === ($event['type'] ?? '')
                && ($prev['state'] ?? '') === ($event['state'] ?? '')
                && ($prev['message'] ?? '') === ($event['message'] ?? '');
            $prevAt = strtotime((string) ($prev['at'] ?? ''));
            $nowAt = strtotime((string) ($event['at'] ?? ''));
            if ($same && $prevAt && $nowAt && abs($nowAt - $prevAt) < 5) {
                flock($fh, LOCK_UN);
                fclose($fh);
                return null;
            }
        }
        fseek($fh, 0, SEEK_END);
        fwrite($fh, json_encode($event, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . "\n");
        fflush($fh);
        flock($fh, LOCK_UN);
        fclose($fh);
        return $index;
    }

    /** @return array<int,array<string,mixed>> */
    public function readEvents(string $id): array
    {
        $path = $this->eventsPath($id);
        if (!is_file($path)) {
            return [];
        }
        $lines = file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
        if (!is_array($lines)) {
            return [];
        }
        $out = [];
        foreach ($lines as $line) {
            $row = json_decode($line, true);
            if (is_array($row)) {
                $out[] = $row;
            }
        }
        return $out;
    }

    public function countEvents(string $id, string $kind): int
    {
        $n = 0;
        foreach ($this->readEvents($id) as $event) {
            if (($event['kind'] ?? '') === $kind) {
                $n++;
            }
        }
        return $n;
    }

    private function eventsPath(string $id): string
    {
        return $this->path($id) . '.events.jsonl';
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
