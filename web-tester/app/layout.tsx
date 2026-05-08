import type { Metadata } from 'next';
import './globals.css';
import { SiteHeader } from '../components/SiteHeader';

export const metadata: Metadata = {
  title: 'Oyster GameData — Earn by recording Minecraft',
  description:
    'Get paid to record your Minecraft gameplay. Real data for AI world-model training. Built by Oyster Labs.',
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? 'http://localhost:3000'),
  openGraph: {
    title: 'Oyster GameData — Earn by recording Minecraft',
    description:
      'Get paid to record your Minecraft gameplay. Real data for AI world-model training.',
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
            <span>© {new Date().getFullYear()} Oyster Labs · GameData program</span>
            <div className="flex gap-4">
              <a href="/docs" className="hover:text-oyster-100">Docs</a>
              <a href="mailto:gamedata@oyster.example" className="hover:text-oyster-100">Contact</a>
            </div>
          </div>
        </footer>
      </body>
    </html>
  );
}
