'use client';

import { useState } from 'react';

/**
 * Posts to a Stripe Connect endpoint and redirects the browser to the
 * `url` returned in the JSON body. Used by /payouts for the three flows:
 *   - "Connect bank account"  → POST /api/stripe/connect/onboard
 *   - "Finish setup"          → POST /api/stripe/connect/onboard (resumes)
 *   - "Manage on Stripe"      → POST /api/stripe/connect/dashboard
 */
export function StripeConnectButton(props: {
  endpoint: string;
  label: string;
  variant?: 'primary' | 'ghost';
  className?: string;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const cls =
    props.variant === 'ghost'
      ? `btn-ghost ${props.className ?? ''}`
      : `btn-primary ${props.className ?? ''}`;

  async function onClick() {
    setPending(true);
    setError(null);
    try {
      const res = await fetch(props.endpoint, { method: 'POST' });
      const json = (await res.json()) as { url?: string; error?: string };
      if (!res.ok || !json.url) {
        setError(json.error ?? `HTTP ${res.status}`);
        setPending(false);
        return;
      }
      window.location.assign(json.url);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <button className={cls} onClick={onClick} disabled={pending}>
        {pending ? 'Redirecting…' : props.label}
      </button>
      {error && <span className="text-xs text-red-400">{error}</span>}
    </div>
  );
}
