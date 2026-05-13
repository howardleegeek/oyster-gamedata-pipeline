/**
 * Structured JSON-line logger for production routes.
 *
 * Howard 2026-05-08: Stage-1 observability. Every line is a single JSON
 * object on stdout — Vercel + GCP + Logflare + Sentry all ingest this
 * shape natively, so dropping in any aggregator later is zero-code.
 *
 * Iron-law: log only real fields. Never invent latency / status / counts.
 */

export interface LogFields {
  // Free-form structured payload — anything JSON-serializable.
  [k: string]: unknown;
}

const LEVELS = ['debug', 'info', 'warn', 'error'] as const;
type Level = (typeof LEVELS)[number];

function emit(level: Level, event: string, fields: LogFields): void {
  // Stable ordering: ts, level, event, then user fields. Easier to grep.
  const line = JSON.stringify({
    ts: new Date().toISOString(),
    level,
    event,
    ...fields,
  });
  // eslint-disable-next-line no-console
  if (level === 'error' || level === 'warn') {
    console.error(line);
  } else {
    console.log(line);
  }
}

export const log = {
  debug: (event: string, fields: LogFields = {}) => emit('debug', event, fields),
  info: (event: string, fields: LogFields = {}) => emit('info', event, fields),
  warn: (event: string, fields: LogFields = {}) => emit('warn', event, fields),
  error: (event: string, fields: LogFields = {}) => emit('error', event, fields),
};
