<?php
declare(strict_types=1);

/**
 * Minimal multi-provider LLM client (replaces LiteLLM for the PHP port).
 *
 * Model strings follow the LiteLLM convention:
 *   * "gemini/<model>"      -> Google Generative Language API (generateContent)
 *   * "openrouter/<model>"  -> OpenRouter (OpenAI-compatible)
 *   * "openai/<model>", "gpt-..." or anything with EVAL_LLM_API_BASE set
 *                            -> OpenAI-compatible /chat/completions
 *
 * The provider API key is taken from server config (EVAL_LLM_API_KEY) only.
 */
final class LlmClient
{
    /** Alternate Gemini Flash models to try if the configured one is overloaded (503). */
    private const GEMINI_FALLBACKS = [
        'gemini/gemini-2.5-flash',
        'gemini/gemini-3.6-flash',
        'gemini/gemini-flash-latest',
    ];

    private Config $cfg;

    public function __construct(Config $cfg)
    {
        $this->cfg = $cfg;
    }

    /**
     * @param array<int,array{role:string,content:string}> $messages
     * @return array{ok:bool,content?:string,model?:?string,error?:string}
     */
    public function complete(string $model, array $messages): array
    {
        $models = [$model];
        if (str_starts_with($model, 'gemini/')) {
            foreach (self::GEMINI_FALLBACKS as $alt) {
                if (!in_array($alt, $models, true)) {
                    $models[] = $alt;
                }
            }
        }

        $lastError = null;
        foreach ($models as $m) {
            try {
                $content = $this->callOne($m, $messages);
                return ['ok' => true, 'content' => $content, 'model' => $m];
            } catch (Throwable $e) {
                $lastError = $e->getMessage();
            }
        }
        return ['ok' => false, 'model' => null, 'error' => $lastError ?? 'unknown error'];
    }

    /** @param array<int,array{role:string,content:string}> $messages */
    private function callOne(string $model, array $messages): string
    {
        $key = $this->cfg->evalApiKey();

        if (str_starts_with($model, 'gemini/')) {
            return $this->callGemini(substr($model, strlen('gemini/')), $messages, $key);
        }

        $base = $this->cfg->evalApiBase();
        if (str_starts_with($model, 'openrouter/')) {
            $base = $base ?: 'https://openrouter.ai/api/v1';
            $model = substr($model, strlen('openrouter/'));
        } elseif (str_starts_with($model, 'openai/')) {
            $base = $base ?: 'https://api.openai.com/v1';
            $model = substr($model, strlen('openai/'));
        } else {
            $base = $base ?: 'https://api.openai.com/v1';
        }
        return $this->callOpenAiCompatible($base, $model, $messages, $key);
    }

    /** @param array<int,array{role:string,content:string}> $messages */
    private function callGemini(string $model, array $messages, ?string $key): string
    {
        if (!$key) {
            throw new RuntimeException('EVAL_LLM_API_KEY is not set');
        }

        $system = '';
        $userParts = [];
        foreach ($messages as $m) {
            if ($m['role'] === 'system') {
                $system .= $m['content'] . "\n";
            } else {
                $userParts[] = $m['content'];
            }
        }

        $body = [
            'contents' => [[
                'role'  => 'user',
                'parts' => [['text' => implode("\n", $userParts)]],
            ]],
            'generationConfig' => ['temperature' => 0.2],
        ];
        if (trim($system) !== '') {
            $body['system_instruction'] = ['parts' => [['text' => $system]]];
        }

        $url = 'https://generativelanguage.googleapis.com/v1beta/models/'
            . rawurlencode($model) . ':generateContent?key=' . urlencode($key);

        [$st, $resp] = Http::request('POST', $url, ['json' => $body, 'timeout' => 60]);
        if ($st >= 400) {
            throw new RuntimeException("gemini HTTP $st: " . substr($resp, 0, 300));
        }
        $j = json_decode($resp, true);
        $text = $j['candidates'][0]['content']['parts'][0]['text'] ?? null;
        if ($text === null) {
            throw new RuntimeException('gemini: no content in response');
        }
        return (string) $text;
    }

    /** @param array<int,array{role:string,content:string}> $messages */
    private function callOpenAiCompatible(string $base, string $model, array $messages, ?string $key): string
    {
        $headers = [];
        if ($key) {
            $headers['Authorization'] = 'Bearer ' . $key;
        }
        $body = ['model' => $model, 'messages' => $messages, 'temperature' => 0.2];

        [$st, $resp] = Http::request('POST', rtrim($base, '/') . '/chat/completions', [
            'headers' => $headers,
            'json'    => $body,
            'timeout' => 60,
        ]);
        if ($st >= 400) {
            throw new RuntimeException("llm HTTP $st: " . substr($resp, 0, 300));
        }
        $j = json_decode($resp, true);
        $text = $j['choices'][0]['message']['content'] ?? null;
        if ($text === null) {
            throw new RuntimeException('llm: no content in response');
        }
        return (string) $text;
    }
}
