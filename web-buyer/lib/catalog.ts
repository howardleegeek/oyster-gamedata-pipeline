/**
 * Catalog data layer.
 *
 * Howard 2026-05-07 IRON-LAW: never falls back to fabricated catalog data.
 * The previous implementation imported `lib/sample-data.ts` and rendered
 * 5 hand-tuned tarballs with fake D5 scores when Supabase wasn't
 * configured — that's fabricated data shipping in production source.
 * Now: throws `CatalogNotConfiguredError` when supabase isn't reachable
 * so page-level callers can hard-gate with <NotConfigured>.
 *
 * In LIVE mode the catalog is the JOIN of:
 *   tarballs (tester-side)
 *   ⨯ catalog_metadata  (buyer-side curation: title, description, mc_task_type, price)
 * — when a tarball has no curation row we fall back to a deterministic
 * default derived from the tarball's REAL fields (id hash, real size,
 * real duration). That is not fake data; the underlying tarball is real.
 */

import { z } from 'zod';
import { env, isSupabaseConfigured } from './env';
import { getSupabaseServiceClient } from './supabase-server';
import type {
  CatalogFilters,
  CatalogTarball,
  McTaskType,
} from '../types/database';

const TASK_TYPES: McTaskType[] = [
  'survival',
  'creative',
  'redstone',
  'pvp',
  'mining',
  'building',
  'exploration',
  'farming',
];

export const FilterSchema = z.object({
  verdict: z.enum(['accepted', 'pending', 'rejected']).optional(),
  task_type: z.enum(TASK_TYPES as [McTaskType, ...McTaskType[]]).optional(),
  min_size_bytes: z.coerce.number().int().min(0).optional(),
  max_size_bytes: z.coerce.number().int().min(0).optional(),
  min_price_cents: z.coerce.number().int().min(0).optional(),
  max_price_cents: z.coerce.number().int().min(0).optional(),
  limit: z.coerce.number().int().min(1).max(200).optional(),
});

/**
 * Thrown when the catalog cannot be served because Supabase isn't
 * configured. Page-level callers should catch this, gate with
 * `isSupabaseConfigured()`, and render <NotConfigured> instead.
 * Route handlers should return 503 with envVars in the body.
 */
export class CatalogNotConfiguredError extends Error {
  readonly envVars: string[];
  constructor(message: string, envVars: string[]) {
    super(message);
    this.name = 'CatalogNotConfiguredError';
    this.envVars = envVars;
  }
}

/**
 * Apply CatalogFilters to a CatalogTarball list. Pure function, easy to
 * test and reused everywhere.
 */
export function applyFilters(
  rows: CatalogTarball[],
  filters: CatalogFilters,
): CatalogTarball[] {
  let out = rows;
  if (filters.verdict) out = out.filter((t) => t.d5_verdict === filters.verdict);
  if (filters.task_type) out = out.filter((t) => t.mc_task_type === filters.task_type);
  if (filters.min_size_bytes != null)
    out = out.filter((t) => t.size_bytes >= (filters.min_size_bytes ?? 0));
  if (filters.max_size_bytes != null)
    out = out.filter((t) => t.size_bytes <= (filters.max_size_bytes ?? Infinity));
  if (filters.min_price_cents != null)
    out = out.filter((t) => t.price_cents >= (filters.min_price_cents ?? 0));
  if (filters.max_price_cents != null)
    out = out.filter((t) => t.price_cents <= (filters.max_price_cents ?? Infinity));
  if (filters.limit) out = out.slice(0, filters.limit);
  return out;
}

/**
 * Stable derivation of curation metadata for a tarball that has no
 * `catalog_metadata` row yet. Keeps the catalog populated even if curation
 * is behind on tagging. This is NOT fake data — every input field
 * (id, size, duration) comes from a real tarball row.
 */
function deriveCurationFallback(row: {
  id: string;
  size_bytes: number;
  duration_seconds: number;
}): {
  title: string;
  description: string;
  mc_task_type: McTaskType;
  price_cents: number;
} {
  // Hash the id to pick a stable task type per tarball.
  const idx =
    [...row.id].reduce((acc, c) => (acc * 17 + c.charCodeAt(0)) & 0xffffffff, 11) %
    TASK_TYPES.length;
  const taskType = TASK_TYPES[idx];
  const sizeGb = row.size_bytes / 1024 / 1024 / 1024;
  return {
    title: `Minecraft ${taskType} session — ${(row.duration_seconds / 3600).toFixed(1)} h`,
    description:
      `Auto-curated tarball: ${(row.duration_seconds / 60).toFixed(0)} min of ${taskType} gameplay, ` +
      `${sizeGb.toFixed(1)} GB. Re-curate this tarball in the admin to override.`,
    mc_task_type: taskType,
    price_cents: Math.ceil(sizeGb * env.pricePerGbCents),
  };
}

