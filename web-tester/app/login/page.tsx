'use client';

import { useState, FormEvent, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { getSupabaseBrowserClient } from '../../lib/supabase-browser';
import { isSupabaseConfigured, env } from '../../lib/env';
import { NotConfigured } from '../../components/NotConfigured';

function LoginInner() {
  const params = useSearchParams();
  const next = params.get('next') ?? '/dashboard';

  const [email, setEmail] = useState('');
  const [status, setStatus] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle');
  const [error, setError] = useState('');

  const supabase = getSupabaseBrowserClient();

  // Howard 2026-05-07 IRON-LAW: hard-gate. Previous code surfaced "DEV
  // MODE: Auth is disabled — visit /dashboard directly to see the sample
  // UI" — pointing testers at fabricated stats. Now: render NotConfigured.
  if (!isSupabaseConfigured()) {
    return (
      <NotConfigured
        service="Supabase"
        envVars={['NEXT_PUBLIC_SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_ANON_KEY']}
        docsUrl="/docs#supabase-setup"
      />
    );
  }

  async function handleMagicLink(e: FormEvent) {
    e.preventDefault();
    setStatus('sending');
    setError('');

    if (!supabase) {
      setStatus('error');
      setError('Supabase browser client unavailable — check your config.');
      return;
    }
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: `${env.siteUrl}/auth/callback?next=${encodeURIComponent(next)}` },
    });
    if (error) {
      setStatus('error');
      setError(error.message);
    } else {
      setStatus('sent');
    }
  }

  async function handleGitHub() {
    if (!supabase) {
      setError('Supabase browser client unavailable — check your config.');
      return;
    }
    await supabase.auth.signInWithOAuth({
      provider: 'github',
      options: { redirectTo: `${env.siteUrl}/auth/callback?next=${encodeURIComponent(next)}` },
    });
  }

  return (
    <div className="max-w-md mx-auto px-4 py-16">
      <h1 className="text-3xl font-bold mb-2">Sign in</h1>
      <p className="text-oyster-300 text-sm mb-8">
        New here?{' '}
        <Link href="/signup" className="text-amber-accent hover:underline">
          Create an account
        </Link>
        .
      </p>

      <button onClick={handleGitHub} className="btn-secondary w-full mb-6">
        <svg viewBox="0 0 24 24" className="w-5 h-5" fill="currentColor" aria-hidden>
          <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.1.79-.25.79-.55v-2.13c-3.2.7-3.87-1.36-3.87-1.36-.52-1.32-1.27-1.67-1.27-1.67-1.04-.71.08-.69.08-.69 1.15.08 1.76 1.18 1.76 1.18 1.02 1.76 2.69 1.25 3.34.96.1-.74.4-1.25.72-1.54-2.55-.29-5.24-1.27-5.24-5.66 0-1.25.45-2.27 1.18-3.07-.12-.29-.51-1.45.11-3.02 0 0 .96-.31 3.15 1.17.91-.25 1.89-.38 2.86-.38.97 0 1.95.13 2.86.38 2.18-1.48 3.14-1.17 3.14-1.17.62 1.57.23 2.73.11 3.02.73.8 1.18 1.82 1.18 3.07 0 4.4-2.69 5.36-5.25 5.65.41.36.78 1.07.78 2.16v3.21c0 .31.2.66.79.55C20.71 21.39 24 17.08 24 12 24 5.65 18.85.5 12.5.5h-.5z" />
        </svg>
        Continue with GitHub
      </button>

      <div className="flex items-center gap-3 mb-6 text-xs text-oyster-400 uppercase tracking-wider">
        <div className="flex-1 border-t border-oyster-800" />
        or email magic link
        <div className="flex-1 border-t border-oyster-800" />
      </div>

      <form onSubmit={handleMagicLink} className="space-y-3">
        <input
          type="email"
          required
          placeholder="you@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="input"
          disabled={status === 'sending' || status === 'sent'}
        />
        <button type="submit" className="btn-primary w-full" disabled={status === 'sending' || status === 'sent'}>
          {status === 'sending' && 'Sending…'}
          {status === 'sent' && 'Check your email ✓'}
          {(status === 'idle' || status === 'error') && 'Send magic link'}
        </button>
      </form>

      {status === 'sent' && (
        <p className="mt-4 text-sm text-oyster-200 bg-oyster-800/40 border border-oyster-700/50 rounded-lg p-3">
          We sent a sign-in link to <strong>{email}</strong>. Click it to finish signing in.
        </p>
      )}
      {status === 'error' && error && (
        <p className="mt-4 text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded-lg p-3">
          {error}
        </p>
      )}
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="max-w-md mx-auto px-4 py-16">Loading…</div>}>
      <LoginInner />
    </Suspense>
  );
}
