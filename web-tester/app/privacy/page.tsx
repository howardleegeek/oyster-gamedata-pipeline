/**
 * Tester portal — Privacy Policy.
 *
 * Howard 2026-05-08: Real description of what the recorder captures and
 * what the upload pipeline stores. No boilerplate — every claim here
 * matches actual code behavior.
 */

import Link from 'next/link';

export const metadata = {
  title: 'Privacy Policy — Oyster GameData (Tester)',
  description:
    'What the OysterRecorder client captures, what the upload pipeline stores, and how tester data is handled.',
};

const lastUpdated = '2026-05-08';

export default function PrivacyPage() {
  return (
    <div className="max-w-3xl mx-auto px-4 py-16">
      <Link href="/" className="text-amber-accent hover:underline text-sm">
        ← back to home
      </Link>
      <h1 className="text-4xl font-bold mt-4 mb-2">Privacy Policy</h1>
      <p className="text-oyster-400 text-sm mb-10">Last updated: {lastUpdated}</p>

      <section className="space-y-6 text-oyster-200 leading-relaxed">
        <h2 className="text-2xl font-semibold text-oyster-100">What the recorder captures</h2>
        <p>
          The OysterRecorder client records the Minecraft window only. It does not capture
          other open applications, your microphone, your webcam, system audio outside Minecraft,
          or any browser tabs. Recording is started and stopped explicitly by you. Each session
          is encoded as H.265 video plus a synchronized JSON action / camera trace, packaged as
          a single <code>.tar.gz</code> file on your local disk before upload.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">What we store on upload</h2>
        <p>
          When you click upload, the recorder POSTs the tarball to{' '}
          <code>/api/upload-tarball</code> over HTTPS. We store:
        </p>
        <ul className="list-disc pl-6 space-y-2">
          <li>
            <strong>The tarball itself</strong> — in Supabase Storage, keyed by your tester ID and
            the SHA-256 hash of the file. Buyers receive signed download URLs that expire after 24 h.
          </li>
          <li>
            <strong>Metadata row</strong> — your tester ID, the SHA-256, byte size, recording duration,
            upload timestamp, and the D5 quality verdict assigned by our classifier.
          </li>
          <li>
            <strong>Account record</strong> — the email or GitHub identity you signed up with, plus
            (later, when Stripe Connect goes live) the bank / payout details you enter into Stripe&rsquo;s
            hosted onboarding form. We do not see or store your bank credentials directly.
          </li>
        </ul>

        <h2 className="text-2xl font-semibold text-oyster-100">What we do NOT store</h2>
        <ul className="list-disc pl-6 space-y-2">
          <li>Recordings of any application other than Minecraft.</li>
          <li>Your screen outside the Minecraft window.</li>
          <li>Audio of any kind — recordings are video + actions only.</li>
          <li>Browser history, system telemetry, or installed-software inventories.</li>
          <li>Your Minecraft account credentials. The recorder never sees them.</li>
        </ul>

        <h2 className="text-2xl font-semibold text-oyster-100">Who sees your tarballs</h2>
        <p>
          Buyers who purchase a license to a specific tarball receive a 24-hour signed URL to that
          tarball only. They do not see your email, your tester ID, your IP address, or any other
          tarballs you have uploaded. Tester PII is stripped before tarballs ship — buyers receive
          gameplay data without your account information attached.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">Operational logs</h2>
        <p>
          The upload pipeline emits structured JSON log lines on accept, reject, duplicate, and error
          events. Each line includes your tester ID, the file&rsquo;s SHA-256, byte size, latency, and a
          truncated client IP. These logs are retained for operational debugging only and are not
          shared with buyers or third parties.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">Your rights</h2>
        <p>
          You can request deletion of your account and all uploaded tarballs at any time by emailing{' '}
          <a href="mailto:howard@oyster.gg" className="text-amber-accent hover:underline">
            howard@oyster.gg
          </a>
          . We will remove your account, your tarballs, and any associated metadata within 30 days.
          Tarballs that have already been licensed to buyers will be removed from our catalog so they
          cannot be re-licensed; buyers who hold valid licenses retain their downloads under the
          terms they purchased.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">Changes</h2>
        <p>
          When this policy changes materially, we will update the &ldquo;Last updated&rdquo; date above and
          email all active testers with a summary of the change before it takes effect.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">Contact</h2>
        <p>
          Privacy questions:{' '}
          <a href="mailto:howard@oyster.gg" className="text-amber-accent hover:underline">
            howard@oyster.gg
          </a>
          . We respond within two business days.
        </p>
      </section>
    </div>
  );
}
