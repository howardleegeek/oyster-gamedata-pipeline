'use client';

import { useState, FormEvent } from 'react';
import Link from 'next/link';
import { getSupabaseBrowserClient } from '../../lib/supabase-browser';
import { isSupabaseConfigured, env } from '../../lib/env';

export default function SignupPage() {
  const supabase = getSupabaseBrowserClient();
  const [email, setEmail] = useState('');
  const [company, setCompany] = useState('');
  const [status, setStatus] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle');
  const [error, setError] = useState('');

  async function handleEmail(e: FormEvent) {
    e.preventDefault();
    setStatus('sending');
    setError('');
    if (!supabase) {
      setStatus('error');
      setError('Supabase is not configured (DEV MODE).');
      return;
    }
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: {
        emailRedirectTo: `${env.siteUrl}/auth/callback?next=/browse`,
        shouldCreateUser: true,
        data: { company_name: company || null },
      },
    });
    if (error) {
      setStatus('error');
      setError(error.message);
    } else {
      setStatus('sent');
    }
  }

  async function handleGitHub() {
    if (!supabase) return;
    await supabase.auth.signInWithOAuth({
      provider: 'github',
      options: { redirectTo: `${env.siteUrl}/auth/callback?next=/browse` },
    });
  }

  return (
    <div className="max-w-2xl mx-auto px-4 py-16">
      <h1 className="text-4xl font-extrabold mb-3">Create a buyer account</h1>
      <p className="text-oyster-300 mb-10">
        License real Minecraft gameplay tarballs at{' '}
        <span className="text-amber-accent font-semibold">
          ${(env.pricePerGbCents / 100).toFixed(2)}/GB
        </span>
        . Academic researchers get {env.researchDiscountPct}% off — verified at checkout.
      </p>

      <div className="grid md:grid-cols-5 gap-8">
        {/* Left: form */}
        <div className="md:col-span-3 card p-6">
          <button
            onClick={handleGitHub}
            className="btn-secondary w-full mb-5"
            disabled={!isSupabaseConfigured()}
          >
            Sign up with GitHub
          </button>

          <div className="flex items-center gap-3 mb-5 text-xs text-oyster-400 uppercase tracking-wider">
            <div className="flex-1 border-t border-oyster-800" />
            or email
            <div className="flex-1 border-t border-oyster-800" />
          </div>

          <form onSubmit={handleEmail} className="space-y-3">
            <label className="block text-sm font-medium text-oyster-200">
              Work email
              <input
                type="email"
                required
                placeholder="you@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="input mt-1"
                disabled={status === 'sending' || status === 'sent'}
              />
            </label>
            <label className="block text-sm font-medium text-oyster-200">
              Company / lab (optional)
              <input
                type="text"
                placeholder="Acme AI Research"
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                className="input mt-1"
                disabled={status === 'sending' || status === 'sent'}
              />
            </label>
            <button
              type="submit"
              className="btn-primary w-full"
              disabled={status === 'sending' || status === 'sent'}
            >
              {status === 'sending' && 'Sending…'}
              {status === 'sent' && 'Check your inbox ✓'}
              {(status === 'idle' || status === 'error') && 'Send sign-up link'}
            </button>
          </form>

          {status === 'sent' && (
            <p className="mt-4 text-sm text-oyster-200 bg-oyster-800/40 border border-oyster-700/50 rounded-lg p-3">
              We sent a confirmation link to <strong>{email}</strong>. Click it to finish.
            </p>
          )}
          {status === 'error' && (
            <p className="mt-4 text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded-lg p-3">
              {error}
            </p>
          )}

          <p className="mt-6 text-xs text-oyster-400">
            By signing up you agree to the{' '}
            <Link href="/licenses" className="hover:text-oyster-100 underline">
              license terms
            </Link>{' '}
            for any tarballs you purchase.
          </p>
        </div>

        {/* Right: pitch */}
        <aside className="md:col-span-2 space-y-4 text-sm text-oyster-300">
          <div className="card p-5">
            <h3 className="font-semibold text-oyster-50 mb-2">What you get</h3>
            <ul className="space-y-1.5 list-disc list-inside marker:text-amber-accent">
              <li>Per-tarball Stripe checkout</li>
              <li>Signed S3 download URLs (24h)</li>
              <li>Per-purchase license certificates</li>
              <li>Free 30-second + JSON sample</li>
            </ul>
          </div>
          <div className="card p-5">
            <h3 className="font-semibold text-oyster-50 mb-2">Already have an account?</h3>
            <Link href="/login" className="text-amber-accent hover:underline">
              Sign in →
            </Link>
          </div>
          <div className="card p-5">
            <h3 className="font-semibold text-oyster-50 mb-2">Just browsing?</h3>
            <Link href="/browse" className="text-amber-accent hover:underline">
              Open the catalog →
            </Link>
            <p className="mt-2 text-xs text-oyster-400">
              No account needed to inspect samples.
            </p>
          </div>
        </aside>
      </div>
    </div>
  );
}
