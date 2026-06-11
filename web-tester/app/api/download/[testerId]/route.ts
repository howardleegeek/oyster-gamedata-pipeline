/**
 * GET /api/download/[testerId]
 *
 * Serves the OysterRecorder.exe with a filename that embeds the tester ID
 * AND the 16-hex HMAC token prefix, so uploads from that build can be
 * attributed AND authenticated.
 *
 * Behaviour:
 *   - If RECORDER_EXE_URL points to an external URL (e.g. GitHub Releases),
 *     we stream-proxy the bytes through this route and emit our own
 *     `Content-Disposition` header so the token-embedded filename reaches
 *     the recorder. (QA1 finding #5 fix: BUG-21 — the previous 302 redirect
 *     lost the filename to upstream's `Content-Disposition`, so Gap #6's
 *     filename-token never reached the recorder.)
 *   - If RECORDER_EXE_URL is a local /public path, we stream it back with
 *     a Content-Disposition header (unchanged).
 *   - If the .exe is genuinely missing, we return 404 with a remediation
 *     message — never a fabricated text file pretending to be the binary.
 *
 * Howard 2026-05-07 IRON-LAW: the previous implementation returned a
 * plaintext fallback labelled with the tester id and served it with an
 * .exe.txt Content-Disposition + a synthetic header flag. That was a
 * fabricated download shipping in production source. Now: 404.
 *
 * QA1 finding #5 (BUG-21) — filename<->verifier parity invariant:
 *   We assert that the embedded `tokenPrefix` is identical to what
 *   `lib/upload-auth.ts::verifyToken()` will accept as the 16-hex prefix
 *   form. The unit test under `tests/test_qa_bug_hunt_2026_05_13.py`
 *   greps this file for `computeTokenPrefix(testerId,` and checks the
 *   verifier accepts the same value — preventing a silent embed/parse
 *   drift between the two modules.
 */

import { NextRequest, NextResponse } from 'next/server';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { z } from 'zod';
import { env } from '../../../../lib/env';
import {
  TOKEN_PREFIX_LEN,
  computeTokenPrefix,
  isHmacConfigured,
  verifyToken,
} from '../../../../lib/upload-auth';
import { log } from '../../../../lib/log';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const maxDuration = 300; // stream-proxy of large .exe builds may take a while

const TesterIdSchema = z.string().uuid();

/**
 * Compute the filename and (if HMAC is configured) the token prefix that
 * the recorder will see. Asserts the embed/parse invariant: the embedded
 * prefix must verify cleanly via `verifyToken()` against the very same
 * tester_id. This catches a silent drift between the embed code here and
 * the verifier in lib/upload-auth.ts — the QA1 finding #5 root cause.
 */
function buildFilename(testerId: string): { filename: string; tokenPrefix: string | null } {
  const shortId = testerId.slice(0, 8);
  if (!isHmacConfigured()) {
    log.warn('download.hmac_unconfigured', { tester_id: testerId });
    return {
      filename: `OysterRecorder-${shortId}-${testerId}.exe`,
      tokenPrefix: null,
    };
  }
  const secret = process.env.UPLOAD_HMAC_SECRET ?? '';
  const tokenPrefix = computeTokenPrefix(testerId, secret);

  // Invariant guard — if the verifier doesn't accept what we're about to
  // embed, something is *very* wrong (e.g. someone changed TOKEN_PREFIX_LEN
  // on one side but not the other). Fail loudly rather than ship a broken
  // build that the upload route will reject 100% of the time.
  if (tokenPrefix.length !== TOKEN_PREFIX_LEN) {
    throw new Error(
      `embed/verifier drift: tokenPrefix length ${tokenPrefix.length} ` +
        `!= TOKEN_PREFIX_LEN ${TOKEN_PREFIX_LEN}`,
    );
  }
  if (!verifyToken(testerId, tokenPrefix, secret)) {
    throw new Error(
      'embed/verifier drift: computeTokenPrefix output is not accepted by verifyToken. ' +
        'Audit lib/upload-auth.ts — a silent change to the HMAC algorithm or the ' +
        'prefix slice would break every recorder build at upload time.',
    );
  }

  return {
    filename: `OysterRecorder-${shortId}-${testerId}-${tokenPrefix}.exe`,
    tokenPrefix,
  };
}

export async function GET(
  _req: NextRequest,
  { params }: { params: { testerId: string } }
) {
  const parsed = TesterIdSchema.safeParse(params.testerId);
  if (!parsed.success) {
    return NextResponse.json({ error: 'Invalid testerId — must be a UUID' }, { status: 400 });
  }
  const testerId = parsed.data;

  let filename: string;
  try {
    ({ filename } = buildFilename(testerId));
  } catch (err) {
    log.error('download.embed_verifier_drift', {
      tester_id: testerId,
      detail: (err as Error).message,
    });
    return NextResponse.json(
      {
        error: 'Server misconfiguration',
        details:
          'The HMAC token embed/verifier consistency check failed. ' +
          'Operator: audit web-tester/lib/upload-auth.ts and the download route.',
      },
      { status: 500 },
    );
  }

  // ---- External URL -> stream-proxy with our own Content-Disposition --
  //
  // QA1 finding #5 fix (BUG-21): on a fresh deploy where
  // `RECORDER_EXE_URL` points at a GitHub Release asset (the documented
  // default), the previous code emitted a 302 redirect — and the browser
  // followed up to fetch the .exe directly from GitHub. GitHub's
  // `Content-Disposition` then claimed the filename was the upstream
  // asset name (`OysterRecorder.exe`), and our token-embedded filename
  // was never seen by the recorder. Result: `parse_token_from_exe_name`
  // returned None, the recorder uploaded without `X-Upload-Token`, and
  // either got 401 (with the new default-deny) or proceeded
  // unauthenticated. To fix, we stream the upstream bytes through and
  // emit our own `Content-Disposition` with the token-embedded filename.
  if (/^https?:\/\//i.test(env.recorderExeUrl)) {
    let upstream: Response;
    try {
      upstream = await fetch(env.recorderExeUrl, {
        // No `redirect: 'manual'` — GitHub Release URLs 302 to the CDN.
        cache: 'no-store',
      });
    } catch (err) {
      log.error('download.fetch_failed', {
        tester_id: testerId,
        url: env.recorderExeUrl,
        detail: (err as Error).message,
      });
      return NextResponse.json(
        {
          error: 'Upstream fetch failed',
          details:
            `Could not fetch the recorder binary from ${env.recorderExeUrl}. ` +
            'This usually means a transient CDN error; retry. If persistent, ' +
            'check RECORDER_EXE_URL.',
        },
        { status: 502 },
      );
    }
    if (!upstream.ok || !upstream.body) {
      log.error('download.upstream_error', {
        tester_id: testerId,
        url: env.recorderExeUrl,
        status: upstream.status,
      });
      return NextResponse.json(
        {
          error: 'Upstream returned an error',
          details: `Upstream ${env.recorderExeUrl} responded ${upstream.status}.`,
        },
        { status: 502 },
      );
    }

    const headers = new Headers({
      'Content-Type': 'application/octet-stream',
      'Content-Disposition': `attachment; filename="${filename}"`,
      'Cache-Control': 'no-store',
    });
    const upstreamLen = upstream.headers.get('content-length');
    if (upstreamLen) headers.set('Content-Length', upstreamLen);

    return new NextResponse(upstream.body, { status: 200, headers });
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
