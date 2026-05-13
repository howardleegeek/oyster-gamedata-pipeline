/**
 * Shared error-report scrub + dedup helpers.
 *
 * Mirrored from bin/error_report_service.py so the algorithm is identical
 * between the Python ingest worker and the Next.js POST route. If you
 * change PII patterns or the fingerprint shape here, change them in the
 * Python module too — otherwise the same crash will produce different
 * fingerprints depending on which path it travelled.
 *
 * Iron-law: no relaxation of the scrub on either side. Tests assert
 * round-trip equivalence between Python and TS for representative
 * traces (see tests/test_error_report.py::test_pii_scrub_parity).
 */

import { z } from 'zod';
import crypto from 'node:crypto';

export const MAX_STACK_BYTES = 16 * 1024;
export const MAX_CONTEXT_BYTES = 4 * 1024;
export const MAX_OS_LEN = 64;
export const MAX_VERSION_LEN = 64;
export const MAX_ANON_ID_LEN = 64;
export const ALLOWED_SEVERITIES = ['crash', 'error', 'warn', 'info'] as const;
export type Severity = (typeof ALLOWED_SEVERITIES)[number];

const VERSION_RE = /^v?\d+\.\d+\.\d+(?:-[A-Za-z0-9.\-]+)?$/;
const ANON_ID_RE = /^[A-Za-z0-9._\-]{1,64}$/;
const OS_RE = /^[A-Za-z0-9 .\-_/():+]+$/;

export const ReportSchema = z.object({
  recorder_version: z.string().min(1).max(MAX_VERSION_LEN).regex(VERSION_RE),
  os: z.string().min(1).max(MAX_OS_LEN).regex(OS_RE),
  stack_trace: z
    .string()
    .min(1)
    .refine(
      (s) => Buffer.byteLength(s, 'utf-8') <= MAX_STACK_BYTES,
      `stack_trace must be at most ${MAX_STACK_BYTES} bytes`
    ),
  context: z
    .record(z.any())
    .optional()
    .default({})
    .refine(
      (c) => Buffer.byteLength(JSON.stringify(c ?? {}), 'utf-8') <= MAX_CONTEXT_BYTES,
      `context must be at most ${MAX_CONTEXT_BYTES} bytes`
    ),
  anon_id: z.string().max(MAX_ANON_ID_LEN).regex(ANON_ID_RE).optional(),
  severity: z.enum(ALLOWED_SEVERITIES).optional().default('crash'),
});

export type ValidatedReport = z.infer<typeof ReportSchema>;

// ---------------------------------------------------------------------------
// PII scrub — order-sensitive (matches Python module exactly)
// ---------------------------------------------------------------------------

const WIN_USER_RE = /([A-Za-z]:[\\/]+Users[\\/]+)([^\\/\s"']+)/gi;
const UNIX_HOME_RE = /(\/(?:Users|home)\/)([^/\s"']+)/g;
// Negative lookahead skips drive paths already redacted by WIN_USER_RE.
const WIN_ABS_RE = /\b[A-Za-z]:\\(?![Uu]sers\\)[^\s"'`]+/g;
const UNIX_ABS_RE = /(?<![A-Za-z])(\/(?:tmp|var|opt|private|mnt|data)\/[^\s"'`,)]+)/g;
const IPV4_RE = /\b(?:\d{1,3}\.){3}\d{1,3}\b/g;
const EMAIL_RE = /\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b/g;
const APPDATA_RE = /\\AppData\\(Local|Roaming)\\/gi;

export function scrubPii(blob: string): string {
  if (typeof blob !== 'string') return blob;
  let s = blob;
  s = s.replace(WIN_USER_RE, '$1<USER>');
  s = s.replace(UNIX_HOME_RE, '$1<USER>');
  s = s.replace(WIN_ABS_RE, '<PATH>');
  s = s.replace(UNIX_ABS_RE, '<PATH>');
  // Canonicalise AppData casing (Title-case) so crashes from different
  // locales hash identically.
  s = s.replace(APPDATA_RE, (_match, sub: string) => {
    const title = sub.charAt(0).toUpperCase() + sub.slice(1).toLowerCase();
    return `\\AppData\\${title}\\`;
  });
  s = s.replace(IPV4_RE, '<IP>');
  s = s.replace(EMAIL_RE, '<EMAIL>');
  return s;
}

export function scrubContext(ctx: Record<string, unknown> | undefined): Record<string, unknown> {
  if (!ctx || typeof ctx !== 'object') return {};
  const out: Record<string, unknown> = {};
  for (const [rawKey, v] of Object.entries(ctx)) {
    const k = rawKey.length > 64 ? rawKey.slice(0, 64) : rawKey;
    if (typeof v === 'string') {
      out[k] = scrubPii(v.slice(0, 1024));
    } else if (typeof v === 'number' || typeof v === 'boolean' || v === null) {
      out[k] = v;
    } else if (Array.isArray(v)) {
      out[k] = v.slice(0, 32).map((item) =>
        typeof item === 'string' ? scrubPii(item.slice(0, 1024)) : item
      );
    } else if (typeof v === 'object') {
      out[k] = scrubContext(v as Record<string, unknown>);
    } else {
      out[k] = String(v).slice(0, 512);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Fingerprint
// ---------------------------------------------------------------------------

const PATCH_SUFFIX_RE = /^(v?\d+\.\d+\.\d+(?:-[A-Za-z]+\d+)?)(?:\.[0-9]+)*$/;

function normaliseVersionForFp(v: string): string {
  const trimmed = v.trim();
  const m = trimmed.match(PATCH_SUFFIX_RE);
  return m ? m[1] : trimmed;
}

function normaliseOsForFp(osStr: string): string {
  const s = (osStr || '').trim().toLowerCase();
  if (s.startsWith('windows') || s.includes('nt-') || s.startsWith('win')) return 'windows';
  if (s.startsWith('darwin') || s.startsWith('macos') || s.startsWith('mac')) return 'macos';
  if (s.startsWith('linux') || s.startsWith('ubuntu') || s.startsWith('debian')) return 'linux';
  return s.slice(0, 32) || 'unknown';
}

export function fingerprintStack(
  scrubbedStack: string,
  osStr: string,
  recorderVersion: string
): string {
  const base = [
    normaliseOsForFp(osStr),
    normaliseVersionForFp(recorderVersion),
    scrubbedStack.trim(),
  ].join('|');
  return crypto.createHash('sha256').update(base, 'utf-8').digest('hex').slice(0, 32);
}

// ---------------------------------------------------------------------------
// `since=24h` parser
// ---------------------------------------------------------------------------

const SINCE_RE = /^(\d+)([smhd])$/;

export function parseSince(spec: string | null | undefined, now: Date = new Date()): Date | null {
  if (!spec) return null;
  const m = spec.trim().toLowerCase().match(SINCE_RE);
  if (!m) return null;
  const n = Number(m[1]);
  const secondsPer: Record<string, number> = { s: 1, m: 60, h: 3600, d: 86400 };
  const seconds = n * secondsPer[m[2]];
  return new Date(now.getTime() - seconds * 1000);
}
