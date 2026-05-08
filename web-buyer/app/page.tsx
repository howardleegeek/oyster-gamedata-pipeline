import Link from 'next/link';
import { env, isSupabaseConfigured } from '../lib/env';
import { fetchCatalog, CatalogNotConfiguredError } from '../lib/catalog';
import { TarballCard } from '../components/TarballCard';
import { NotConfigured } from '../components/NotConfigured';
import { formatCents } from '../lib/format';

export const dynamic = 'force-dynamic';

export default async function LandingPage() {
  const pricePerGb = formatCents(env.pricePerGbCents);

  // Howard 2026-05-07 IRON-LAW: marketing copy is real product copy, but
  // the "Featured tarballs" grid is real catalog data — so we skip the
  // grid when Supabase isn't configured, instead of fabricating tarballs.
  let rows: Awaited<ReturnType<typeof fetchCatalog>>['rows'] = [];
  let catalogError: { envVars: string[] } | null = null;
  try {
    if (isSupabaseConfigured()) {
      const result = await fetchCatalog({ limit: 3 });
      rows = result.rows;
    } else {
      catalogError = {
        envVars: ['NEXT_PUBLIC_SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_ANON_KEY', 'SUPABASE_SERVICE_ROLE_KEY'],
      };
    }
  } catch (err) {
    if (err instanceof CatalogNotConfiguredError) {
      catalogError = { envVars: err.envVars };
    } else {
      // Real DB error — surface it instead of masking with sample data.
      catalogError = { envVars: [] };
    }
  }

  return (
    <div className="hero-bg">
      {/* Hero */}
      <section className="max-w-6xl mx-auto px-4 py-20 md:py-28">
        <div className="max-w-3xl">
          <span className="inline-block tag bg-amber-accent/15 text-amber-accent mb-5">
            Now licensing
          </span>
          <h1 className="text-4xl md:text-6xl font-extrabold tracking-tight leading-tight">
            Real Minecraft gameplay data for AI training.
          </h1>
          <p className="mt-6 text-lg text-oyster-200 max-w-2xl">
            Pay <span className="text-amber-accent font-semibold">{pricePerGb} per GB</span>,
            license-clean, video plus depth plus actions. Tarballs are sourced from real
            human players paid by the hour, then quality-graded by our D5 model. No scraped
            footage, no synthetic data.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/browse" className="btn-primary">
              Browse catalog
            </Link>
            <Link href="/licenses" className="btn-secondary">
              See license terms
            </Link>
          </div>
        </div>

        {/* Stat strip */}
        <div className="mt-16 grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="card p-5">
            <div className="text-3xl font-bold text-amber-accent">{pricePerGb}</div>
            <div className="text-xs uppercase tracking-wider text-oyster-400 mt-1">
              per GB list
            </div>
          </div>
          <div className="card p-5">
            <div className="text-3xl font-bold">−{env.researchDiscountPct}%</div>
            <div className="text-xs uppercase tracking-wider text-oyster-400 mt-1">
              research discount
            </div>
          </div>
          <div className="card p-5">
            <div className="text-3xl font-bold">video + depth + actions</div>
            <div className="text-xs uppercase tracking-wider text-oyster-400 mt-1">
              modalities
            </div>
          </div>
          <div className="card p-5">
            <div className="text-3xl font-bold">24 h</div>
            <div className="text-xs uppercase tracking-wider text-oyster-400 mt-1">
              signed-URL TTL
            </div>
          </div>
        </div>
      </section>

      {/* Featured tarballs — skipped when catalog isn't reachable. */}
      {catalogError ? (
        <section className="max-w-6xl mx-auto px-4 py-12 md:py-16 border-t border-oyster-800/40">
          <h2 className="text-3xl font-bold mb-6">Featured tarballs</h2>
          <NotConfigured
            service="Supabase"
            envVars={catalogError.envVars.length > 0 ? catalogError.envVars : ['SUPABASE_*']}
          />
        </section>
      ) : rows.length > 0 ? (
        <section className="max-w-6xl mx-auto px-4 py-12 md:py-16 border-t border-oyster-800/40">
          <div className="flex items-end justify-between mb-6">
            <h2 className="text-3xl font-bold">Featured tarballs</h2>
            <Link href="/browse" className="btn-ghost">
              See all →
            </Link>
          </div>
          <div className="grid md:grid-cols-3 gap-5">
            {rows.map((t) => (
              <TarballCard key={t.id} t={t} />
            ))}
          </div>
        </section>
      ) : (
        // Howard 2026-05-08 IRON-LAW: when Supabase is configured but the catalog
        // is empty (fresh production DB before the first tester upload), show an
        // honest "live seeding" panel instead of silently dropping the section.
        // This is a real status, not fabricated content.
        <section className="max-w-6xl mx-auto px-4 py-12 md:py-16 border-t border-oyster-800/40">
          <div className="flex items-end justify-between mb-6">
            <h2 className="text-3xl font-bold">Featured tarballs</h2>
          </div>
          <div className="card p-8 text-center">
            <div className="inline-block tag bg-amber-accent/15 text-amber-accent mb-3">
              Catalog seeding live
            </div>
            <p className="text-oyster-200 max-w-xl mx-auto">
              The catalog populates in real time as paid testers upload tarballs. New
              uploads typically appear within a few minutes after a recording session
              ends. Check <Link href="/browse" className="text-amber-accent hover:underline">/browse</Link> for the live list.
            </p>
          </div>
        </section>
      )}

      {/* Why */}
      <section className="max-w-6xl mx-auto px-4 py-12 md:py-20 border-t border-oyster-800/40">
        <h2 className="text-3xl font-bold mb-10">Why Oyster GameData</h2>
        <div className="grid md:grid-cols-3 gap-6">
          {[
            {
              title: 'License-clean',
              body:
                'Every tarball is uploaded by a paid human tester who signed our perpetual non-exclusive training license. No scraped Twitch / YouTube footage. No DMCA risk.',
            },
            {
              title: 'Quality-graded',
              body:
                'Each tarball is reviewed by our in-house D5 model. We surface the score so you can filter by quality and only pay for footage that meets your bar.',
            },
            {
              title: 'Multi-modal',
              body:
                'Each tarball ships with synchronized video (60 FPS, H.265), depth maps, action / camera JSON, and structured world events. Drop-in for VLA fine-tunes.',
            },
          ].map((s) => (
            <div key={s.title} className="card p-6">
              <h3 className="text-xl font-semibold mb-2">{s.title}</h3>
              <p className="text-oyster-300 text-sm leading-relaxed">{s.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* FAQ */}
      <section className="max-w-4xl mx-auto px-4 py-12 md:py-20">
        <h2 className="text-3xl font-bold mb-8">FAQ</h2>
        <div className="space-y-4">
          {[
            {
              q: 'How is the data licensed?',
              a:
                'Each tarball is sold under one of two licenses — research (academic, non-commercial) or commercial. Both are perpetual, world-wide, royalty-free for AI model training. See /licenses for full terms.',
            },
            {
              q: 'How much data is in each tarball?',
              a:
                'Anywhere from 1 GB to 10 GB per tarball, covering 30 minutes to several hours of contiguous gameplay. Each filter on /browse lets you narrow to the size range you need.',
            },
            {
              q: 'How do downloads work?',
              a:
                'Once Stripe Checkout succeeds, /downloads shows signed S3-style URLs that expire after 24 hours. You can re-issue links from the same page indefinitely.',
            },
            {
              q: 'Can I see a sample first?',
              a:
                'Yes — every tarball detail page includes a 30-second video preview and the first 100 records of action_camera.json. No purchase required to inspect samples.',
            },
            {
              q: 'How do you handle privacy?',
              a:
                'Recordings only capture the Minecraft window — no other apps, no microphone, no webcam. Tester PII is stripped before tarballs ship. Read the full privacy section on /licenses.',
            },
          ].map((f) => (
            <details key={f.q} className="card p-5 group">
              <summary className="font-semibold cursor-pointer flex justify-between items-center list-none">
                {f.q}
                <span className="text-amber-accent transition-transform group-open:rotate-45">+</span>
              </summary>
              <p className="mt-3 text-oyster-300 text-sm leading-relaxed">{f.a}</p>
            </details>
          ))}
        </div>
        <div className="mt-12 text-center">
          <Link href="/browse" className="btn-primary">
            Browse the catalog
          </Link>
        </div>
      </section>
    </div>
  );
}
