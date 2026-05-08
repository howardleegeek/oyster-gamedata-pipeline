/**
 * Buyer portal — Terms of Service.
 *
 * Howard 2026-05-08: Real terms describing the buyer license. Mirrors
 * the rights granted by testers in the tester ToS, so what we sell is
 * exactly what testers granted us.
 */

import Link from 'next/link';

export const metadata = {
  title: 'Terms of Service — Oyster GameData (Buyer)',
  description:
    'License terms, refund policy, and the agreement covering tarballs purchased through Oyster GameData.',
};

const lastUpdated = '2026-05-08';

export default function TermsPage() {
  return (
    <div className="max-w-3xl mx-auto px-4 py-16">
      <Link href="/" className="text-amber-accent hover:underline text-sm">
        ← back to home
      </Link>
      <h1 className="text-4xl font-bold mt-4 mb-2">Terms of Service — Buyer</h1>
      <p className="text-oyster-400 text-sm mb-10">Last updated: {lastUpdated}</p>

      <section className="space-y-6 text-oyster-200 leading-relaxed">
        <h2 className="text-2xl font-semibold text-oyster-100">1. The agreement</h2>
        <p>
          By purchasing a tarball, you agree to these terms and to one of the licenses linked from{' '}
          <Link href="/licenses" className="text-amber-accent hover:underline">/licenses</Link>{' '}
          (research-academic or commercial). The license you select at checkout governs how you may
          use the gameplay data; these terms govern the underlying purchase relationship.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">2. What you are buying</h2>
        <p>
          Each tarball contains synchronized H.265 video, action / camera JSON, and structured world
          events from a single Minecraft recording session uploaded by a paid tester who granted us
          the license rights we are passing through to you. The data is real human gameplay — not
          scraped Twitch / YouTube footage, not synthetic, not augmented.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">3. License pass-through</h2>
        <p>
          We grant you the rights described in your selected license file. We can grant only what
          our testers granted us — you are buying their gameplay under the terms they agreed to.
          If a tester revokes future licensability of their content, your existing license under
          the terms you purchased is unaffected, but the tarball will no longer appear in our
          catalog.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">4. Pricing and payment</h2>
        <p>
          Pricing is shown on each tarball detail page in USD per gigabyte. Charges run through
          Stripe Checkout. Research / academic buyers receive an automatic discount as configured
          in our pricing system; the discount is shown on the checkout page before you confirm.
          You authorize Stripe to charge the payment method you select on file.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">5. Downloads</h2>
        <p>
          On successful purchase, the <Link href="/downloads" className="text-amber-accent hover:underline">/downloads</Link>{' '}
          page issues signed URLs that expire after 24 hours. You can re-issue links indefinitely
          from that page using your purchase record. Failed downloads, network interruptions, or
          link expiration are not refundable events — re-issue and try again.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">6. Refunds</h2>
        <p>
          Because tarballs are digital goods that can be downloaded immediately, sales are final
          once the first signed URL has been issued. Before that point, you can cancel from your
          cart at any time and no charge will be processed. If you believe a tarball you purchased
          materially misrepresents what was advertised (wrong duration, wrong resolution, corrupted
          data), email <a href="mailto:howard@oyster.gg" className="text-amber-accent hover:underline">howard@oyster.gg</a>{' '}
          within 7 days and we will issue a full refund through Stripe.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">7. Acceptable use</h2>
        <ul className="list-disc pl-6 space-y-2">
          <li>You will use the gameplay data within the bounds of the license you selected.</li>
          <li>You will not redistribute raw tarballs to third parties (the license is to you, not your downstream users — your trained models are yours, the tarballs are not).</li>
          <li>You will not attempt to identify or contact the testers whose gameplay you license.</li>
          <li>You will not use the data to develop products that compete directly with Oyster GameData&rsquo;s licensing business.</li>
        </ul>

        <h2 className="text-2xl font-semibold text-oyster-100">8. Disclaimers</h2>
        <p>
          Oyster GameData is provided &ldquo;as is&rdquo;. We grade tarballs with our D5 classifier and surface
          the verdict, but we do not warrant that any specific tarball will train any specific model
          to any specific accuracy. We back up tarballs and metadata, but you are responsible for
          keeping local copies of any tarball you have purchased and downloaded.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">9. Termination</h2>
        <p>
          You can close your buyer account at any time by emailing{' '}
          <a href="mailto:howard@oyster.gg" className="text-amber-accent hover:underline">
            howard@oyster.gg
          </a>
          . Licenses you have already purchased survive account closure under their original terms.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">10. Governing law</h2>
        <p>
          These terms are governed by the laws of the State of Delaware, USA. Disputes are resolved
          in the state and federal courts located in Wilmington, Delaware.
        </p>

        <h2 className="text-2xl font-semibold text-oyster-100">11. Contact</h2>
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
