import type { Metadata } from 'next';
import './globals.css';
import { SiteHeader } from '../components/SiteHeader';

export const metadata: Metadata = {
  title: 'Oyster GameData — License real Minecraft gameplay for AI training',
  description:
    'Browse and license real Minecraft gameplay tarballs for world-model and policy training. Video + depth + actions, license-clean, paid per GB.',
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? 'http://localhost:3001'),
  openGraph: {
    title: 'Oyster GameData — License real Minecraft gameplay for AI training',
    description:
      'Browse and license real Minecraft gameplay tarballs for world-model and policy training.',
    type: 'website',
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen flex flex-col">
        <SiteHeader />
        <main className="flex-1">{children}</main>
        <footer className="border-t border-oyster-800/60 py-6 text-center text-sm text-oyster-400">
          <div className="max-w-6xl mx-auto px-4 flex flex-col sm:flex-row items-center justify-between gap-2">
            <span>© {new Date().getFullYear()} Oyster Labs · GameData marketplace</span>
            <div className="flex gap-4">
              <a href="/licenses" className="hover:text-oyster-100">Licenses</a>
              <a href="/browse" className="hover:text-oyster-100">Browse</a>
              <a href="mailto:gamedata@oyster.example" className="hover:text-oyster-100">Contact</a>
            </div>
          </div>
        </footer>
      </body>
    </html>
  );
}
