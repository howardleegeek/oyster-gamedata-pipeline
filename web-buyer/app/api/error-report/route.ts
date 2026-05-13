/**
 * POST /api/error-report
 *
 * G231-G240 · W28 Error Reporting Service
 *
 * Body:
 *   {
 *     recorder_version: "v0.28.0-rc19.0.1",
 *     os:               "windows-11-build-22631",
 *     stack_trace:      "Traceback (most recent call last):\n  ...",
 *     context:          { game: "minecraft", clip_id: "..." },
 *     anon_id:          "a3b9-..."
 *   }
 *
 * Behaviour:
 *   1. zod schema enforces shape, sizes, char-classes.
 *   2. Stack + context are PII-scrubbed (filesystem paths, usernames,
 *      machine names, IPv4, emails) BEFORE persisting.
 *   3. A fingerprint hash is computed over (scrubbed_stack, os_family,
 *      recorder_major_version) so 1000 testers hitting the same crash
 *      collapse into 1 row with count++.
 *   4. Upsert into Supabase table error_reports (see migration
 *      supabase/migrations/2026_05_13_g231_error_reports.sql).
 *   5. Reply with { fingerprint, count, duplicate } so the client can
 *      decide whether to retry or back off.
 *
 * Iron-law:
 *   - Returns 503 with envVars[] when Supabase is not configured. No
 *     local fallback that pretends to succeed.
 *   - Rate-limited per anon_id (10/hour) AND per IP (60/hour).
 */

import { NextRequest, NextResponse } from 'next/server';
import {
  ReportSchema,
  scrubPii,
  scrubContext,
  fingerprintStack,
} from '../../../lib/error-report';
import { getSupabaseServiceClient } from '../../../lib/supabase-server';
import { isSupabaseConfigured } from '../../../lib/env';
import { checkRateLimit, clientIpFromHeaders } from '../../../lib/rate-limit';
import { log } from '../../../lib/log';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const RL_IP_LIMIT = 60;
const RL_IP_WINDOW_MS = 60 * 60_000;
const RL_ANON_LIMIT = 10;
const RL_ANON_WINDOW_MS = 60 * 60_000;

function rateLimitResponse(
  scope: string,
  info: ReturnType<typeof checkRateLimit>
) {
  return NextResponse.json(
    {
      error: 'Rate limit exceeded',
      scope,
      limit: info.limit,
      remaining: info.remaining,
      resetAt: new Date(info.resetAt).toISOString(),
    },
    {
      status: 429,
      headers: {
        'Retry-After': String(
          Math.max(1, Math.ceil((info.resetAt - Date.now()) / 1000))
        ),
        'X-RateLimit-Limit': String(info.limit),
        'X-RateLimit-Remaining': String(info.remaining),
        'X-RateLimit-Reset': String(Math.floor(info.resetAt / 1000)),
      },
    }
  );
}

