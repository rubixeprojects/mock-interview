<?php
declare(strict_types=1);

/**
 * Post-interview report assembly: stats + AI evaluation of the answers + saving.
 *
 * The evaluation prompt is identical to the Python backend. The model call goes
 * through LlmClient (Gemini / OpenAI-compatible), not LiteLLM.
 */
final class Report
{
    private Config $cfg;
    private LlmClient $llm;

    public function __construct(Config $cfg, LlmClient $llm)
    {
        $this->cfg = $cfg;
        $this->llm = $llm;
    }

    private const SYSTEM_PROMPT = <<<'TXT'
You are a senior Data Science interviewer writing a structured, fair, and evidence-based evaluation of a candidate's MOCK INTERVIEW. Judge only what the transcript supports. Be specific and cite what the candidate actually said. Do not invent facts. The interview is project-based and focused on Data Science.

Return ONLY a JSON object with exactly these keys:
{
  "summary": string,                      // 2-3 sentence overall summary
  "strengths": string[],                  // concrete, evidence-backed
  "weaknesses": string[],                 // concrete, evidence-backed
  "topic_assessment": [                   // per Data Science topic touched
     {"topic": string, "rating": "strong"|"adequate"|"weak"|"not_covered", "evidence": string}
  ],
  "communication": {"rating": "strong"|"adequate"|"weak", "notes": string},
  "technical_depth": {"rating": "strong"|"adequate"|"weak", "notes": string},
  "overall_score": number,                // 0-100
  "recommendation": "strong_hire"|"hire"|"lean_no_hire"|"no_hire",
  "rationale": string
}
Output valid JSON only, no markdown, no code fences.
TXT;

    /**
     * @param array<int,array<string,mixed>> $messages
     * @return array<int,array{role:string,content:string}>
     */
    public function buildEvaluationMessages(string $candidateName, string $role, array $messages): array
    {
        $lines = [];
        foreach ($messages as $m) {
            $who = ($m['role'] ?? '') === 'user' ? 'Candidate' : 'Interviewer';
            $lines[] = $who . ': ' . ($m['text'] ?? '');
        }
        $transcript = $lines ? implode("\n", $lines) : '(no transcript captured)';

        $user = "Candidate: {$candidateName}\n"
            . "Target role: {$role}\n\n"
            . "INTERVIEW TRANSCRIPT:\n{$transcript}\n\n"
            . 'Evaluate the candidate per the required JSON schema.';

        return [
            ['role' => 'system', 'content' => self::SYSTEM_PROMPT],
            ['role' => 'user', 'content' => $user],
        ];
    }

    /**
     * @param array<int,array<string,mixed>> $messages
     * @return array<string,mixed>
     */
    public function generateAiEvaluation(string $candidateName, string $role, array $messages, ?string $modelOverride = null): array
    {
        $evalMessages = $this->buildEvaluationMessages($candidateName, $role, $messages);

        if (!$messages) {
            return [
                'status' => 'unavailable',
                'detail' => 'No transcript was captured, so no evaluation was generated.',
            ];
        }

        $model = trim((string) ($modelOverride !== null && $modelOverride !== '' ? $modelOverride : $this->cfg->evalModel()));
        if ($model === '') {
            return [
                'status' => 'pending',
                'detail' => 'AI evaluation not configured. Set EVAL_LLM_MODEL (and a provider key) to enable it. The prompt is ready.',
                'prompt' => $evalMessages,
            ];
        }

        $res = $this->llm->complete($model, $evalMessages);
        if (!$res['ok']) {
            return [
                'status' => 'error',
                'detail' => 'Evaluation model call failed: ' . ($res['error'] ?? 'unknown'),
                'prompt' => $evalMessages,
            ];
        }

        $parsed = self::safeParseJson($res['content'] ?? '');
        return [
            'status'     => 'ok',
            'model'      => $res['model'],
            'evaluation' => $parsed,
            'raw'        => $parsed === null ? ($res['content'] ?? null) : null,
        ];
    }

    /** @return array<string,mixed>|null */
    public static function safeParseJson(?string $text): ?array
    {
        if ($text === null || $text === '') {
            return null;
        }
        $t = trim($text);
        if (str_starts_with($t, '```')) {
            $t = trim($t, '`');
            if (stripos($t, 'json') === 0) {
                $t = substr($t, 4);
            }
        }
        $j = json_decode(trim($t), true);
        if (is_array($j)) {
            return $j;
        }
        // Try to extract the first {...} block.
        $start = strpos($t, '{');
        $end = strrpos($t, '}');
        if ($start !== false && $end !== false && $end > $start) {
            $j = json_decode(substr($t, $start, $end - $start + 1), true);
            if (is_array($j)) {
                return $j;
            }
        }
        return null;
    }

    /**
     * @param array<string,mixed> $session
     * @param array<int,array<string,mixed>> $messages
     * @return array<string,mixed>
     */
    public function interviewStats(array $session, array $messages): array
    {
        $candidate = array_filter($messages, fn ($m) => ($m['role'] ?? '') === 'user');
        $interviewer = array_filter($messages, fn ($m) => ($m['role'] ?? '') === 'assistant');
        $questions = array_filter($interviewer, fn ($m) => strpos((string) ($m['text'] ?? ''), '?') !== false);

        $duration = null;
        $start = $session['created_at'] ?? null;
        $end = $session['ended_at'] ?? null;
        if ($start && $end) {
            $ts = strtotime((string) $start);
            $te = strtotime((string) $end);
            if ($ts !== false && $te !== false) {
                $duration = $te - $ts;
            }
        }

        return [
            'total_turns'      => count($messages),
            'candidate_turns'  => count($candidate),
            'interviewer_turns' => count($interviewer),
            'questions_asked'  => count($questions),
            'duration_seconds' => $duration,
            'started_at'       => $start,
            'ended_at'         => $end,
        ];
    }

    /**
     * Persist a report as data/reports/<interview_id>.json (best-effort).
     * @param array<string,mixed> $report
     */
    public function saveReport(array $report): ?string
    {
        try {
            $dir = $this->cfg->reportsDir();
            if (!is_dir($dir)) {
                @mkdir($dir, 0770, true);
            }
            $id = (string) ($report['interview_id'] ?? 'unknown');
            $safe = preg_replace('/[^A-Za-z0-9_-]/', '', $id);
            $target = $dir . '/' . $safe . '.json';

            $tmp = tempnam($dir, '.tmp');
            if ($tmp === false) {
                return null;
            }
            file_put_contents(
                $tmp,
                json_encode($report, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)
            );
            rename($tmp, $target);
            return $target;
        } catch (Throwable $e) {
            return null;
        }
    }
}
