/**
 * GET /api/recorder-update?current=v0.28.0-rc19.0.1
 *
 * G250 · Update Server Endpoint
 *
 * Proxies the GitHub Releases API for `howardleegeek/oyster-gamedata-pipeline`
 * and returns:
 *
 *   {
 *     latest:         "v0.28.0-rc19.x",
 *     installer_url:  "https://github.com/.../OysterRecorder-setup.exe",
 *     release_notes:  "...",
 *     force:          false,
 *     current:        "v0.28.0-rc19.0.1",
 *     update_available: true
 *   }
 *
 * Cache: 5 min in-process per Vercel instance. We also send the GitHub
 * Cache-Control to the client (recorder) so the on-device updater
 * does not hammer us either. The recorder TTL is independent.
 *
 * Iron-law (Howard 2026-05-07): no fallback `latest` string. If GitHub
 * fails AND the cache is empty, we return 502 with diagnostic detail
 * so ops can see the upstream failure.
 *
 * Security:
 *   - Whitelisted output shape — never echoes raw GH JSON fields.
 *   - GH token (server-only env GITHUB_TOKEN) used only if set; never
 *     surfaced to the client.
 *   - Per-IP rate-limit (60 / hour) — recorder default poll is 1/day
 *     so this is a generous abuse-prevention cap, not a UX wall.
 */

import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { checkRateLimit, clientIpFromHeaders } from '../../../lib/rate-limit';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const REPO_DEFAULT = 'howardleegeek/oyster-gamedata-pipeline';
const GH_API = (repo: string) =>
  `https://api.github.com/repos/${repo}/releases/latest`;
const CACHE_TTL_MS = 5 * 60 * 1000;
const HTTP_TIMEOUT_MS = 8000;
const MAX_GH_BYTES = 1 << 20;

const INSTALLER_SUFFIXES = [
  '-setup.exe',
  '.msi',
  '.exe',
  '.pkg',
  '.dmg',
] as const;

const FORCE_TOKEN = '[FORCE]';

const RL_LIMIT = 60;
const RL_WINDOW_MS = 60 * 60_000;

// ---------------------------------------------------------------------------
// Schema
// ---------------------------------------------------------------------------

const QuerySchema = z.object({
  current: z
    .string()
    .min(1)
    .max(64)
    .regex(
      /^v?\d+\.\d+\.\d+(?:-[A-Za-z0-9.\-]+)?$/,
      'current must be semver-like, e.g. v0.28.0-rc19.0.1'
    ),
  repo: z
    .string()
    .regex(/^[A-Za-z0-9.\-_]+\/[A-Za-z0-9.\-_]+$/)
    .optional(),
});

// ---------------------------------------------------------------------------
// In-process cache (per-instance)
// ---------------------------------------------------------------------------

interface CacheEntry {
  payload: GitHubLatest;
  expiresAt: number;
}

const cache = new Map<string, CacheEntry>();

function cacheGet(key: string): GitHubLatest | null {
  const e = cache.get(key);
  if (!e) return null;
  if (e.expiresAt < Date.now()) {
    cache.delete(key);
    return null;
  }
  return e.payload;
}

function cacheSet(key: string, payload: GitHubLatest, ttlMs = CACHE_TTL_MS) {
  cache.set(key, { payload, expiresAt: Date.now() + ttlMs });
}

export function __clearCache() {
  cache.clear();
}

// ---------------------------------------------------------------------------
// GitHub fetch
// ---------------------------------------------------------------------------

interface GitHubAsset {
  name: string;
  browser_download_url: string;
}

interface GitHubLatest {
  tag_name: string;
  body?: string | null;
  assets?: GitHubAsset[];
}

async function fetchLatestFromGitHub(repo: string): Promise<GitHubLatest> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), HTTP_TIMEOUT_MS);

  const headers: Record<string, string> = {
    Accept: 'application/vnd.github+json',
    'User-Agent':
      'oyster-update-server/1.0 (+howardleegeek/oyster-gamedata-pipeline)',
    'X-GitHub-Api-Version': '2022-11-28',
  };
  const token = process.env.GITHUB_TOKEN || '';
  if (token) headers['Authorization'] = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(GH_API(repo), { headers, signal: controller.signal });
  } catch (err) {
    clearTimeout(timer);
    throw new Error(
      `GitHub unreachable: ${err instanceof Error ? err.message : String(err)}`
    );
  }
  clearTimeout(timer);

  if (!res.ok) {
    throw new Error(`GitHub HTTP ${res.status} for ${repo}`);
  }

  const reader = res.body?.getReader();
  if (!reader) {
    throw new Error('GitHub response missing body');
  }
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (value) {
      total += value.byteLength;
      if (total > MAX_GH_BYTES) {
        try {
          await reader.cancel();
        } catch {
          /* ignore */
        }
        throw new Error(`GitHub response exceeded ${MAX_GH_BYTES} bytes`);
      }
      chunks.push(value);
    }
  }
  const text = Buffer.concat(chunks.map((c) => Buffer.from(c))).toString(
    'utf-8'
  );
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    throw new Error(
      `GitHub returned malformed JSON: ${err instanceof Error ? err.message : String(err)}`
    );
  }
  if (typeof parsed !== 'object' || parsed === null) {
    throw new Error('GitHub returned non-object payload');
  }
  const payload = parsed as Partial<GitHubLatest>;
  if (typeof payload.tag_name !== 'string' || payload.tag_name.length === 0) {
    throw new Error('GitHub payload missing tag_name');
  }
  return payload as GitHubLatest;
}

