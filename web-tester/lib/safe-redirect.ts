/**
 * sanitizeNextPath — close the open-redirect surface on auth callbacks.
 *
 * Howard 2026-05-08: A `?next=` query param is a classic phishing vector.
 * If the user clicks `https://tester.example.com/auth/callback?next=//evil.com`
 * and we redirect to `${origin}${next}`, browsers interpret `//evil.com`
 * as protocol-relative — the user lands on evil.com after we log them in
 * here, where the attacker can immediately phish their session.
 *
 * This helper accepts only paths that start with a single `/` and contain
 * no protocol. Anything else falls back to the supplied default.
 *
 * Iron-law: this is a real security boundary, not theatre. Tests live in
 * web-tester/lib/__tests__ if/when added.
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
  // Must start with exactly one slash.
  if (raw.length === 0 || raw[0] !== '/') return fallback;
  // Reject protocol-relative ("//host/path") and backslash escapes.
  if (raw.length > 1 && (raw[1] === '/' || raw[1] === '\\')) return fallback;
  // Reject any URL-like prefix sneaking past the leading `/`. The earlier
  // check makes this redundant for well-formed inputs, but defense-in-depth.
  if (/^\/[a-z][a-z0-9+.-]*:/i.test(raw)) return fallback;
  return raw;
}
