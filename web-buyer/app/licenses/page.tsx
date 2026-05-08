import Link from 'next/link';
import { env, isSupabaseConfigured } from '../../lib/env';
import { getSupabaseServerClient, getSupabaseServiceClient } from '../../lib/supabase-server';
import { sampleBuyer, sampleLicenses, samplePurchases } from '../../lib/sample-data';
import { formatRelativeTime } from '../../lib/format';
import type { LicenseRow, PurchaseRow } from '../../types/database';

export const dynamic = 'force-dynamic';

async function loadLicenses(): Promise<{
  licenses: LicenseRow[];
  purchaseById: Map<string, PurchaseRow>;
  liveMode: boolean;
}> {
  if (!isSupabaseConfigured()) {
    const buyer = sampleBuyer();
    const purchases = samplePurchases(buyer.id);
    return {
      licenses: sampleLicenses(buyer.id),
      purchaseById: new Map(purchases.map((p) => [p.id, p])),
      liveMode: false,
    };
  }
  const supabase = getSupabaseServerClient();
  if (!supabase) throw new Error('Supabase server client unavailable.');
  const { data: { user } } = await supabase.auth.getUser();

  // Anonymous visitors still get the full terms below — only the
  // "Your license certificates" panel is empty for them.
  if (!user) {
    return { licenses: [], purchaseById: new Map(), liveMode: true };
  }

  const service = getSupabaseServiceClient();
  if (!service) throw new Error('Service role client unavailable.');

  const { data: purchases } = await service
    .from('purchases')
    .select('id, buyer_id, tarball_id, amount_cents, stripe_session_id, purchased_at, license_id')
    .eq('buyer_id', user.id);
  const ids = (purchases ?? []).map((p: any) => p.id);
  const { data: licenseRows } = ids.length
    ? await service
        .from('licenses')
        .select('id, purchase_id, type, terms_url, issued_at')
        .in('purchase_id', ids)
    : { data: [] };

  return {
    licenses: (licenseRows ?? []) as LicenseRow[],
    purchaseById: new Map(((purchases ?? []) as PurchaseRow[]).map((p) => [p.id, p])),
    liveMode: true,
  };
}

