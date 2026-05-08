/**
 * GET /api/download/[testerId]
 *
 * Serves the OysterRecorder.exe with a filename that embeds the tester ID,
 * so uploads from that build can be attributed.
 *
 * Behaviour:
 *   - If RECORDER_EXE_URL points to an external URL (e.g. GitHub Releases),
 *     we 302-redirect with `Content-Disposition: filename=...` hints baked
 *     into the redirect URL via a query param the CDN ignores.
 *   - If RECORDER_EXE_URL is a local /public path, we stream it back with
 *     a Content-Disposition header.
 *   - If the .exe is genuinely missing, we return 404 with a remediation
 *     message — never a fabricated text file pretending to be the binary.
 *
 * Howard 2026-05-07 IRON-LAW: the previous implementation returned a
 * plaintext fallback labelled with the tester id and served it with an
 * .exe.txt Content-Disposition + a synthetic header flag. That was a
 * fabricated download shipping in production source. Now: 404.
 */

import { NextRequest, NextResponse } from 'next/server';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { z } from 'zod';
import { env } from '../../../../lib/env';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const TesterIdSchema = z.string().uuid();

export async function GET(
  _req: NextRequest,
  { params }: { params: { testerId: string } }
) {
  const parsed = TesterIdSchema.safeParse(params.testerId);
  if (!parsed.success) {
    return NextResponse.json({ error: 'Invalid testerId — must be a UUID' }, { status: 400 });
  }
  const testerId = parsed.data;
  const shortId = testerId.slice(0, 8);
  const filename = `OysterRecorder-${shortId}-${testerId}.exe`;

  // ---- External URL -> redirect with attribution param ----------------
  if (/^https?:\/\//i.test(env.recorderExeUrl)) {
    const redirectUrl = new URL(env.recorderExeUrl);
    redirectUrl.searchParams.set('tester_id', testerId);
    return NextResponse.redirect(redirectUrl.toString(), { status: 302 });
  }

  // ---- Local file -> stream with Content-Disposition ------------------
  const localPath = path.join(process.cwd(), 'public', env.recorderExeUrl.replace(/^\//, ''));
  try {
    const buf = await fs.readFile(localPath);
    return new NextResponse(buf, {
      status: 200,
      headers: {
        'Content-Type': 'application/octet-stream',
        'Content-Length': buf.byteLength.toString(),
        'Content-Disposition': `attachment; filename="${filename}"`,
        'Cache-Control': 'no-store',
      },
    });
  } catch {
    // Howard 2026-05-07 IRON-LAW: no stub-text fallback. Return 404 with
    // a remediation message so the operator knows what to fix.
    return NextResponse.json(
      {
        error: 'OysterRecorder.exe is not available',
        details:
          `The build is missing at ${env.recorderExeUrl}. ` +
          'Set RECORDER_EXE_URL to a hosted URL (e.g. GitHub Releases asset) ' +
          'or drop the binary into web-tester/public/downloads/OysterRecorder.exe.',
        tester_id: testerId,
        build: env.recorderVersion,
      },
      { status: 404 },
    );
  }
}
