import Link from 'next/link';
import { fetchCatalog, CatalogNotConfiguredError } from '../../lib/catalog';
import { readCartCookie } from '../../lib/cart-cookie';
import { getSupabaseServerClient, getSupabaseServiceClient } from '../../lib/supabase-server';
import { isSupabaseConfigured, env } from '../../lib/env';
import { formatBytes, formatCents, totalCents } from '../../lib/format';
import { NotConfigured } from '../../components/NotConfigured';
import type { CatalogTarball } from '../../types/database';
import { CartCheckoutButton } from './CheckoutButton';

export const dynamic = 'force-dynamic';

async function loadCart(): Promise<{
  items: CatalogTarball[];
  signedIn: boolean;
}> {
  const { rows } = await fetchCatalog({ limit: 200 });
  const byId = new Map(rows.map((r) => [r.id, r]));

  let cartIds: string[] = [];
  let signedIn = false;

  const supabase = getSupabaseServerClient();
  const { data } = supabase ? await supabase.auth.getUser() : { data: { user: null } };
  if (data?.user) {
    signedIn = true;
    const service = getSupabaseServiceClient();
    if (service) {
      const { data: cartRows } = await service
        .from('cart_items')
        .select('tarball_id')
        .eq('buyer_id', data.user.id);
      cartIds = (cartRows ?? []).map((r: any) => r.tarball_id);
    }
  } else {
    cartIds = readCartCookie();
  }

  const items = cartIds.map((id) => byId.get(id)).filter(Boolean) as CatalogTarball[];
  return { items, signedIn };
}

export default async function CartPage() {
  // Howard 2026-05-07 IRON-LAW: hard-gate. Cart contents must come from
  // a real catalog — no fabricated entries.
  if (!isSupabaseConfigured()) {
    return (
      <NotConfigured
        service="Supabase"
        envVars={['NEXT_PUBLIC_SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_ANON_KEY', 'SUPABASE_SERVICE_ROLE_KEY']}
        docsUrl="/docs#supabase-setup"
      />
    );
  }

  let cartState: Awaited<ReturnType<typeof loadCart>>;
  try {
    cartState = await loadCart();
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
  const { items, signedIn } = cartState;

  const subtotalCents = totalCents(
    items.map((i) => i.size_bytes),
    env.pricePerGbCents,
    0,
  );

  return (
    <div className="max-w-5xl mx-auto px-4 py-10">
      <div className="flex items-end justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold">Your cart</h1>
          <p className="text-sm text-oyster-300 mt-1">
            {items.length} tarball{items.length === 1 ? '' : 's'}
          </p>
        </div>
        <Link href="/browse" className="btn-ghost">← Keep browsing</Link>
      </div>

      {items.length === 0 ? (
        <div className="card p-10 text-center text-oyster-300">
          <h3 className="text-lg font-semibold text-oyster-100 mb-2">Cart is empty.</h3>
          <p className="mb-4 text-sm">Find a tarball you like and click Add to cart.</p>
          <Link href="/browse" className="btn-primary">Browse the catalog</Link>
        </div>
      ) : (
        <div className="grid md:grid-cols-[1fr_320px] gap-6 items-start">
          {/* Items */}
          <div className="card overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-oyster-950/40 text-xs uppercase tracking-wider text-oyster-400">
                <tr>
                  <th className="text-left px-4 py-3 font-semibold">Tarball</th>
                  <th className="text-left px-4 py-3 font-semibold">Size</th>
                  <th className="text-right px-4 py-3 font-semibold">Price</th>
                  <th className="text-right px-4 py-3 font-semibold w-20"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-oyster-800/40">
                {items.map((t) => (
                  <tr key={t.id} className="hover:bg-oyster-800/30">
                    <td className="px-4 py-3">
                      <Link
                        href={`/tarball/${t.id}`}
                        className="font-medium text-oyster-100 hover:text-amber-accent"
                      >
                        {t.title}
                      </Link>
                      <div className="mt-0.5 flex items-center gap-2">
                        <span className={`tag tag-task-${t.mc_task_type}`}>
                          {t.mc_task_type}
                        </span>
                        <span className="text-xs text-oyster-400">
                          D5 {(t.d5_score ?? 0).toFixed(2)}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-oyster-200">{formatBytes(t.size_bytes)}</td>
                    <td className="px-4 py-3 text-right font-semibold text-oyster-100">
                      {formatCents(t.price_cents)}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <form action="/api/cart/remove" method="POST">
                        <input type="hidden" name="tarball_id" value={t.id} />
                        <button
                          type="submit"
                          className="text-xs text-oyster-300 hover:text-red-300 transition"
                          aria-label={`Remove ${t.title}`}
                        >
                          Remove
                        </button>
                      </form>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Summary */}
          <aside className="card p-5 space-y-4 sticky top-20">
            <h2 className="font-semibold text-oyster-50">Summary</h2>
            <dl className="text-sm text-oyster-300 space-y-1">
              <div className="flex justify-between">
                <dt>Subtotal</dt>
                <dd className="text-oyster-100 font-semibold">{formatCents(subtotalCents)}</dd>
              </div>
              <div className="flex justify-between text-xs text-oyster-400">
                <dt>Pricing</dt>
                <dd>{formatCents(env.pricePerGbCents)}/GB · {env.researchDiscountPct}% off research</dd>
              </div>
            </dl>

            <hr className="border-oyster-800/50" />

            <CartCheckoutButton
              items={items.map((i) => ({ id: i.id, title: i.title, price_cents: i.price_cents }))}
              signedIn={signedIn}
            />

            <p className="text-xs text-oyster-400">
              You&apos;ll pick research vs. commercial license on the next page. Stripe Checkout
              is the payment processor; we never see your card details.
            </p>
          </aside>
        </div>
      )}
    </div>
  );
}
