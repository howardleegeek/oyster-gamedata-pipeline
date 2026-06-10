/**
 * GET /api/downloads/[purchaseId]
 *
 * Returns the signed download URL(s) for a purchase. Two behaviours:
 *
 *   1. ?format=json (default for fetch / curl)
 *      -> { tarball, signed_url, expires_at, license, certificate }
 *
 *   2. plain GET (browser)
 *      -> 302-redirect straight to the signed URL so clicking the
 *         "Download .tar.gz" button starts the download immediately.
 *
 * Optional `?license_only=1` returns the signed certificate text instead
 * of the tarball — used by the /licenses "Download cert" button.
 *
 * Authorisation: the calling buyer (or service role header) must own the
 * purchase row.
 *
 * Howard 2026-05-07 IRON-LAW: when Supabase isn't configured, returns
 * 503 with envVars. The previous DEV MODE branch synthesised fake buyer
 * emails, fake purchase IDs, fake signed URLs, and even a fake tarball
 * blob (`stubBody` octet-stream). All gone — render NotConfigured at the
 * page level instead.
 */

import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { getSupabaseServerClient, getSupabaseServiceClient } from '../../../../lib/supabase-server';
import { isSupabaseConfigured, env } from '../../../../lib/env';
import { safeEqual } from '../../../../lib/safe-equal';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const PurchaseIdSchema = z.string().uuid();

function buildCertificate(args: {
  purchaseId: string;
  buyerEmail: string;
  tarballTitle: string;
  tarballSha256: string;
  licenseType: string;
  amountCents: number;
  issuedAt: string;
}): string {
  return [
    'OYSTER GAMEDATA — LICENSE CERTIFICATE',
    '=====================================',
    '',
    `Purchase ID  : ${args.purchaseId}`,
    `Buyer        : ${args.buyerEmail}`,
    `Tarball      : ${args.tarballTitle}`,
    `SHA-256      : ${args.tarballSha256}`,
    `License type : ${args.licenseType}`,
    `Amount paid  : $${(args.amountCents / 100).toFixed(2)} USD`,
    `Issued at    : ${args.issuedAt}`,
    '',
    'This certificate confirms that the named buyer holds a perpetual,',
    'world-wide, royalty-free license to use the named tarball under the',
    `${args.licenseType.toUpperCase()} terms published at ${env.siteUrl}/licenses.`,
    '',
    'Verify this certificate at any time by re-issuing it from /licenses.',
  ].join('\n');
}

export async function GET(
  req: NextRequest,
  { params }: { params: { purchaseId: string } },
) {
  // Howard 2026-05-07 IRON-LAW: hard-gate before any synthesis.
  if (!isSupabaseConfigured()) {
    return NextResponse.json(
      {
        error: 'Supabase not configured',
        envVars: ['NEXT_PUBLIC_SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_ANON_KEY', 'SUPABASE_SERVICE_ROLE_KEY'],
      },
      { status: 503 },
    );
  }

  const parsed = PurchaseIdSchema.safeParse(params.purchaseId);
  if (!parsed.success) {
    return NextResponse.json({ error: 'Invalid purchaseId' }, { status: 400 });
  }
  const purchaseId = parsed.data;

  const url = new URL(req.url);
  const isJson =
    url.searchParams.get('format') === 'json' ||
    (req.headers.get('accept') ?? '').includes('application/json');
  const licenseOnly = url.searchParams.get('license_only') === '1';

  // ------------------------------------------------------------------
  // LIVE MODE — Supabase + signed storage URL.
  // ------------------------------------------------------------------
  const supabase = getSupabaseServerClient();
  if (!supabase) {
    return NextResponse.json({ error: 'Supabase server client unavailable' }, { status: 500 });
  }
  const { data: { user } } = await supabase.auth.getUser();

  // SECURITY (fix #02): constant-time compare against service-role key.
  // Plain `===` short-circuits at the first byte mismatch and lets an
  // attacker recover the key one byte at a time via HTTP RTT timing.
  const adminHeader = req.headers.get('x-supabase-service-role') ?? '';
  const isAdmin = safeEqual(adminHeader, env.supabaseServiceRoleKey);

  if (!isAdmin && !user) {
    return NextResponse.json({ error: 'Unauthenticated' }, { status: 401 });
  }

  const service = getSupabaseServiceClient();
  if (!service) {
    return NextResponse.json({ error: 'Service client unavailable' }, { status: 500 });
  }

  const { data: purchase, error: pErr } = await service
    .from('purchases')
    .select('id, buyer_id, tarball_id, amount_cents, purchased_at, stripe_session_id, license_id')
    .eq('id', purchaseId)
    .single();
  if (pErr || !purchase) {
    return NextResponse.json({ error: 'Purchase not found' }, { status: 404 });
  }
  if (!isAdmin && user && purchase.buyer_id !== user.id) {
    return NextResponse.json({ error: 'Forbidden — not your purchase' }, { status: 403 });
  }

  const { data: tarball, error: tErr } = await service
    .from('tarballs')
    .select('id, sha256, size_bytes, storage_path')
    .eq('id', purchase.tarball_id)
    .single();
  if (tErr || !tarball) {
    return NextResponse.json({ error: 'Tarball missing' }, { status: 410 });
  }

  let license: any = null;
  if (purchase.license_id) {
    const { data } = await service
      .from('licenses')
      .select('id, purchase_id, type, terms_url, issued_at')
      .eq('id', purchase.license_id)
      .single();
    license = data;
  }

  // Buyer email for the certificate
  let buyerEmail = user?.email ?? '';
  try {
    const { data: buyer } = await service
      .from('buyers')
      .select('email')
      .eq('id', purchase.buyer_id)
      .single();
    if (buyer?.email) buyerEmail = buyer.email;
  } catch {
    /* ignore */
  }

  if (licenseOnly) {
    const cert = buildCertificate({
      purchaseId: purchase.id,
      buyerEmail,
      tarballTitle: '(see /downloads)',
      tarballSha256: tarball.sha256,
      licenseType: license?.type ?? 'commercial',
      amountCents: Number(purchase.amount_cents),
      issuedAt: license?.issued_at ?? purchase.purchased_at,
    });
    return new NextResponse(cert, {
      status: 200,
      headers: {
        'Content-Type': 'text/plain; charset=utf-8',
        'Content-Disposition': `attachment; filename="oyster-license-${purchase.id}.txt"`,
      },
    });
  }

  // Mint a signed URL for the tarball blob.
  const { data: signed, error: signErr } = await service.storage
    .from(env.tarballBucket)
    .createSignedUrl(tarball.storage_path, env.downloadLinkTtlSeconds, {
      download: `oyster-${tarball.sha256.slice(0, 12)}.tar.gz`,
    });
  if (signErr || !signed?.signedUrl) {
    return NextResponse.json(
      { error: 'Could not sign download URL', details: signErr?.message },
      { status: 502 },
    );
  }

  const expiresAt = new Date(Date.now() + env.downloadLinkTtlSeconds * 1000).toISOString();

  if (isJson) {
    return NextResponse.json({
      mode: 'live',
      tarball: {
        id: tarball.id,
        size_bytes: Number(tarball.size_bytes),
        sha256: tarball.sha256,
      },
      signed_url: signed.signedUrl,
      expires_at: expiresAt,
      license,
    });
  }

  return NextResponse.redirect(signed.signedUrl, { status: 302 });
}
