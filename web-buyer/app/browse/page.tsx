import Link from 'next/link';
import { TarballCard } from '../../components/TarballCard';
import { CatalogFiltersPanel } from '../../components/CatalogFilters';
import { fetchCatalog, FilterSchema } from '../../lib/catalog';
import { readCartCookie } from '../../lib/cart-cookie';

export const dynamic = 'force-dynamic';

interface PageProps {
  searchParams: Record<string, string | string[] | undefined>;
}

export default async function BrowsePage({ searchParams }: PageProps) {
  // Coerce searchParams to single strings (we never use arrays here).
  const flat = Object.fromEntries(
    Object.entries(searchParams).map(([k, v]) => [k, Array.isArray(v) ? v[0] : v]),
  );
  const parsed = FilterSchema.safeParse(flat);
  const filters = parsed.success ? parsed.data : {};

  const { rows, liveMode } = await fetchCatalog(filters);
  const cartIds = new Set(readCartCookie());

  return (
    <div className="max-w-6xl mx-auto px-4 py-10">
      <div className="flex items-end justify-between mb-8 gap-3">
        <div>
          <h1 className="text-3xl font-bold">Catalog</h1>
          <p className="text-sm text-oyster-300 mt-1">
            {rows.length} tarball{rows.length === 1 ? '' : 's'} available
            {!liveMode && (
              <span className="ml-2 text-amber-accent">[DEV MODE: showing sample data]</span>
            )}
          </p>
        </div>
        <Link href="/cart" className="btn-ghost">
          View cart →
        </Link>
      </div>

      <div className="grid md:grid-cols-[260px_1fr] gap-6">
        <CatalogFiltersPanel />

        {/* Results */}
        {rows.length === 0 ? (
          <div className="card p-10 text-center text-oyster-300">
            <h3 className="text-lg font-semibold text-oyster-100 mb-2">No tarballs match.</h3>
            <p className="mb-4 text-sm">Loosen your filters or reset.</p>
            <Link href="/browse" className="btn-secondary">
              Reset filters
            </Link>
          </div>
        ) : (
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {rows.map((t) => (
              <div key={t.id} className="relative">
                <TarballCard t={t} />
                {cartIds.has(t.id) && (
                  <span className="absolute top-3 right-3 tag bg-emerald-500/30 text-emerald-100 backdrop-blur-sm">
                    in cart
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
