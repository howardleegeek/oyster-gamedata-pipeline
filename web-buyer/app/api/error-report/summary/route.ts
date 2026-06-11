/**
 * GET /api/error-report/summary?since=24h&limit=50
 *
 * G232 · Ops dashboard endpoint — returns top crashes by count.
 *
 * Query params:
 *   since   — time window, e.g. 24h, 7d, 30m (default: all time)
 *   limit   — 1..500 (default: 50)
 *
 * Reply:
 *   {
 *     since:    "2026-05-12T16:14:00.000Z" | null,
 *     count:    23,
 *     rows: [
 *       { fingerprint, first_seen, last_seen, count,
 *         recorder_version, os, severity, stack_trace_preview }
 *     ]
 *   }
 *
 * Auth: this endpoint exposes scrubbed PII-stripped crash data only and
 * is intended for the ops dashboard. We DO NOT require auth in this
 * version because the table content is already PII-free. If we later
 * add fields that could re-identify (e.g. game session ids), revisit.
 *
 * Iron-law: returns 503 when Supabase is not configured.
 */

import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { parseSince } from '../../../../lib/error-report';
import { getSupabaseServiceClient } from '../../../../lib/supabase-server';
import { isSupabaseConfigured } from '../../../../lib/env';
import { checkRateLimit, clientIpFromHeaders } from '../../../../lib/rate-limit';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const QuerySchema = z.object({
  since: z
    .string()
    .regex(/^\d+[smhd]$/, 'since must look like 24h, 7d, 30m, 90s')
    .optional(),
  limit: z.coerce.number().int().min(1).max(500).optional().default(50),
});

const RL_LIMIT = 120;
const RL_WINDOW_MS = 60 * 60_000;

export async function GET(req: NextRequest) {
  const ip = clientIpFromHeaders(req.headers);
  const rl = checkRateLimit(`error_summary:${ip}`, RL_LIMIT, RL_WINDOW_MS);
  if (!rl.allowed) {
    return NextResponse.json(
      {
        error: 'Rate limit exceeded',
        limit: rl.limit,
        remaining: rl.remaining,
        resetAt: new Date(rl.resetAt).toISOString(),
      },
      {
        status: 429,
        headers: {
          'Retry-After': String(
            Math.max(1, Math.ceil((rl.resetAt - Date.now()) / 1000))
          ),
        },
      }
    );
  }

  const url = new URL(req.url);
  const raw = Object.fromEntries(url.searchParams.entries());
  const parsed = QuerySchema.safeParse(raw);
  if (!parsed.success) {
    return NextResponse.json(
      { error: 'Invalid query', details: parsed.error.flatten() },
      { status: 400 }
    );
  }
  const cutoff = parseSince(parsed.data.since ?? null);
  const limit = parsed.data.limit ?? 50;

  if (!isSupabaseConfigured()) {
    return NextResponse.json(
      {
        error: 'Supabase not configured',
        envVars: [
          'NEXT_PUBLIC_SUPABASE_URL',
          'NEXT_PUBLIC_SUPABASE_ANON_KEY',
          'SUPABASE_SERVICE_ROLE_KEY',
        ],
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

  let q = supabase
    .from('error_reports')
    .select(
      'fingerprint, first_seen, last_seen, count, recorder_version, os, severity, stack_trace'
    )
    .order('count', { ascending: false })
    .order('last_seen', { ascending: false })
    .limit(limit);

  if (cutoff) {
    q = q.gte('last_seen', cutoff.toISOString());
  }

  const { data, error: dbErr } = await q;
  if (dbErr) {
    return NextResponse.json(
      { error: 'DB read failed', details: dbErr.message },
      { status: 500 }
    );
  }

  const rows = (data ?? []).map((r: Record<string, unknown>) => ({
    fingerprint: r.fingerprint,
    first_seen: r.first_seen,
    last_seen: r.last_seen,
    count: r.count,
    recorder_version: r.recorder_version,
    os: r.os,
    severity: r.severity,
    stack_trace_preview:
      typeof r.stack_trace === 'string' ? r.stack_trace.slice(0, 240) : '',
  }));

  return NextResponse.json({
    since: cutoff ? cutoff.toISOString() : null,
    count: rows.length,
    rows,
  });
}