export async function POST(req: NextRequest) {
  const startedAt = Date.now();
  const ip = clientIpFromHeaders(req.headers);

  const ipRl = checkRateLimit(`error:ip:${ip}`, RL_IP_LIMIT, RL_IP_WINDOW_MS);
  if (!ipRl.allowed) {
    log.warn('error_report.rate_limited', { ip, scope: 'ip', limit: ipRl.limit });
    return rateLimitResponse('ip', ipRl);
  }

  let raw: unknown;
  try {
    raw = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }
  const parsed = ReportSchema.safeParse(raw);
  if (!parsed.success) {
    return NextResponse.json(
      { error: 'Invalid body', details: parsed.error.flatten() },
      { status: 400 }
    );
  }
  const report = parsed.data;

  if (report.anon_id) {
    const anonRl = checkRateLimit(
      `error:anon:${report.anon_id}`,
      RL_ANON_LIMIT,
      RL_ANON_WINDOW_MS
    );
    if (!anonRl.allowed) {
      log.warn('error_report.rate_limited', {
        ip,
        scope: 'anon',
        limit: anonRl.limit,
      });
      return rateLimitResponse('anon', anonRl);
    }
  }

  const scrubbedStack = scrubPii(report.stack_trace);
  const scrubbedCtx = scrubContext(report.context);
  const fingerprint = fingerprintStack(
    scrubbedStack,
    report.os,
    report.recorder_version
  );
  const now = new Date().toISOString();

  if (!isSupabaseConfigured()) {
    log.error('error_report.not_configured', { ip });
    return NextResponse.json(
      {
        error: 'Supabase not configured',
        envVars: [
          'NEXT_PUBLIC_SUPABASE_URL',
          'NEXT_PUBLIC_SUPABASE_ANON_KEY',
          'SUPABASE_SERVICE_ROLE_KEY',
        ],
        details:
          'Error reporting writes to the error_reports table. Configure ' +
          'Supabase before pointing the recorder at this endpoint.',
      },
      { status: 503 }
    );
  }

  const supabase = getSupabaseServiceClient();
  if (!supabase) {
    return NextResponse.json(
      { error: 'Service client unavailable' },
      { status: 500 }
    );
  }

  const { data: existing, error: selectErr } = await supabase
    .from('error_reports')
    .select('count, first_seen')
    .eq('fingerprint', fingerprint)
    .maybeSingle();

  if (selectErr) {
    log.error('error_report.select_failed', {
      ip,
      fingerprint,
      code: selectErr.code,
    });
    return NextResponse.json(
      { error: 'DB read failed', details: selectErr.message },
      { status: 500 }
    );
  }

  if (existing) {
    const newCount = (existing.count ?? 0) + 1;
    const { error: updateErr } = await supabase
      .from('error_reports')
      .update({ count: newCount, last_seen: now })
      .eq('fingerprint', fingerprint);
    if (updateErr) {
      log.error('error_report.update_failed', {
        ip,
        fingerprint,
        code: updateErr.code,
      });
      return NextResponse.json(
        { error: 'DB update failed', details: updateErr.message },
        { status: 500 }
      );
    }
    log.info('error_report.duplicate', {
      ip,
      fingerprint,
      count: newCount,
      latency_ms: Date.now() - startedAt,
    });
    return NextResponse.json({
      fingerprint,
      count: newCount,
      duplicate: true,
      last_seen: now,
    });
  }

  const { error: insertErr } = await supabase.from('error_reports').insert({
    fingerprint,
    first_seen: now,
    last_seen: now,
    count: 1,
    recorder_version: report.recorder_version,
    os: report.os,
    severity: report.severity,
    stack_trace: scrubbedStack,
    context_json: scrubbedCtx,
    sample_anon_id: report.anon_id ?? null,
  });
  if (insertErr) {
    if (insertErr.code === '23505') {
      const { data: race } = await supabase
        .from('error_reports')
        .select('count, last_seen')
        .eq('fingerprint', fingerprint)
        .maybeSingle();
      return NextResponse.json({
        fingerprint,
        count: race?.count ?? 1,
        duplicate: true,
        last_seen: race?.last_seen ?? now,
      });
    }
    log.error('error_report.insert_failed', {
      ip,
      fingerprint,
      code: insertErr.code,
    });
    return NextResponse.json(
      { error: 'DB insert failed', details: insertErr.message },
      { status: 500 }
    );
  }

  log.info('error_report.accepted', {
    ip,
    fingerprint,
    recorder_version: report.recorder_version,
    os: report.os,
    severity: report.severity,
    latency_ms: Date.now() - startedAt,
  });
  return NextResponse.json({
    fingerprint,
    count: 1,
    duplicate: false,
    last_seen: now,
  });
}

export async function GET() {
  return NextResponse.json(
    {
      endpoint: '/api/error-report',
      method: 'POST',
      content_type: 'application/json',
      fields: [
        'recorder_version',
        'os',
        'stack_trace',
        'context (optional)',
        'anon_id (optional)',
        'severity (optional)',
      ],
      max_stack_bytes: 16384,
      see_also: '/api/error-report/summary',
    },
    { status: 200 }
  );
}