// ---------------------------------------------------------------------------
// Helpers — installer selection, force-detect, version compare
// ---------------------------------------------------------------------------

function pickInstaller(assets: GitHubAsset[] | undefined): string {
  if (!Array.isArray(assets)) return '';
  for (const suf of INSTALLER_SUFFIXES) {
    for (const a of assets) {
      if (
        a &&
        typeof a.name === 'string' &&
        typeof a.browser_download_url === 'string' &&
        a.name.toLowerCase().endsWith(suf)
      ) {
        return a.browser_download_url;
      }
    }
  }
  return '';
}

function detectForce(body?: string | null): boolean {
  if (!body) return false;
  for (const line of body.split(/\r?\n/)) {
    if (line.trim().toUpperCase().startsWith(FORCE_TOKEN)) return true;
  }
  return false;
}

interface VersionParts {
  semver: [number, number, number];
  pre: string | null;
}

function parseVersion(raw: string): VersionParts | null {
  const m = /^v?(\d+)\.(\d+)\.(\d+)(?:-([A-Za-z0-9.\-]+))?$/.exec(raw.trim());
  if (!m) return null;
  return {
    semver: [Number(m[1]), Number(m[2]), Number(m[3])],
    pre: m[4] ?? null,
  };
}

function comparePre(a: string, b: string): number {
  const pa = a.split('.');
  const pb = b.split('.');
  for (let i = 0; i < Math.max(pa.length, pb.length); i += 1) {
    const ai = pa[i] ?? '';
    const bi = pb[i] ?? '';
    if (ai === bi) continue;
    const aNum = /^\d+$/.test(ai);
    const bNum = /^\d+$/.test(bi);
    if (aNum && bNum) {
      const an = Number(ai);
      const bn = Number(bi);
      if (an !== bn) return an < bn ? -1 : 1;
      continue;
    }
    if (aNum) return -1;
    if (bNum) return 1;
    const am = /^([A-Za-z]+)(\d+)?$/.exec(ai);
    const bm = /^([A-Za-z]+)(\d+)?$/.exec(bi);
    if (am && bm) {
      if (am[1] !== bm[1]) return am[1] < bm[1] ? -1 : 1;
      const ad = Number(am[2] ?? 0);
      const bd = Number(bm[2] ?? 0);
      if (ad !== bd) return ad < bd ? -1 : 1;
      continue;
    }
    return ai < bi ? -1 : 1;
  }
  return 0;
}

export function isNewer(latest: string, current: string): boolean {
  const L = parseVersion(latest);
  const C = parseVersion(current);
  if (!L || !C) return false;
  for (let i = 0; i < 3; i += 1) {
    if (L.semver[i] !== C.semver[i]) return L.semver[i] > C.semver[i];
  }
  if (L.pre === null && C.pre === null) return false;
  if (L.pre === null) return true;
  if (C.pre === null) return false;
  return comparePre(L.pre, C.pre) > 0;
}

// ---------------------------------------------------------------------------
// Route handler
// ---------------------------------------------------------------------------

export async function GET(req: NextRequest) {
  const ip = clientIpFromHeaders(req.headers);

  const rl = checkRateLimit(`update:${ip}`, RL_LIMIT, RL_WINDOW_MS);
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

  const url = new URL(req.url);
  const raw = Object.fromEntries(url.searchParams.entries());
  const parsed = QuerySchema.safeParse(raw);
  if (!parsed.success) {
    return NextResponse.json(
      { error: 'Invalid query', details: parsed.error.flatten() },
      { status: 400 }
    );
  }
  const repo = parsed.data.repo ?? REPO_DEFAULT;
  const cacheKey = `latest:${repo}`;

  let payload = cacheGet(cacheKey);
  let cacheHit = true;
  if (!payload) {
    cacheHit = false;
    try {
      payload = await fetchLatestFromGitHub(repo);
      cacheSet(cacheKey, payload);
    } catch (err) {
      return NextResponse.json(
        {
          error: 'Upstream unavailable',
          details: err instanceof Error ? err.message : String(err),
        },
        { status: 502 }
      );
    }
  }

  const latest = payload.tag_name;
  const installer = pickInstaller(payload.assets);
  const notes = typeof payload.body === 'string' ? payload.body : '';
  const force = detectForce(notes);
  const updateAvailable = isNewer(latest, parsed.data.current);

  return NextResponse.json(
    {
      latest,
      installer_url: installer,
      release_notes: notes,
      force,
      current: parsed.data.current,
      update_available: updateAvailable,
    },
    {
      headers: {
        'Cache-Control': 'public, max-age=300, must-revalidate',
        'X-Update-Cache': cacheHit ? 'HIT' : 'MISS',
      },
    }
  );
}
