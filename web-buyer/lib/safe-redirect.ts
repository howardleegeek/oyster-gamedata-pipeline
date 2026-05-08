/**
 * sanitizeNextPath — close the open-redirect surface on auth callbacks.
 *
 * See web-tester/lib/safe-redirect.ts for full rationale. Mirrored here
 * so each portal stays self-contained (no monorepo shared lib).
 */

/**
 * Validate a `next` redirect target. Accepts only same-origin paths.
 *
 * Rejected:
 *   - undefined / null / empty
 *   - protocol-relative `//host/path`
 *   - absolute URL `http://...`, `https://...`, `javascript:...`, `data:...`
 *   - backslash escapes `/\\evil.com`
 *
 * Accepted: a single `/` followed by anything not matching the above.
 */
export function sanitizeNextPath(
  raw: string | null | undefined,
  fallback: string
): string {
  if (!raw) return fallback;
  if (typeof raw !== 'string') return fallback;
  if (raw.length === 0 || raw[0] !== '/') return fallback;
  if (raw.length > 1 && (raw[1] === '/' || raw[1] === '\\')) return fallback;
  if (/^\/[a-z][a-z0-9+.-]*:/i.test(raw)) return fallback;
  return raw;
}
