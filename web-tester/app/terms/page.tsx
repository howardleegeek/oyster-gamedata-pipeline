/**
 * Tester portal — Terms of Service.
 *
 * Howard 2026-05-08: Real terms describing the tester relationship.
 * No boilerplate — every clause matches actual product behavior.
 */

import Link from 'next/link';

export const metadata = {
  title: 'Terms of Service — Oyster GameData (Tester)',
  description:
    'The agreement between testers and Oyster GameData covering recording, payouts, and licensing.',
};

const lastUpdated = '2026-05-08';

export default function TermsPage() {
  return (
    <div className="max-w-3xl mx-auto px-4 py-16">
      <Link href="/" className="text-amber-accent hover:underline text-sm">
        ← back to home
      </Link>
      <h1 className="text-4xl font-bold mt-4 mb-2">Terms of Service — Tester</h1>
      <p className="text-oyster-400 text-sm mb-10">Last updated: {lastUpdated}</p>

      <section className="space-y-6 text-oyster-200 leading-relaxed">
        <h2 className="text-2xl font-semibold text-oyster-100">1. The agreement</h2>
        <p>
          By signing up as a tester, you agree to record Minecraft gameplay using the
          OysterRecorder client and upload qualifying recordings to Oyster GameData. In exchange,
          we pay you on a per-hour basis for accepted recordings and license those recordings to
          third parties for AI model training.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">2. License grant from you to us</h2>
        <p>
          For each tarball you upload that we accept, you grant Oyster GameData a perpetual,
          worldwide, royalty-free, sublicensable, irrevocable license to reproduce, distribute,
          display, and create derivative works from the gameplay data contained in that tarball,
          for the purpose of training, evaluating, and operating AI models. This license applies
          only to tarballs that pass our acceptance criteria; rejected tarballs grant no rights.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">3. Your representations</h2>
        <ul className="list-disc pl-6 space-y-2">
          <li>You own a legitimate copy of Minecraft and abide by Mojang&rsquo;s end-user license agreement.</li>
          <li>The gameplay you record is your own — not someone else&rsquo;s screen, not a stream you are watching, not pre-recorded footage.</li>
          <li>The recorder is running on a machine you control and are authorized to install software on.</li>
          <li>You are at least 18 years old, or have a parent / guardian co-signing this agreement.</li>
        </ul>

        <h2 className="text-2xl font-semibold text-oyster-100">4. Acceptance and quality</h2>
        <p>
          Each uploaded tarball is graded by our D5 classifier into one of three verdicts:
          <code> real</code>, <code>placeholder</code>, or <code>pending</code>. Only{' '}
          <code>real</code> tarballs are billable. Tarballs that fail the classifier or that we
          identify as duplicate, fabricated, or violating these terms will be removed and not paid.
          Repeated submission of fabricated data is grounds for permanent account termination
          without payout of pending balances.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">5. Payment</h2>
        <p>
          Payouts are calculated at the rate published on the <Link href="/docs" className="text-amber-accent hover:underline">/docs</Link>{' '}
          page (currently $6.00 per accepted hour) and disbursed via Stripe Connect. Payouts trigger
          automatically once your accumulated unpaid balance crosses the minimum threshold (currently
          $20.00). Stripe holds funds for up to 7 days for fraud review before disbursement; this
          is a Stripe policy, not ours.
        </p>
        <p>
          During the launch window, Stripe Connect onboarding may be temporarily unavailable while
          we finalize KYC requirements. Your accepted hours continue to accrue and are paid in
          full once onboarding opens. The <Link href="/dashboard" className="text-amber-accent hover:underline">/dashboard</Link>{' '}
          page always reflects your real accepted-hour count, never an estimate.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">6. Termination</h2>
        <p>
          You can terminate this agreement at any time by emailing{' '}
          <a href="mailto:howard@oyster.gg" className="text-amber-accent hover:underline">
            howard@oyster.gg
          </a>
          . On termination we will pay out any accrued balance above the minimum threshold and
          remove your account per the privacy policy. The license you granted in section 2 for
          tarballs we have already accepted survives termination.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">7. Disclaimers</h2>
        <p>
          Oyster GameData is provided &ldquo;as is&rdquo;. We do not warrant that the recorder is bug-free,
          that uploads will always succeed on the first try, or that any specific buyer will license
          your tarballs. We back up uploaded tarballs and metadata, but you are responsible for
          keeping local copies of any recordings you consider irreplaceable.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">8. Governing law</h2>
        <p>
          These terms are governed by the laws of the State of Delaware, USA. Disputes are resolved
          in the state and federal courts located in Wilmington, Delaware.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">9. Contact</h2>
        <p>
          <a href="mailto:howard@oyster.gg" className="text-amber-accent hover:underline">
            howard@oyster.gg
          </a>
          {' '}— we respond within two business days.
        </p>
      </section>
    </div>
  );
}
