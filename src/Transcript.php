<?php
declare(strict_types=1);

/**
 * Turn a Dograh run's "logs" into individual chat messages.
 *
 * Dograh stores a call's conversation under run["logs"]["realtime_feedback_events"]:
 *   * rtf-user-transcription -> candidate speech (only `final: true` is settled)
 *   * rtf-bot-text           -> agent speech, emitted in chunks to be coalesced
 */
final class Transcript
{
    private const USER_EVENT = 'rtf-user-transcription';
    private const BOT_EVENT  = 'rtf-bot-text';

    /**
     * @param array<string,mixed> $run
     * @return array<int,array<string,mixed>>
     */
    private static function eventsFromRun(array $run): array
    {
        $logs = $run['logs'] ?? [];
        if (is_array($logs)) {
            if (array_key_exists('realtime_feedback_events', $logs)) {
                return $logs['realtime_feedback_events'] ?? [];
            }
            // tolerate a bare list of events
            if ($logs !== [] && array_is_list($logs)) {
                return $logs;
            }
        }
        return [];
    }

    /**
     * @param array<int,array<string,mixed>> $events
     * @return array<int,array{role:string,text:string,timestamp:mixed}>
     */
    public static function messagesFromEvents(array $events): array
    {
        $messages = [];
        foreach ($events as $event) {
            $etype = $event['type'] ?? null;
            $payload = $event['payload'] ?? [];
            $ts = $payload['timestamp'] ?? ($event['timestamp'] ?? null);

            if ($etype === self::USER_EVENT && ($payload['final'] ?? null) === true) {
                $text = trim((string) ($payload['text'] ?? ''));
                if ($text !== '') {
                    $messages[] = ['role' => 'user', 'text' => $text, 'timestamp' => $ts];
                }
            } elseif ($etype === self::BOT_EVENT) {
                $text = (string) ($payload['text'] ?? '');
                $n = count($messages);
                if ($n > 0 && $messages[$n - 1]['role'] === 'assistant') {
                    $messages[$n - 1]['text'] .= $text;
                } else {
                    $messages[] = ['role' => 'assistant', 'text' => $text, 'timestamp' => $ts];
                }
            }
        }

        $clean = [];
        foreach ($messages as $m) {
            $m['text'] = trim($m['text']);
            if ($m['text'] !== '') {
                $clean[] = $m;
            }
        }
        return $clean;
    }

    /**
     * @param array<string,mixed> $run
     * @return array<int,array<string,mixed>>
     */
    public static function messagesFromRun(array $run): array
    {
        return self::messagesFromEvents(self::eventsFromRun($run));
    }

    /** @param array<int,array<string,mixed>> $messages */
    public static function transcriptText(array $messages): string
    {
        return implode("\n", array_map(fn ($m) => $m['role'] . ': ' . $m['text'], $messages));
    }
}
