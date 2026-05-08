import Link from 'next/link';

export function SiteHeader() {
  return (
    <header className="border-b border-oyster-800/60 bg-oyster-950/70 backdrop-blur sticky top-0 z-40">
      <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-2 group">
          <div className="w-8 h-8 rounded-lg bg-amber-accent flex items-center justify-center font-black text-oyster-950">
            O
          </div>
          <span className="font-semibold tracking-tight group-hover:text-amber-accent transition">
            Oyster GameData
          </span>
        </Link>
        <nav className="hidden md:flex items-center gap-1 text-sm">
          <Link href="/dashboard" className="btn-ghost">Dashboard</Link>
          <Link href="/download"  className="btn-ghost">Download</Link>
          <Link href="/payouts"   className="btn-ghost">Payouts</Link>
          <Link href="/docs"      className="btn-ghost">Docs</Link>
          <Link href="/login"     className="btn-secondary ml-2">Sign in</Link>
        </nav>
        <Link href="/login" className="md:hidden btn-secondary">Sign in</Link>
      </div>
    </header>
  );
}
