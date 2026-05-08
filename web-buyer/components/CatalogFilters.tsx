'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useTransition } from 'react';

const TASK_TYPES = [
  'survival',
  'creative',
  'redstone',
  'pvp',
  'mining',
  'building',
  'exploration',
  'farming',
] as const;

const SIZE_BUCKETS: Array<{ label: string; min?: number; max?: number }> = [
  { label: 'any size' },
  { label: '< 2 GB', max: 2 * 1024 * 1024 * 1024 },
  { label: '2–5 GB', min: 2 * 1024 * 1024 * 1024, max: 5 * 1024 * 1024 * 1024 },
  { label: '> 5 GB', min: 5 * 1024 * 1024 * 1024 },
];

const PRICE_BUCKETS: Array<{ label: string; min?: number; max?: number }> = [
  { label: 'any price' },
  { label: '< $50', max: 5000 },
  { label: '$50 – $150', min: 5000, max: 15000 },
  { label: '> $150', min: 15000 },
];

/**
 * Filter panel for /browse. URL-driven so server components re-render on
 * change, no client-side state mismatches.
 */
export function CatalogFiltersPanel() {
  const router = useRouter();
  const params = useSearchParams();
  const [pending, startTransition] = useTransition();

  const setParam = useCallback(
    (next: Record<string, string | undefined>) => {
      const merged = new URLSearchParams(params.toString());
      for (const [k, v] of Object.entries(next)) {
        if (v == null || v === '') merged.delete(k);
        else merged.set(k, v);
      }
      startTransition(() => {
        router.push(`/browse?${merged.toString()}`);
      });
    },
    [params, router],
  );

  const verdict = params.get('verdict') ?? 'accepted';
  const taskType = params.get('task_type') ?? '';
  const minSize = params.get('min_size_bytes');
  const maxSize = params.get('max_size_bytes');
  const minPrice = params.get('min_price_cents');
  const maxPrice = params.get('max_price_cents');

  const sizeIdx = SIZE_BUCKETS.findIndex(
    (b) =>
      String(b.min ?? '') === (minSize ?? '') && String(b.max ?? '') === (maxSize ?? ''),
  );
  const priceIdx = PRICE_BUCKETS.findIndex(
    (b) =>
      String(b.min ?? '') === (minPrice ?? '') && String(b.max ?? '') === (maxPrice ?? ''),
  );

  return (
    <aside className="card p-4 space-y-5 sticky top-20 self-start">
      <h2 className="font-semibold text-oyster-50">Filters</h2>

      {/* D5 verdict */}
      <div>
        <label className="text-xs uppercase tracking-wider text-oyster-400 font-semibold">
          D5 verdict
        </label>
        <select
          className="input mt-1"
          value={verdict}
          onChange={(e) =>
            setParam({ verdict: e.target.value === 'accepted' ? undefined : e.target.value })
          }
          disabled={pending}
        >
          <option value="accepted">Accepted only (default)</option>
          <option value="pending">Pending</option>
          <option value="rejected">Rejected</option>
        </select>
      </div>

      {/* MC task type */}
      <div>
        <label className="text-xs uppercase tracking-wider text-oyster-400 font-semibold">
          Task type
        </label>
        <select
          className="input mt-1"
          value={taskType}
          onChange={(e) => setParam({ task_type: e.target.value || undefined })}
          disabled={pending}
        >
          <option value="">All</option>
          {TASK_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </div>

      {/* Size */}
      <div>
        <label className="text-xs uppercase tracking-wider text-oyster-400 font-semibold">
          Size
        </label>
        <div className="mt-1 grid grid-cols-2 gap-1.5">
          {SIZE_BUCKETS.map((b, i) => (
            <button
              key={b.label}
              type="button"
              className={`px-2.5 py-1.5 rounded-md text-sm border transition ${
                sizeIdx === i
                  ? 'bg-amber-accent text-oyster-950 border-amber-accent'
                  : 'border-oyster-700/50 hover:bg-oyster-800/40'
              }`}
              onClick={() =>
                setParam({
                  min_size_bytes: b.min == null ? undefined : String(b.min),
                  max_size_bytes: b.max == null ? undefined : String(b.max),
                })
              }
              disabled={pending}
            >
              {b.label}
            </button>
          ))}
        </div>
      </div>

      {/* Price */}
      <div>
        <label className="text-xs uppercase tracking-wider text-oyster-400 font-semibold">
          Price
        </label>
        <div className="mt-1 grid grid-cols-2 gap-1.5">
          {PRICE_BUCKETS.map((b, i) => (
            <button
              key={b.label}
              type="button"
              className={`px-2.5 py-1.5 rounded-md text-sm border transition ${
                priceIdx === i
                  ? 'bg-amber-accent text-oyster-950 border-amber-accent'
                  : 'border-oyster-700/50 hover:bg-oyster-800/40'
              }`}
              onClick={() =>
                setParam({
                  min_price_cents: b.min == null ? undefined : String(b.min),
                  max_price_cents: b.max == null ? undefined : String(b.max),
                })
              }
              disabled={pending}
            >
              {b.label}
            </button>
          ))}
        </div>
      </div>

      {/* Reset */}
      <button
        type="button"
        className="btn-ghost w-full"
        onClick={() => router.push('/browse')}
        disabled={pending}
      >
        Reset filters
      </button>
    </aside>
  );
}
