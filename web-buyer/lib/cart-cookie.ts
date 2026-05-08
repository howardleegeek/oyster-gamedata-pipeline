/**
 * Cart cookie — used when the buyer is NOT authenticated, or in DEV MODE.
 *
 * Once the buyer signs in we persist cart_items in Postgres; before that,
 * we keep a list of tarball IDs in a single httpOnly cookie so the /cart
 * UI is functional for anonymous browsers.
 *
 * Format: JSON-encoded array of UUID strings.
 */

import { cookies } from 'next/headers';

const COOKIE_NAME = 'oyster_cart';
const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30; // 30 days

export function readCartCookie(): string[] {
  try {
    const raw = cookies().get(COOKIE_NAME)?.value;
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((s) => typeof s === 'string');
  } catch {
    return [];
  }
}

export function writeCartCookie(ids: string[]) {
  const value = JSON.stringify(Array.from(new Set(ids)));
  cookies().set({
    name: COOKIE_NAME,
    value,
    httpOnly: true,
    sameSite: 'lax',
    path: '/',
    maxAge: COOKIE_MAX_AGE_SECONDS,
  });
}

export function clearCartCookie() {
  cookies().set({ name: COOKIE_NAME, value: '', maxAge: 0, path: '/' });
}