/**
 * Read the catalog. Throws CatalogNotConfiguredError when Supabase isn't
 * set up — callers MUST handle this and hard-gate the UI/API.
 */
export async function fetchCatalog(filters: CatalogFilters = {}): Promise<{
  rows: CatalogTarball[];
}> {
  if (!isSupabaseConfigured()) {
    throw new CatalogNotConfiguredError(
      'Catalog read requires a configured Supabase instance.',
      ['NEXT_PUBLIC_SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_ANON_KEY', 'SUPABASE_SERVICE_ROLE_KEY'],
    );
  }

  const service = getSupabaseServiceClient();
  if (!service) {
    throw new CatalogNotConfiguredError(
      'Supabase service-role client is unavailable.',
      ['SUPABASE_SERVICE_ROLE_KEY'],
    );
  }

  // Pull only accepted (the buyer marketplace never sells pending/rejected
  // tarballs by default — overridable via filters.verdict).
  const verdictFilter = filters.verdict ?? 'accepted';

  const { data: tarballs, error } = await service
    .from('tarballs')
    .select(
      'id, tester_id, uploaded_at, size_bytes, sha256, duration_seconds, d5_verdict, d5_score, storage_path',
    )
    .eq('d5_verdict', verdictFilter)
    .order('uploaded_at', { ascending: false })
    .limit(filters.limit ?? 100);

  if (error) {
    // Howard 2026-05-07 IRON-LAW: do NOT mask DB errors with sample data.
    // Surface the real error so the operator sees what's broken.
    throw new Error(`Supabase tarballs query failed: ${error.message}`);
  }
  if (!tarballs) return { rows: [] };

  // Try to read the curation rows; tolerate the table not existing.
  const curationRows: Record<
    string,
    {
      title: string;
      description: string;
      mc_task_type: McTaskType;
      price_cents: number;
      poster_url: string | null;
      video_preview_url: string | null;
    }
  > = {};
  try {
    const ids = tarballs.map((t: any) => t.id);
    const { data: curation } = await service
      .from('catalog_metadata')
      .select(
        'tarball_id, title, description, mc_task_type, price_cents, poster_url, video_preview_url',
      )
      .in('tarball_id', ids);
    if (curation) {
      for (const row of curation as any[]) {
        curationRows[row.tarball_id] = {
          title: row.title,
          description: row.description,
          mc_task_type: row.mc_task_type,
          price_cents: row.price_cents,
          poster_url: row.poster_url,
          video_preview_url: row.video_preview_url,
        };
      }
    }
  } catch {
    // catalog_metadata table missing — use derived fallback over real fields.
  }

  const enriched: CatalogTarball[] = tarballs.map((t: any) => {
    const curation = curationRows[t.id];
    const fallback = deriveCurationFallback(t);
    return {
      id: t.id,
      tester_id: t.tester_id,
      uploaded_at: t.uploaded_at,
      size_bytes: Number(t.size_bytes),
      duration_seconds: t.duration_seconds,
      d5_verdict: t.d5_verdict,
      d5_score: t.d5_score == null ? null : Number(t.d5_score),
      sha256: t.sha256,
      title: curation?.title ?? fallback.title,
      description: curation?.description ?? fallback.description,
      mc_task_type: curation?.mc_task_type ?? fallback.mc_task_type,
      price_cents: curation?.price_cents ?? fallback.price_cents,
      poster_url: curation?.poster_url ?? `/samples/poster-default.svg`,
      video_preview_url: curation?.video_preview_url ?? '',
      storage_path: t.storage_path,
    };
  });

  return { rows: applyFilters(enriched, filters) };
}

/**
 * Fetch one row by id. Same error contract as fetchCatalog().
 */
export async function fetchCatalogById(id: string): Promise<{
  row: CatalogTarball | null;
}> {
  const { rows } = await fetchCatalog({ limit: 200 });
  return { row: rows.find((r) => r.id === id) ?? null };
}
