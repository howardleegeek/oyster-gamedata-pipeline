import Link from 'next/link';
import type { CatalogTarball } from '../types/database';
import { formatBytes, formatCents, formatHours } from '../lib/format';

interface TarballCardProps {
  t: CatalogTarball;
}

export function TarballCard({ t }: TarballCardProps) {
  return (
    <Link
      href={`/tarball/${t.id}`}
      className="card p-0 overflow-hidden block group hover:border-amber-accent/40 transition"
    >
      {/* Poster */}
      <div className="aspect-video bg-oyster-950/70 border-b border-oyster-800/40 relative overflow-hidden">
        {t.poster_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={t.poster_url}
            alt={t.title}
            className="w-full h-full object-cover group-hover:scale-105 transition"
          />
        ) : (
          <div className="w-full h-full grid place-items-center text-oyster-700 text-sm">
            no preview
          </div>
        )}
        <span
          className={`absolute top-2 left-2 tag tag-task-${t.mc_task_type} backdrop-blur-sm`}
        >
          {t.mc_task_type}
        </span>
        <span className="absolute top-2 right-2 tag bg-oyster-950/80 text-oyster-200 backdrop-blur-sm">
          D5 {(t.d5_score ?? 0).toFixed(2)}
        </span>
      </div>

      {/* Body */}
      <div className="p-4">
        <h3 className="font-semibold text-oyster-50 group-hover:text-amber-accent transition line-clamp-2">
          {t.title}
        </h3>
        <p className="mt-1.5 text-xs text-oyster-300 line-clamp-2">{t.description}</p>

        <div className="mt-3 grid grid-cols-3 gap-1 text-xs text-oyster-400">
          <div>
            <div className="uppercase tracking-wider">Size</div>
            <div className="text-oyster-100 font-semibold">{formatBytes(t.size_bytes)}</div>
          </div>
          <div>
            <div className="uppercase tracking-wider">Length</div>
            <div className="text-oyster-100 font-semibold">
              {formatHours(t.duration_seconds / 3600)}
            </div>
          </div>
          <div>
            <div className="uppercase tracking-wider">Verdict</div>
            <div>
              <span className={`tag tag-${t.d5_verdict}`}>{t.d5_verdict}</span>
            </div>
          </div>
        </div>

        <div className="mt-4 flex items-end justify-between">
          <div>
            <div className="text-xs text-oyster-400 uppercase tracking-wider">Price</div>
            <div className="text-2xl font-bold text-amber-accent">
              {formatCents(t.price_cents)}
            </div>
          </div>
          <span className="btn-secondary py-1.5 px-3 text-sm">View →</span>
        </div>
      </div>
    </Link>
  );
}
