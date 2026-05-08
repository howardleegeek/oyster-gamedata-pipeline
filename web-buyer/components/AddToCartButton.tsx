'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';

interface AddToCartButtonProps {
  tarballId: string;
  initiallyInCart?: boolean;
}

/**
 * Posts to /api/cart/add and refreshes the route so the navbar count + page
 * state both update. Optimistic — flips state immediately and only reverts
 * if the server rejects.
 */
export function AddToCartButton({ tarballId, initiallyInCart = false }: AddToCartButtonProps) {
  const [inCart, setInCart] = useState(initiallyInCart);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const router = useRouter();

  async function toggle() {
    const wasInCart = inCart;
    setInCart(!wasInCart);
    setError(null);
    try {
      const res = await fetch(wasInCart ? '/api/cart/remove' : '/api/cart/add', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ tarball_id: tarballId }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error ?? `HTTP ${res.status}`);
      }
      startTransition(() => router.refresh());
    } catch (err: any) {
      setInCart(wasInCart);
      setError(err.message ?? 'Cart update failed');
    }
  }

  return (
    <div>
      <button
        type="button"
        onClick={toggle}
        disabled={pending}
        className={inCart ? 'btn-secondary w-full' : 'btn-primary w-full'}
      >
        {pending ? 'Saving…' : inCart ? 'Remove from cart' : 'Add to cart'}
      </button>
      {error && (
        <p className="mt-2 text-xs text-red-300">{error}</p>
      )}
    </div>
  );
}
