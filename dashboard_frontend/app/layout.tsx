import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "GameData Dashboard",
  description: "Track your game session income and payouts",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-gray-50 text-gray-900 antialiased">
        <header className="border-b border-gray-200 bg-white">
          <nav className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3 sm:px-6">
            <a href="/" className="text-lg font-bold tracking-tight text-indigo-600">
              GameData
            </a>
            <div className="flex gap-4 text-sm">
              <a href="/income" className="text-gray-600 hover:text-indigo-600 transition">
                Income
              </a>
            </div>
          </nav>
        </header>
        <main className="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-10">
          {children}
        </main>
      </body>
    </html>
  );
}
