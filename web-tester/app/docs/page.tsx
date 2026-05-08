import Link from 'next/link';
import { env } from '../../lib/env';
import { formatCents } from '../../lib/format';

export default function DocsPage() {
  const hourly = formatCents(env.ratePerHourCents);
  const minPayout = formatCents(env.minPayoutCents);

  return (
    <div className="max-w-3xl mx-auto px-4 py-10 prose-oyster">
      <h1 className="text-4xl font-extrabold mb-2">Tester onboarding</h1>
      <p className="text-oyster-300">
        Everything you need to start earning {hourly}/hr by recording your Minecraft gameplay.
      </p>

      <nav className="card p-4 my-8 text-sm">
        <h3 className="font-semibold mb-2 text-oyster-50">Contents</h3>
        <ul className="space-y-1.5 text-oyster-300">
          <li><a href="#install"  className="hover:text-amber-accent">1. Install Minecraft</a></li>
          <li><a href="#recorder" className="hover:text-amber-accent">2. Run the recorder</a></li>
          <li><a href="#billable" className="hover:text-amber-accent">3. What counts as billable time</a></li>
          <li><a href="#payouts"  className="hover:text-amber-accent">4. Payouts</a></li>
          <li><a href="#privacy"  className="hover:text-amber-accent">5. Privacy &amp; data</a></li>
          <li><a href="#terms"    className="hover:text-amber-accent">6. Terms</a></li>
          <li><a href="#support"  className="hover:text-amber-accent">7. Support</a></li>
        </ul>
      </nav>

      {/* 1 */}
      <section id="install" className="space-y-3">
        <h2 className="text-2xl font-bold mt-10">1. Install Minecraft</h2>
        <p>
          OysterRecorder works with <strong>Minecraft: Java Edition</strong> on Windows 10/11.
          We don&apos;t support Bedrock Edition or console versions yet.
        </p>
        <ul className="list-disc list-inside space-y-1 text-oyster-300">
          <li>Install the official launcher from <code>minecraft.net</code>.</li>
          <li>Launch any world (vanilla or modded) and confirm it runs at ≥ 60 FPS.</li>
          <li>
            On the first run, Windows may ask for permission to access GPU performance counters.
            Allow it — the recorder uses them to detect AFK frames.
          </li>
        </ul>
      </section>

      {/* 2 */}
      <section id="recorder" className="space-y-3">
        <h2 className="text-2xl font-bold mt-10">2. Run the recorder</h2>
        <p>
          From your <Link href="/dashboard" className="text-amber-accent hover:underline">dashboard</Link>,
          download the build with your tester ID in the filename. Run the .exe, sign in once, and start playing.
        </p>
        <p>The recorder UI shows a live indicator:</p>
        <ul className="list-disc list-inside space-y-1 text-oyster-300">
          <li><strong>Green dot</strong> — recording, billable</li>
          <li><strong>Yellow dot</strong> — recording, not billable (idle or menu)</li>
          <li><strong>Grey dot</strong> — paused (you pressed pause, or Minecraft lost focus)</li>
          <li><strong>Blue dot</strong> — uploading a finished tarball in the background</li>
        </ul>
      </section>

      {/* 3 */}
      <section id="billable" className="space-y-3">
        <h2 className="text-2xl font-bold mt-10">3. What counts as billable time</h2>
        <p>Billable means: you are actively interacting with the world.</p>
        <table className="w-full text-sm card my-4">
          <thead className="bg-oyster-950/40">
            <tr>
              <th className="text-left  px-4 py-2">Counts</th>
              <th className="text-left  px-4 py-2">Doesn&apos;t count</th>
            </tr>
          </thead>
          <tbody className="text-oyster-300 [&_td]:px-4 [&_td]:py-2 [&_td]:align-top divide-y divide-oyster-800/40">
            <tr>
              <td>Walking, mining, building, exploring</td>
              <td>Standing still &gt; 60s</td>
            </tr>
            <tr>
              <td>Combat, redstone, crafting</td>
              <td>Inventory or pause menu &gt; 60s</td>
            </tr>
            <tr>
              <td>Multiplayer interaction</td>
              <td>Minecraft minimised / unfocused</td>
            </tr>
          </tbody>
        </table>
        <p>
          Each tarball is reviewed by our quality model (D5). Verdict <span className="tag tag-accepted">accepted</span>{' '}
          → counts toward earnings. <span className="tag tag-rejected">rejected</span> → doesn&apos;t count, but you can dispute via support.
        </p>
      </section>

      {/* 4 */}
      <section id="payouts" className="space-y-3">
        <h2 className="text-2xl font-bold mt-10">4. Payouts</h2>
        <p>
          Rate: <strong>{hourly}/hr</strong> of accepted gameplay. Payouts run weekly via Stripe Connect, with a
          minimum of <strong>{minPayout}</strong>. Anything below the minimum rolls into the next week.
        </p>
        <p>
          One-time setup on the{' '}
          <Link href="/payouts" className="text-amber-accent hover:underline">/payouts</Link>{' '}
          page links your bank account or debit card.
        </p>
      </section>

      {/* 5 */}
      <section id="privacy" className="space-y-3">
        <h2 className="text-2xl font-bold mt-10">5. Privacy &amp; data</h2>
        <ul className="list-disc list-inside space-y-1 text-oyster-300">
          <li>The recorder only captures the Minecraft window — no other apps, desktop, microphone, or camera.</li>
          <li>Your recordings are used to train AI world models. We never sell raw footage to third parties.</li>
          <li>You can request deletion of all your data at any time by emailing <code>gamedata@oyster.example</code>.</li>
          <li>We don&apos;t store your Minecraft account credentials. The recorder only reads window pixels.</li>
        </ul>
      </section>

      {/* 6 */}
      <section id="terms" className="space-y-3">
        <h2 className="text-2xl font-bold mt-10">6. Terms</h2>
        <ul className="list-disc list-inside space-y-1 text-oyster-300">
          <li>You must be 18 or older to participate.</li>
          <li>One account per person. Sock-puppet accounts are banned and forfeit pending earnings.</li>
          <li>You retain copyright over your gameplay; you grant Oyster Labs a perpetual non-exclusive license to use it for AI training.</li>
          <li>We reserve the right to reject low-quality, AFK, or scripted submissions.</li>
        </ul>
      </section>

      {/* 7 */}
      <section id="support" className="space-y-3">
        <h2 className="text-2xl font-bold mt-10">7. Support</h2>
        <p>
          Questions, disputes, or bugs:{' '}
          <a href="mailto:gamedata@oyster.example" className="text-amber-accent hover:underline">
            gamedata@oyster.example
          </a>
          . We aim to respond within one business day.
        </p>
      </section>
    </div>
  );
}
