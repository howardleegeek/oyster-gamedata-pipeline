import Link from 'next/link';
import { TarballCard } from '../../components/TarballCard';
import { CatalogFiltersPanel } from '../../components/CatalogFilters';
import { NotConfigured } from '../../components/NotConfigured';
import { fetchCatalog, FilterSchema, CatalogNotConfiguredError } from '../../lib/catalog';
import { isSupabaseConfigured } from '../../lib/env';
import { readCartCookie } from '../../lib/cart-cookie';

export const dynamic = 'force-dynamic';

interface PageProps {
  searchParams: Record<string, string | string[] | undefined>;
}

export default async function BrowsePage({ searchParams }: PageProps) {
  // Howard 2026-05-07 IRON-LAW: hard-gate. The previous DEV MODE branch
  // imported `sampleCatalog()` and rendered 5 fabricated tarballs; that
  // is fake data shipping in production source.
  if (!isSupabaseConfigured()) {
    return (
      <NotConfigured
        service="Supabase"
        envVars={['NEXT_PUBLIC_SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_ANON_KEY', 'SUPABASE_SERVICE_ROLE_KEY']}
        docsUrl="/docs#supabase-setup"
      />
    );
  }

  // Coerce searchParams to single strings (we never use arrays here).
  const flat = Object.fromEntries(
    Object.entries(searchParams).map(([k, v]) => [k, Array.isArray(v) ? v[0] : v]),
  );
  const parsed = FilterSchema.safeParse(flat);
  const filters = parsed.success ? parsed.data : {};

  let rows: Awaited<ReturnType<typeof fetchCatalog>>['rows'];
  try {
    const result = await fetchCatalog(filters);
    rows = result.rows;
  } catch (err) {
    if (err instanceof CatalogNotConfiguredError) {
      return (
        <NotConfigured
          service="Supabase"
          envVars={err.envVars}
          docsUrl="/docs#supabase-setup"
        />
      );
    }
    throw err;
  }

  const cartIds = new Set(readCartCookie());

  return (
    <div className="max-w-6xl mx-auto px-4 py-10">
      <div className="flex items-end justify-between mb-8 gap-3">
        <div>
          <h1 className="text-3xl font-bold">Catalog</h1>
          <p className="text-sm text-oyster-300 mt-1">
            {rows.length} tarball{rows.length === 1 ? '' : 's'} available
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
