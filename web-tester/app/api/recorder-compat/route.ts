/**
 * POST /api/recorder-compat
 *
 * G251 · Recorder pipeline Compatibility Check (HTTP wrapper)
 *
 * Body:  { recorder_version: string,  pipeline_version?: string }
 * Reply: { accepted: bool, recorder_version, matched_entry,
 *          reason, upgrade_url, min_pipeline, lint_version, deprecated }
 *
 * Reads bin/compat_matrix.json (bundled at build time) so the check
 * works without a database round-trip. The matrix is rolled forward
 * by appending entries (never editing in-place) so an old recorder
 * either matches a wildcard family entry or gets rejected with an
 * upgrade URL.
 *
 * This endpoint is the cheap pre-flight the recorder calls before
 * uploading a 200-500 MB tarball. The same logic also runs server-side
 * inside /api/upload-tarball once the tarball lands.
 *
 * Iron-law: no permissive defaults. Unknown version => 400 rejection
 * with the upgrade URL surfaced to the caller.
 */

import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import fs from 'node:fs';
import path from 'node:path';
import { checkRateLimit, clientIpFromHeaders } from '../../../lib/rate-limit';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const BodySchema = z.object({
  recorder_version: z
    .string()
    .min(1)
    .max(64)
    .regex(/^v?\d+\.\d+\.\d+(?:-[A-Za-z0-9.\-]+)?$/),
  pipeline_version: z
    .string()
    .min(1)
    .max(64)
    .regex(/^\d+\.\d+\.\d+(?:-[A-Za-z0-9.\-]+)?$/)
    .optional(),
});

interface MatrixEntry {
  min_pipeline?: string;
  lint_version?: number;
  deprecated?: boolean;
  deprecation_reason?: string;
  support_window_end?: string;
}

interface CompatMatrix {
  entries: Record<string, MatrixEntry>;
}

const DEFAULT_UPGRADE_URL =
  'https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/latest';
const RL_LIMIT = 120;
const RL_WINDOW_MS = 60 * 60_000;

// ---------------------------------------------------------------------------
// Matrix loading (cached at module load)
// ---------------------------------------------------------------------------

let matrixCache: CompatMatrix | null = null;
let matrixCacheTime = 0;
const MATRIX_CACHE_TTL_MS = 60_000;

