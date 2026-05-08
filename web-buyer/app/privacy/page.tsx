/**
 * Buyer portal — Privacy Policy.
 *
 * Howard 2026-05-08: Real description of what buyer accounts store and
 * how purchase / download data is handled.
 */

import Link from 'next/link';

export const metadata = {
  title: 'Privacy Policy — Oyster GameData (Buyer)',
  description:
    'What data buyer accounts store, how purchases are processed, and how downloads are authorized.',
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
        <h2 className="text-2xl font-semibold text-oyster-100">What we store on your account</h2>
        <p>
          When you sign up as a buyer, we store your email or GitHub identity, the organization you
          declare you are buying for, and your purchase history (which tarballs you bought and when).
          We do not store your payment card details — those go directly to Stripe&rsquo;s hosted Checkout,
          which we never see.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">What you see about testers</h2>
        <p>
          Buyers see only anonymized tarball metadata: SHA-256 hash, size, recording duration,
          upload timestamp, and our D5 quality verdict. You do not see tester emails, tester IDs,
          tester IP addresses, or any other identifying information about the human who recorded
          the gameplay.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">Downloads and signed URLs</h2>
        <p>
          When you purchase a tarball, the <Link href="/downloads" className="text-amber-accent hover:underline">/downloads</Link>{' '}
          page issues a signed URL that expires after 24 hours. You can re-issue links from that page
          indefinitely. We log the timestamp and your account ID against each download, but we do not
          share download patterns with anyone.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">Operational logs</h2>
        <p>
          The catalog and checkout endpoints emit structured JSON log lines on key events
          (catalog query, cart add, checkout session created, signed URL issued). Log lines include
          your account ID, the affected tarball ID, byte size, latency, and a truncated client IP.
          These logs are retained for operational debugging only and are not shared with third
          parties.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">Stripe</h2>
        <p>
          Payments are processed by Stripe under{' '}
          <a
            href="https://stripe.com/privacy"
            className="text-amber-accent hover:underline"
            target="_blank"
            rel="noreferrer"
          >
            Stripe&rsquo;s privacy policy
          </a>
          . We see and store the Stripe customer ID, the Checkout session ID, the payment status,
          and the line items purchased — never the card itself. Refund requests go through the
          Stripe dashboard; we can reverse a charge but we never re-touch the underlying card data.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">Your rights</h2>
        <p>
          You can request deletion of your buyer account at any time by emailing{' '}
          <a href="mailto:howard@oyster.gg" className="text-amber-accent hover:underline">
            howard@oyster.gg
          </a>
          . We will remove your account and your purchase history within 30 days. License grants
          for tarballs you have already purchased survive account deletion under their original
          terms.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">Changes</h2>
        <p>
          When this policy changes materially, we will update the &ldquo;Last updated&rdquo; date above and
          email all active buyers with a summary of the change before it takes effect.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">Contact</h2>
        <p>
          <a href="mailto:howard@oyster.gg" className="text-amber-accent hover:underline">
            howard@oyster.gg
          </a>
          {' '}— privacy questions answered within two business days.
        </p>
      </section>
    </div>
  );
}