export default async function LicensesPage() {
  const { licenses, purchaseById, liveMode } = await loadLicenses();

  return (
    <div className="max-w-3xl mx-auto px-4 py-10">
      <h1 className="text-4xl font-extrabold mb-2">License terms</h1>
      <p className="text-oyster-300 mb-8">
        Every Oyster GameData tarball is sold under one of two licenses.
        Both are perpetual, world-wide, and royalty-free for AI model training.
      </p>

      {/* Side nav */}
      <nav className="card p-4 mb-8 text-sm">
        <h3 className="font-semibold mb-2 text-oyster-50">Contents</h3>
        <ul className="space-y-1.5 text-oyster-300">
          <li><a href="#research"   className="hover:text-amber-accent">1. Research license</a></li>
          <li><a href="#commercial" className="hover:text-amber-accent">2. Commercial license</a></li>
          <li><a href="#delivery"   className="hover:text-amber-accent">3. Delivery and downloads</a></li>
          <li><a href="#privacy"    className="hover:text-amber-accent">4. Privacy and PII</a></li>
          <li><a href="#certs"      className="hover:text-amber-accent">5. Your license certificates</a></li>
        </ul>
      </nav>

      {/* RESEARCH */}
      <section id="research" className="space-y-3 scroll-mt-24">
        <h2 className="text-2xl font-bold mt-8">1. Research license</h2>
        <p>
          Available to academic researchers and non-profit labs. Discounted{' '}
          <strong>{env.researchDiscountPct}%</strong> off list price. The buyer must verify
          academic affiliation at checkout.
        </p>
        <ul className="list-disc list-inside space-y-1 text-oyster-300">
          <li>Use for AI model training, evaluation, and academic publication.</li>
          <li>You may publish trained model weights derived from the data.</li>
          <li>You may NOT redistribute the raw tarballs.</li>
          <li>You may NOT use the data to train models that are commercially licensed or sold.</li>
          <li>Attribution requested in publications: &quot;Oyster GameData v1, Oyster Labs, 2026.&quot;</li>
        </ul>
      </section>

      {/* COMMERCIAL */}
      <section id="commercial" className="space-y-3 scroll-mt-24">
        <h2 className="text-2xl font-bold mt-10">2. Commercial license</h2>
        <p>
          For private companies training proprietary models. List price is{' '}
          <strong>${(env.pricePerGbCents / 100).toFixed(2)}/GB</strong>.
        </p>
        <ul className="list-disc list-inside space-y-1 text-oyster-300">
          <li>Use for AI model training, evaluation, and product features.</li>
          <li>You may train and ship commercial models derived from the data.</li>
          <li>You may NOT redistribute the raw tarballs to third parties.</li>
          <li>You may NOT republish raw video footage in any product.</li>
          <li>No royalty obligations after purchase.</li>
        </ul>
      </section>

      {/* DELIVERY */}
      <section id="delivery" className="space-y-3 scroll-mt-24">
        <h2 className="text-2xl font-bold mt-10">3. Delivery and downloads</h2>
        <ul className="list-disc list-inside space-y-1 text-oyster-300">
          <li>Tarballs are delivered as private, signed S3-style URLs from{' '}
            <Link href="/downloads" className="text-amber-accent hover:underline">/downloads</Link>.</li>
          <li>Each link expires after{' '}
            <strong>{Math.round(env.downloadLinkTtlSeconds / 3600)} hours</strong>.</li>
          <li>You can re-issue links from /downloads at any time after purchase.</li>
          <li>Each tarball has a SHA-256 checksum visible on the detail page;
            verify integrity locally after download.</li>
        </ul>
      </section>

      {/* PRIVACY */}
      <section id="privacy" className="space-y-3 scroll-mt-24">
        <h2 className="text-2xl font-bold mt-10">4. Privacy and PII</h2>
        <ul className="list-disc list-inside space-y-1 text-oyster-300">
          <li>Recordings only capture the Minecraft window — no other apps, no microphone, no webcam.</li>
          <li>Tester PII is stripped from tarball metadata before delivery.</li>
          <li>If a tester revokes consent for a specific tarball, we will best-effort delete it from
            our storage and notify any buyers who licensed it.</li>
          <li>Multiplayer chat captures redact usernames matching common PII patterns (emails, phone numbers).</li>
        </ul>
      </section>

      {/* CERTS */}
      <section id="certs" className="space-y-3 scroll-mt-24">
        <h2 className="text-2xl font-bold mt-10">5. Your license certificates</h2>

        {!liveMode && (
          <p className="text-amber-accent text-sm">
            [DEV MODE] showing sample certificates.
          </p>
        )}

        {licenses.length === 0 ? (
          <p className="text-oyster-400 text-sm">
            You have no purchases yet —{' '}
            <Link href="/browse" className="text-amber-accent hover:underline">
              browse the catalog
            </Link>{' '}
            to buy your first tarball.
          </p>
        ) : (
          <div className="space-y-3">
            {licenses.map((l) => {
              const purchase = purchaseById.get(l.purchase_id);
              return (
                <div key={l.id} className="card p-4 flex items-center justify-between gap-4">
                  <div>
                    <div className="font-semibold text-oyster-100">
                      {l.type === 'research' ? 'Research license' : 'Commercial license'}
                    </div>
                    <div className="text-xs text-oyster-400 mt-0.5">
                      Issued {formatRelativeTime(l.issued_at)}
                      {purchase && (
                        <>
                          {' '}· purchase{' '}
                          <span className="font-mono">{purchase.id.slice(0, 8)}…</span>
                        </>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <a
                      href={`/api/downloads/${l.purchase_id}?license_only=1`}
                      className="btn-secondary py-1.5 px-3 text-sm"
                    >
                      Download cert
                    </a>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
