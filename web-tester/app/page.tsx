import Link from 'next/link';
import { env } from '../lib/env';
import { formatCents } from '../lib/format';

export default function LandingPage() {
  const hourly = formatCents(env.ratePerHourCents);

  return (
    <div className="hero-bg">
      {/* Hero */}
      <section className="max-w-6xl mx-auto px-4 py-20 md:py-28">
        <div className="max-w-3xl">
          <span className="inline-block tag bg-amber-accent/15 text-amber-accent mb-5">
            Now paying testers
          </span>
          <h1 className="text-4xl md:text-6xl font-extrabold tracking-tight leading-tight">
            Earn{' '}
            <span className="text-amber-accent">{hourly}/hr</span>{' '}
            by recording your Minecraft gameplay.
          </h1>
          <p className="mt-6 text-lg text-oyster-200 max-w-2xl">
            Real data, real money. Your gameplay trains the next generation of AI
            world models. Install our recorder, play normally, and we&apos;ll pay
            you for every billable hour.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/signup" className="btn-primary">
              Sign up to start earning
            </Link>
            <Link href="/docs" className="btn-secondary">
              How it works
            </Link>
          </div>
        </div>

        {/* Stat strip */}
        <div className="mt-16 grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="card p-5">
            <div className="text-3xl font-bold text-amber-accent">{hourly}</div>
            <div className="text-xs uppercase tracking-wider text-oyster-400 mt-1">
              base rate
            </div>
          </div>
          <div className="card p-5">
            <div className="text-3xl font-bold">5 GB</div>
            <div className="text-xs uppercase tracking-wider text-oyster-400 mt-1">
              avg per session
            </div>
          </div>
          <div className="card p-5">
            <div className="text-3xl font-bold">~30 min</div>
            <div className="text-xs uppercase tracking-wider text-oyster-400 mt-1">
              first payout
            </div>
          </div>
          <div className="card p-5">
            <div className="text-3xl font-bold">Windows</div>
            <div className="text-xs uppercase tracking-wider text-oyster-400 mt-1">
              recorder client
            </div>
          </div>
        </div>
      </section>

      {/* How it works */}
      <section className="max-w-6xl mx-auto px-4 py-12 md:py-20 border-t border-oyster-800/40">
        <h2 className="text-3xl font-bold mb-10">How it works</h2>
        <div className="grid md:grid-cols-3 gap-6">
          {[
            {
              n: '1',
              title: 'Sign up',
              body:
                'Create an account with email or GitHub. We use your account to attribute uploads and pay you out.',
            },
            {
              n: '2',
              title: 'Install the recorder',
              body:
                'Download OysterRecorder.exe from your dashboard. Your unique tester ID is baked into the filename so we know which uploads are yours.',
            },
            {
              n: '3',
              title: 'Play and get paid',
              body:
                'Run Minecraft as you normally would. The recorder captures gameplay locally, packages it as a tarball, and uploads it. We pay out to your bank weekly.',
            },
          ].map((s) => (
            <div key={s.n} className="card p-6">
              <div className="w-10 h-10 rounded-full bg-amber-accent text-oyster-950 font-black grid place-items-center mb-4">
                {s.n}
              </div>
              <h3 className="text-xl font-semibold mb-2">{s.title}</h3>
              <p className="text-oyster-300 text-sm leading-relaxed">{s.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* FAQ */}
      <section className="max-w-4xl mx-auto px-4 py-12 md:py-20">
        <h2 className="text-3xl font-bold mb-8">FAQ</h2>
        <div className="space-y-4">
          {[
            {
              q: 'What counts as billable time?',
              a:
                'Active gameplay where you are interacting with the world — moving, building, mining, exploring. AFK time and menus are not billable. Our quality model (D5) reviews each upload and assigns a verdict.',
            },
            {
              q: 'How much storage / bandwidth will this use?',
              a:
                'Roughly 5 GB per hour of gameplay, uploaded in the background. The recorder is bandwidth-aware and pauses uploads on metered networks by default.',
            },
            {
              q: 'How do I get paid?',
              a:
                'Once you cross the minimum payout threshold, we wire payment via Stripe Connect. Stripe Connect onboarding (linking your bank or debit card) is a one-time step on /payouts.',
            },
            {
              q: 'Is my data private?',
              a:
                'Recordings only capture the Minecraft window — no other apps, no microphone, no webcam. We never sell or republish raw footage. See the docs page for the full privacy policy.',
            },
          ].map((f) => (
            <details key={f.q} className="card p-5 group">
              <summary className="font-semibold cursor-pointer flex justify-between items-center list-none">
                {f.q}
                <span className="text-amber-accent transition-transform group-open:rotate-45">+</span>
              </summary>
              <p className="mt-3 text-oyster-300 text-sm leading-relaxed">{f.a}</p>
            </details>
          ))}
        </div>
        <div className="mt-12 text-center">
          <Link href="/signup" className="btn-primary">
            Sign up — earn {hourly}/hr
          </Link>
        </div>
      </section>
    </div>
  );
}
