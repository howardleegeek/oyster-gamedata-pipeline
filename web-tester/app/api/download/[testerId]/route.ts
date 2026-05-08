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
 *   - In DEV with no .exe present, we return a small placeholder text file
 *     so the click-through still works during smoke tests.
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
    // File is missing — return a plaintext "stub" so the link still resolves.
    // This is intentional for DEV environments where the .exe isn't built yet.
    const stub = [
      `# OysterRecorder placeholder for tester ${testerId}`,
      ``,
      `This is a stub file — the real OysterRecorder.exe is not yet present`,
      `at ${env.recorderExeUrl}.`,
      ``,
      `Drop the built binary into web-tester/public/downloads/OysterRecorder.exe`,
      `or set RECORDER_EXE_URL to a hosted URL (e.g. GitHub Releases asset).`,
      ``,
      `Tester ID: ${testerId}`,
      `Build: ${env.recorderVersion}`,
      `Generated: ${new Date().toISOString()}`,
    ].join('\n');
    return new NextResponse(stub, {
      status: 200,
      headers: {
        'Content-Type': 'text/plain; charset=utf-8',
        'Content-Disposition': `attachment; filename="${filename}.txt"`,
        'Cache-Control': 'no-store',
        'X-Recorder-Stub': 'true',
      },
    });
  }
}
