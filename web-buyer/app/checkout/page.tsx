import Link from 'next/link';
import { fetchCatalog, CatalogNotConfiguredError } from '../../lib/catalog';
import { readCartCookie } from '../../lib/cart-cookie';
import { getSupabaseServerClient, getSupabaseServiceClient } from '../../lib/supabase-server';
import { isSupabaseConfigured, env, isStripeConfigured } from '../../lib/env';
import { formatBytes, formatCents, totalCents } from '../../lib/format';
import { NotConfigured } from '../../components/NotConfigured';
import type { CatalogTarball } from '../../types/database';
import { CartCheckoutButton } from '../cart/CheckoutButton';

export const dynamic = 'force-dynamic';

async function loadCheckout(): Promise<{
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

export default async function CheckoutPage() {
  // Howard 2026-05-07 IRON-LAW: hard-gate. Both Supabase (cart, buyer)
  // and Stripe (checkout session) are required to render this page.
  if (!isSupabaseConfigured()) {
    return (
      <NotConfigured
        service="Supabase"
        envVars={['NEXT_PUBLIC_SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_ANON_KEY', 'SUPABASE_SERVICE_ROLE_KEY']}
        docsUrl="/docs#supabase-setup"
      />
    );
  }
  if (!isStripeConfigured()) {
    return (
      <NotConfigured
        service="Stripe Checkout"
        envVars={['STRIPE_SECRET_KEY', 'STRIPE_PUBLISHABLE_KEY']}
        docsUrl="/docs#stripe-setup"
      />
    );
  }

  let checkoutState: Awaited<ReturnType<typeof loadCheckout>>;
  try {
    checkoutState = await loadCheckout();
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
  const { items, signedIn } = checkoutState;

  const subtotal = totalCents(items.map((i) => i.size_bytes), env.pricePerGbCents, 0);
  const research = totalCents(items.map((i) => i.size_bytes), env.pricePerGbCents, env.researchDiscountPct);

  return (
    <div className="max-w-3xl mx-auto px-4 py-10">
      <div className="flex items-end justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold">Checkout</h1>
          <p className="text-sm text-oyster-300 mt-1">
            Review your order, pick a license, pay through Stripe.
          </p>
        </div>
        <Link href="/cart" className="btn-ghost">← Back to cart</Link>
      </div>

      {items.length === 0 ? (
        <div className="card p-10 text-center text-oyster-300">
          <h3 className="text-lg font-semibold text-oyster-100 mb-2">Nothing to check out.</h3>
          <p className="mb-4 text-sm">Add at least one tarball before continuing.</p>
          <Link href="/browse" className="btn-primary">Browse the catalog</Link>
        </div>
      ) : (
        <>
          {/* Items */}
          <div className="card overflow-hidden mb-5">
            <table className="w-full text-sm">
              <thead className="bg-oyster-950/40 text-xs uppercase tracking-wider text-oyster-400">
                <tr>
                  <th className="text-left  px-4 py-3 font-semibold">Tarball</th>
                  <th className="text-left  px-4 py-3 font-semibold">Size</th>
                  <th className="text-right px-4 py-3 font-semibold">Price</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-oyster-800/40">
                {items.map((t) => (
                  <tr key={t.id}>
                    <td className="px-4 py-3 font-medium text-oyster-100">{t.title}</td>
                    <td className="px-4 py-3 text-oyster-300">{formatBytes(t.size_bytes)}</td>
                    <td className="px-4 py-3 text-right font-semibold">
                      {formatCents(t.price_cents)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="bg-oyster-950/40 text-sm">
                <tr>
                  <td className="px-4 py-3 font-semibold" colSpan={2}>
                    Commercial subtotal
                  </td>
                  <td className="px-4 py-3 text-right text-amber-accent font-bold">
                    {formatCents(subtotal)}
                  </td>
                </tr>
                <tr>
                  <td className="px-4 py-3 font-semibold text-oyster-300" colSpan={2}>
                    Research price (−{env.researchDiscountPct}%)
                  </td>
                  <td className="px-4 py-3 text-right text-oyster-200">
                    {formatCents(research)}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>

          <div className="card p-5">
            <CartCheckoutButton
              items={items.map((i) => ({
                id: i.id,
                title: i.title,
                price_cents: i.price_cents,
              }))}
              signedIn={signedIn}
            />
          </div>

          <p className="mt-5 text-xs text-oyster-400">
            By clicking Proceed to checkout you agree to the{' '}
            <Link href="/licenses" className="text-amber-accent hover:underline">
              license terms
            </Link>
            . Stripe Checkout handles payment; your card never touches our servers.
          </p>
        </>
      )}
    </div>
  );
}
