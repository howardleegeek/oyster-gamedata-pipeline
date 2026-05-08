import Link from 'next/link';
import { readCartCookie } from '../lib/cart-cookie';

export function SiteHeader() {
  // Read cart cookie server-side so the navbar count never flickers.
  const cartCount = readCartCookie().length;

  return (
    <header className="border-b border-oyster-800/60 bg-oyster-950/70 backdrop-blur sticky top-0 z-40">
      <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-2 group">
          <div className="w-8 h-8 rounded-lg bg-amber-accent flex items-center justify-center font-black text-oyster-950">
            O
          </div>
          <span className="font-semibold tracking-tight group-hover:text-amber-accent transition">
            Oyster GameData{' '}
            <span className="text-oyster-400 text-xs font-medium">/ buyers</span>
          </span>
        </Link>
        <nav className="hidden md:flex items-center gap-1 text-sm">
          <Link href="/browse"    className="btn-ghost">Browse</Link>
          <Link href="/downloads" className="btn-ghost">Downloads</Link>
          <Link href="/licenses"  className="btn-ghost">Licenses</Link>
          <Link href="/cart"      className="btn-ghost relative">
            Cart
            {cartCount > 0 && (
              <span className="ml-1 inline-flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded-full bg-amber-accent text-oyster-950 text-xs font-bold">
                {cartCount}
              </span>
            )}
          </Link>
          <Link href="/login"     className="btn-secondary ml-2">Sign in</Link>
        </nav>
        <div className="md:hidden flex gap-2">
          <Link href="/cart" className="btn-ghost relative">
            Cart
            {cartCount > 0 && (
              <span className="ml-1 inline-flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded-full bg-amber-accent text-oyster-950 text-xs font-bold">
                {cartCount}
              </span>
            )}
          </Link>
          <Link href="/login" className="btn-secondary">Sign in</Link>
        </div>
      </div>
    </header>
  );
}
