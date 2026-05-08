'use client';

import Link from 'next/link';
import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';

interface Item {
  id: string;
  title: string;
  price_cents: number;
}

interface CartCheckoutButtonProps {
  items: Item[];
  signedIn: boolean;
}

/**
 * Sends the cart to /api/checkout, which returns a real Stripe Checkout
 * URL or 503 with envVars when not configured.
 *
 * Howard 2026-05-07 IRON-LAW: removed the `devMode` prop and the fake-
 * checkout banner. The previous code displayed "[DEV MODE] Checkout is
 * faked — clicking will mint a sample purchase" — that was fabricated-
 * data theatre. If Stripe isn't configured the API now returns 503 with
 * remediation envVars and we surface the error message directly.
 */
export function CartCheckoutButton({ items, signedIn }: CartCheckoutButtonProps) {
  const [licenseType, setLicenseType] = useState<'research' | 'commercial'>('commercial');
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const router = useRouter();

  async function go() {
    setError(null);
    if (items.length === 0) return;

    if (!signedIn) {
      // Bounce through /login first; the cart cookie survives the redirect.
      router.push(`/login?next=${encodeURIComponent('/cart')}`);
      return;
    }

    startTransition(async () => {
      try {
        const res = await fetch('/api/checkout', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            tarball_ids: items.map((i) => i.id),
            license_type: licenseType,
          }),
        });
        const body = await res.json();
        if (!res.ok || !body?.url) {
          if (res.status === 503 && body?.envVars) {
            throw new Error(
              `${body.error}. Set: ${(body.envVars as string[]).join(', ')}`,
            );
          }
          throw new Error(body?.error ?? `HTTP ${res.status}`);
        }
        // Real Stripe Checkout URL.
        window.location.assign(body.url as string);
      } catch (err: any) {
        setError(err.message ?? 'Checkout failed');
      }
    });
  }

  return (
    <div className="space-y-3">
      <fieldset>
        <legend className="text-xs uppercase tracking-wider text-oyster-400 font-semibold mb-2">
          License type
        </legend>
        <div className="grid grid-cols-2 gap-2">
          {(['research', 'commercial'] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setLicenseType(t)}
              className={`px-3 py-2 rounded-md text-sm border transition ${
                licenseType === t
                  ? 'bg-amber-accent text-oyster-950 border-amber-accent font-semibold'
                  : 'border-oyster-700/50 hover:bg-oyster-800/40'
              }`}
            >
              {t === 'research' ? 'Research (−40%)' : 'Commercial'}
            </button>
          ))}
        </div>
        <p className="mt-1 text-xs text-oyster-400">
          Research = academic / non-commercial only. Verified at checkout.
        </p>
      </fieldset>

      <button
        type="button"
        onClick={go}
        className="btn-primary w-full"
        disabled={pending || items.length === 0}
      >
        {pending ? 'Redirecting…' : 'Proceed to checkout'}
      </button>

      <Link href="/checkout" className="text-xs text-oyster-400 hover:text-amber-accent text-center block">
        See checkout summary
      </Link>

      {error && (
        <p className="text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded-lg p-3">
          {error}
        </p>
      )}
    </div>
  );
}
