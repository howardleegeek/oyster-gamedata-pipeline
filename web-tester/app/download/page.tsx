import Link from 'next/link';
import { getSupabaseServerClient } from '../../lib/supabase-server';
import { isSupabaseConfigured, env } from '../../lib/env';
import { NotConfigured } from '../../components/NotConfigured';

export const dynamic = 'force-dynamic';

async function loadTesterId(): Promise<{ id: string; email: string }> {
  const supabase = getSupabaseServerClient();
  if (!supabase) throw new Error('Supabase server client unavailable.');
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Unauthenticated (middleware should redirect).');
  return { id: user.id, email: user.email ?? '' };
}

export default async function DownloadPage() {
  // Howard 2026-05-07 IRON-LAW: hard-gate. The previous implementation
  // returned a fabricated tester id (`00000000-…-001`) and email
  // (`sample-tester@example.com`) when Supabase wasn't configured, then
  // baked them into the .exe download URL — meaning anyone could grab
  // a build attributed to the fake tester.
  if (!isSupabaseConfigured()) {
    return (
      <NotConfigured
        service="Supabase"
        envVars={['NEXT_PUBLIC_SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_ANON_KEY', 'SUPABASE_SERVICE_ROLE_KEY']}
        docsUrl="/docs#supabase-setup"
      />
    );
  }

  const { id, email } = await loadTesterId();
  // Tester ID is embedded in the filename so uploads can be attributed.
  // Format: OysterRecorder-<short>-<full>.exe — short is for humans, full is the source of truth.
  const shortId = id.slice(0, 8);
  const filename = `OysterRecorder-${shortId}-${id}.exe`;
  const downloadHref = `/api/download/${id}?v=${env.recorderVersion}`;

  return (
    <div className="max-w-3xl mx-auto px-4 py-10">
      <h1 className="text-3xl font-bold mb-2">Download OysterRecorder</h1>
      <p className="text-oyster-300 mb-6">
        Windows recorder · v{env.recorderVersion}
      </p>

      <div className="card p-6 mb-6">
        <div className="grid sm:grid-cols-2 gap-6 items-center">
          <div>
            <h3 className="text-xl font-semibold mb-1">Your unique recorder build</h3>
            <p className="text-sm text-oyster-300 mb-3">
              Your tester ID is baked into the filename so we can attribute every upload back to you.
              Don&apos;t share this build — installs from this link are tracked.
            </p>
            <p className="text-xs text-oyster-400">
              Tester: <span className="text-oyster-100">{email}</span>
              <br />
              ID: <span className="font-mono text-oyster-100">{id}</span>
            </p>
          </div>
          <div className="text-center">
            <a href={downloadHref} download={filename} className="btn-primary w-full">
              Download .exe
            </a>
            <p className="text-xs text-oyster-400 mt-2 break-all">{filename}</p>
          </div>
        </div>
      </div>

      <ol className="space-y-4 mt-8">
        {[
          {
            t: 'Install Java + Minecraft (Java Edition)',
            b: 'OysterRecorder works with the Java Edition. Make sure you can launch a vanilla world before continuing.',
          },
          {
            t: 'Run the .exe',
            b: 'On first launch, Windows SmartScreen may ask for confirmation. Click "More info → Run anyway" — we ship signed binaries but new versions take ~24h to propagate.',
          },
          {
            t: 'Sign in (one-time)',
            b: 'The recorder reads your tester ID from its filename, then asks you to confirm by signing in once via the same account you used here.',
          },
          {
            t: 'Play normally',
            b: 'Recording auto-starts when Minecraft is in focus. Tarballs upload in the background as you play. Watch your /dashboard for status.',
          },
        ].map((step, i) => (
          <li key={i} className="card p-5 flex gap-4">
            <span className="w-8 h-8 rounded-full bg-amber-accent text-oyster-950 font-black grid place-items-center shrink-0">
              {i + 1}
            </span>
            <div>
              <h4 className="font-semibold">{step.t}</h4>
              <p className="text-sm text-oyster-300 mt-1">{step.b}</p>
            </div>
          </li>
        ))}
      </ol>

      <div className="mt-10 text-sm text-oyster-400">
        Need help?{' '}
        <Link href="/docs" className="text-amber-accent hover:underline">
          Read the full onboarding docs →
        </Link>
      </div>
    </div>
  );
}