function locateMatrixFile(): string {
  const explicit = process.env.COMPAT_MATRIX_PATH;
  if (explicit && fs.existsSync(explicit)) return explicit;

  let dir = process.cwd();
  for (let i = 0; i < 6; i += 1) {
    const candidate = path.join(dir, 'bin', 'compat_matrix.json');
    if (fs.existsSync(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error('compat_matrix.json not found (set COMPAT_MATRIX_PATH)');
}

function loadMatrix(): CompatMatrix {
  const now = Date.now();
  if (matrixCache && now - matrixCacheTime < MATRIX_CACHE_TTL_MS) {
    return matrixCache;
  }
  const fp = locateMatrixFile();
  const raw = fs.readFileSync(fp, 'utf-8');
  const parsed = JSON.parse(raw) as unknown;
  if (
    !parsed ||
    typeof parsed !== 'object' ||
    !('entries' in parsed) ||
    typeof (parsed as { entries: unknown }).entries !== 'object'
  ) {
    throw new Error(`matrix at ${fp} missing entries object`);
  }
  matrixCache = parsed as CompatMatrix;
  matrixCacheTime = now;
  return matrixCache;
}

export function __resetMatrixCache() {
  matrixCache = null;
  matrixCacheTime = 0;
}

// ---------------------------------------------------------------------------
// Version helpers
// ---------------------------------------------------------------------------

function matchesWildcard(version: string, pattern: string): boolean {
  if (!pattern.includes('.x') && !pattern.includes('*')) return false;
  const escaped = pattern
    .replace(/[-/\\^$+?.()|[\]{}]/g, '\\$&')
    .replace(/\\\.x/g, '\\.[A-Za-z0-9]+')
    .replace(/\\\*/g, '.*');
  return new RegExp(`^${escaped}$`).test(version);
}

function lookupEntry(
  version: string,
  matrix: CompatMatrix
): [string | null, MatrixEntry | null] {
  if (matrix.entries[version]) return [version, matrix.entries[version]];
  for (const [key, entry] of Object.entries(matrix.entries)) {
    if (matchesWildcard(version, key)) return [key, entry];
  }
  return [null, null];
}

function comparePre(a: string[], b: string[]): number {
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    const ai = a[i] ?? '';
    const bi = b[i] ?? '';
    if (ai === bi) continue;
    const aN = /^\d+$/.test(ai);
    const bN = /^\d+$/.test(bi);
    if (aN && bN) {
      const an = Number(ai);
      const bn = Number(bi);
      if (an !== bn) return an < bn ? -1 : 1;
      continue;
    }
    if (aN) return -1;
    if (bN) return 1;
    const am = ai.match(/^([A-Za-z]+)(\d+)?$/);
    const bm = bi.match(/^([A-Za-z]+)(\d+)?$/);
    if (am && bm && am[1] === bm[1]) {
      const ad = Number(am[2] ?? 0);
      const bd = Number(bm[2] ?? 0);
      if (ad !== bd) return ad < bd ? -1 : 1;
      continue;
    }
    return ai < bi ? -1 : 1;
  }
  return 0;
}

function pipelineAtLeast(actual: string, required: string): boolean {
  const norm = (s: string) =>
    s.trim().toLowerCase().startsWith('v') ? s.trim().slice(1) : s.trim();
  const split = (s: string): { semver: [number, number, number]; pre: string[] } => {
    const [base, pre] = norm(s).split('-', 2) as [string, string | undefined];
    const parts = base.split('.').map(Number);
    return {
      semver: [parts[0] ?? 0, parts[1] ?? 0, parts[2] ?? 0],
      pre: pre ? pre.split('.') : [],
    };
  };
  const A = split(actual);
  const B = split(required);
  for (let i = 0; i < 3; i += 1) {
    if (A.semver[i] !== B.semver[i]) return A.semver[i] > B.semver[i];
  }
  if (A.pre.length === 0 && B.pre.length === 0) return true;
  if (A.pre.length === 0) return true;
  if (B.pre.length === 0) return false;
  return comparePre(A.pre, B.pre) >= 0;
}

// ---------------------------------------------------------------------------
// Route handler
// ---------------------------------------------------------------------------

export async function POST(req: NextRequest) {
  const ip = clientIpFromHeaders(req.headers);
  const rl = checkRateLimit(`compat:${ip}`, RL_LIMIT, RL_WINDOW_MS);
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
          'X-RateLimit-Limit': String(rl.limit),
          'X-RateLimit-Remaining': String(rl.remaining),
          'X-RateLimit-Reset': String(Math.floor(rl.resetAt / 1000)),
        },
      }
    );
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 });
  }
  const parsed = BodySchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: 'Invalid body', details: parsed.error.flatten() },
      { status: 400 }
    );
  }
  const { recorder_version, pipeline_version } = parsed.data;

  let matrix: CompatMatrix;
  try {
    matrix = loadMatrix();
  } catch (err) {
    return NextResponse.json(
      {
        error: 'Matrix unavailable',
        details: err instanceof Error ? err.message : String(err),
      },
      { status: 500 }
    );
  }

  const [matchedKey, entry] = lookupEntry(recorder_version, matrix);
  if (!entry) {
    return NextResponse.json(
      {
        accepted: false,
        recorder_version,
        matched_entry: null,
        reason: `recorder version ${recorder_version} is not in the compatibility matrix. Please upgrade.`,
        upgrade_url: DEFAULT_UPGRADE_URL,
        min_pipeline: null,
        lint_version: null,
        deprecated: false,
      },
      { status: 400 }
    );
  }

  const today = new Date().toISOString().slice(0, 10);
  const swEnd = entry.support_window_end ?? null;
  const pastWindow =
    swEnd !== null && /^\d{4}-\d{2}-\d{2}$/.test(swEnd) && today > swEnd;
  if (entry.deprecated || pastWindow) {
    const bits: string[] = [`recorder version ${recorder_version} is deprecated`];
    if (entry.deprecation_reason) bits.push(`(${entry.deprecation_reason})`);
    if (pastWindow && swEnd) bits.push(`support ended ${swEnd}`);
    bits.push('please upgrade to the latest release.');
    return NextResponse.json(
      {
        accepted: false,
        recorder_version,
        matched_entry: matchedKey,
        reason: bits.join(' '),
        upgrade_url: DEFAULT_UPGRADE_URL,
        min_pipeline: entry.min_pipeline ?? null,
        lint_version: entry.lint_version ?? null,
        deprecated: true,
      },
      { status: 400 }
    );
  }

  if (pipeline_version && entry.min_pipeline) {
    if (!pipelineAtLeast(pipeline_version, entry.min_pipeline)) {
      return NextResponse.json(
        {
          accepted: false,
          recorder_version,
          matched_entry: matchedKey,
          reason: `pipeline ${pipeline_version} is older than the minimum (${entry.min_pipeline}) required by recorder ${recorder_version}.`,
          upgrade_url: DEFAULT_UPGRADE_URL,
          min_pipeline: entry.min_pipeline,
          lint_version: entry.lint_version ?? null,
          deprecated: false,
        },
        { status: 400 }
      );
    }
  }

  return NextResponse.json({
    accepted: true,
    recorder_version,
    matched_entry: matchedKey,
    reason: `recorder ${recorder_version} is supported (matched ${matchedKey})`,
    upgrade_url: DEFAULT_UPGRADE_URL,
    min_pipeline: entry.min_pipeline ?? null,
    lint_version: entry.lint_version ?? null,
    deprecated: false,
  });
}
